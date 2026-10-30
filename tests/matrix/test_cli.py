import hashlib
from datetime import timedelta

import pytest
import yaml
from orgdocs.orgdocs_helpers import SIGNATORY, fixture_answers, write_answers
from typer.testing import CliRunner

from ens_evidence.catalog.controls import CatalogVersion
from ens_evidence.cli import app
from ens_evidence.matrix import (
    MATRIX_KEY,
    ApplicabilityReview,
    MatrixEntry,
    MatrixFile,
    MeasureReviewRecord,
    plan_obligations,
    read_matrix,
    read_reviews,
    save_matrix,
)
from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import approve_document, build_documents

from .evaluate_helpers import NOW, ONE_CLAUSE, TWO_CLAUSES
from .source_helpers import CHECK, collected_evidence, manual_evidence

NOT_APPLICABLE = "op.cont.3"
SELF = ["--by", "Ana", "--role", "system_administrator"]
CHECKS_PENDING = "evidencia técnica: pendiente del motor de checks"


@pytest.fixture
def ens_cli(tmp_path, signed, monkeypatch):
    """CLI sobre `signed` (Declaración vigente) con el reloj en `NOW`."""
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    runner = CliRunner()

    def run(*args: str):
        return runner.invoke(app, ["--settings", str(settings), *args])

    return run


@pytest.fixture
def initialized(ens_cli):
    assert ens_cli("matrix", "init").exit_code == 0
    return ens_cli


def configured(ens_cli, measure=ONE_CLAUSE, every="30"):
    result = ens_cli(
        "matrix", "set", measure, "--owner", "Ana", "--owner-role", "system_administrator"
    )
    assert result.exit_code == 0, result.output
    assert ens_cli("matrix", "set", measure, "--every", every).exit_code == 0


def linked_evidence(ens_cli, store, measure=ONE_CLAUSE, covers="base"):
    evidence_id = manual_evidence(store, valid_until=NOW + timedelta(days=365))
    result = ens_cli("matrix", "link", measure, "--evidence", evidence_id, "--covers", covers)
    assert result.exit_code == 0, result.output
    return evidence_id


def review(ens_cli, measure=ONE_CLAUSE, *extra):
    args = ["matrix", "review", measure, *SELF, "--conclusion", "Revisada", *extra]
    return ens_cli(*args)


def line_of(output: str, measure: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(f"{measure}  "))


def entries(store) -> dict[str, MatrixEntry]:
    matrix, errors = read_matrix(store)
    assert matrix is not None and not errors
    return {e.id: e for e in matrix.entries}


# --- init -------------------------------------------------------------------------------------


def test_init_crea_una_entrada_por_cada_fila_de_la_declaracion(ens_cli, signed):
    result = ens_cli("matrix", "init")

    rows = {r.measure_id: r.kind.value for r in plan_obligations(signed, NOW).rows}
    assert result.exit_code == 0, result.output
    assert set(entries(signed)) == set(rows)
    assert {"selected", "not_selected"} <= set(rows.values())
    assert f"{len(rows)} entradas nuevas" in result.output


def test_init_es_idempotente_y_conserva_lo_configurado(ens_cli, signed):
    ens_cli("matrix", "init")
    configured(ens_cli)
    before = signed.read_bytes(MATRIX_KEY)

    again = ens_cli("matrix", "init")

    assert again.exit_code == 0
    assert "0 entradas nuevas" in again.output
    assert signed.read_bytes(MATRIX_KEY) == before
    assert entries(signed)[ONE_CLAUSE].review_every_days == 30


def test_init_avisa_de_las_entradas_que_sobran_sin_borrarlas(ens_cli, signed):
    ens_cli("matrix", "init")
    matrix, _ = read_matrix(signed)
    extra = [*matrix.entries, MatrixEntry(id="op.zzz.9")]
    save_matrix(signed, matrix.model_copy(update={"entries": extra}))

    result = ens_cli("matrix", "init")

    assert result.exit_code == 0
    assert "sobran entradas" in result.output and "op.zzz.9" in result.output
    assert "op.zzz.9" in entries(signed)


def test_init_avisa_si_la_version_del_catalogo_no_coincide(ens_cli, signed):
    ens_cli("matrix", "init")
    matrix, _ = read_matrix(signed)
    other = CatalogVersion(snapshot_sha256="f" * 64, consolidation_date="2020-01-01")
    save_matrix(signed, matrix.model_copy(update={"catalog_version": other}))

    result = ens_cli("matrix", "init")

    assert result.exit_code == 0
    assert "otra versión del catálogo" in result.output


def test_init_exige_la_declaracion_vigente(tmp_path, workspace, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)
    settings = tmp_path / "s.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )

    result = CliRunner().invoke(app, ["--settings", str(settings), "matrix", "init"])

    assert result.exit_code == 1
    assert "no está vigente" in result.output
    assert not workspace.exists(MATRIX_KEY)


def test_un_workspace_inexistente_sale_con_2(tmp_path):
    settings = tmp_path / "s.yaml"
    settings.write_text(yaml.safe_dump({"workspace": {"backend": "local"}}))

    result = CliRunner().invoke(app, ["--settings", str(settings), "matrix", "init"])

    assert result.exit_code == 2


# --- set --------------------------------------------------------------------------------------


def test_set_fija_responsable_y_periodicidad(initialized, signed):
    configured(initialized, every="90")

    entry = entries(signed)[ONE_CLAUSE]
    assert entry.owner is not None and entry.owner.name == "Ana"
    assert entry.owner.role is Role.SYSTEM_ADMINISTRATOR
    assert entry.review_every_days == 90


def test_set_acepta_365_y_rechaza_mas(initialized, signed):
    assert initialized("matrix", "set", ONE_CLAUSE, "--every", "365").exit_code == 0

    refused = initialized("matrix", "set", ONE_CLAUSE, "--every", "366")

    assert refused.exit_code == 1
    assert "de 1 a 365" in refused.output
    assert entries(signed)[ONE_CLAUSE].review_every_days == 365


@pytest.mark.parametrize(
    "args",
    [
        ("--owner", "Ana"),
        ("--owner-role", "system_administrator"),
        ("--owner", "Ana", "--owner-role", "capitan"),
        ("--every", "0"),
        (),
    ],
)
def test_set_rechaza_combinaciones_incompletas_o_invalidas(initialized, args):
    result = initialized("matrix", "set", ONE_CLAUSE, *args)

    assert result.exit_code == 1


def test_set_rechaza_una_medida_sin_entrada_y_la_matriz_sin_iniciar(ens_cli, initialized):
    unknown = initialized("matrix", "set", "op.zzz.9", "--every", "30")

    assert unknown.exit_code == 1


def test_set_sin_matrix_init_pide_iniciarla(ens_cli):
    result = ens_cli("matrix", "set", ONE_CLAUSE, "--every", "30")

    assert result.exit_code == 1
    assert "matrix init" in result.output


# --- link / unlink ----------------------------------------------------------------------------


def test_link_de_una_evidencia_manual_muestra_la_cobertura(initialized, signed):
    evidence_id = manual_evidence(signed, valid_until=NOW + timedelta(days=30))

    result = initialized(
        "matrix", "link", TWO_CLAUSES, "--evidence", evidence_id, "--covers", "op.ext.4.1"
    )

    assert result.exit_code == 0, result.output
    assert "Cubre: op.ext.4.1." in result.output
    assert "Sin cubrir por ningún enlace: op.ext.4.2." in result.output
    (link,) = entries(signed)[TWO_CLAUSES].links
    assert link.covers == ["op.ext.4.1"]


def test_link_con_un_atajo_cubre_todas_las_clausulas(initialized, signed):
    evidence_id = manual_evidence(signed)

    result = initialized(
        "matrix", "link", TWO_CLAUSES, "--evidence", evidence_id, "--covers", "base"
    )

    assert "Cubre: op.ext.4.1, op.ext.4.2." in result.output
    assert "Todas las cláusulas tienen algún enlace" in result.output


def test_link_une_los_covers_de_una_fuente_ya_vinculada(initialized, signed):
    evidence_id = linked_evidence(initialized, signed, TWO_CLAUSES, "op.ext.4.1")

    initialized("matrix", "link", TWO_CLAUSES, "--evidence", evidence_id, "--covers", "op.ext.4.2")

    (link,) = entries(signed)[TWO_CLAUSES].links
    assert link.covers == ["op.ext.4.1", "op.ext.4.2"]


def test_link_de_un_check_conocido_y_de_un_documento_real(initialized, signed):
    check = initialized("matrix", "link", ONE_CLAUSE, "--check", CHECK, "--covers", "base")
    document = initialized(
        "matrix", "link", ONE_CLAUSE, "--document", "politica-seguridad", "--covers", "base"
    )

    assert check.exit_code == 0, check.output
    assert document.exit_code == 0, document.output
    assert {link.kind for link in entries(signed)[ONE_CLAUSE].links} == {"check", "org_document"}


@pytest.mark.parametrize(
    ("source", "covers", "message"),
    [
        (("--check", "no.existe"), "base", "Check desconocido"),
        (("--document", "inventado"), "base", "Documento desconocido"),
        (("--evidence", "EV-20990101-deadbeef"), "base", "no existe"),
        (("--evidence", "no-es-un-id"), "base", "id de evidencia no válido"),
        (("--check", CHECK), "op.exp.9.9", "no es una obligación ni una cláusula"),
        (("--check", CHECK), "R7", "no es una obligación ni una cláusula"),
        (("--check", CHECK), "base,,R1", "sin elementos vacíos"),
    ],
)
def test_link_rechaza_fuentes_y_coberturas_invalidas(initialized, signed, source, covers, message):
    result = initialized("matrix", "link", ONE_CLAUSE, *source, "--covers", covers)

    assert result.exit_code == 1
    assert message in result.output
    assert entries(signed)[ONE_CLAUSE].links == []


def test_link_rechaza_una_evidencia_de_collector(initialized, signed):
    evidence_id = collected_evidence(signed)

    result = initialized(
        "matrix", "link", ONE_CLAUSE, "--evidence", evidence_id, "--covers", "base"
    )

    assert result.exit_code == 1
    assert "no es una evidencia manual" in result.output


def test_link_a_una_medida_no_aplicable_se_rechaza(initialized):
    result = initialized("matrix", "link", NOT_APPLICABLE, "--check", CHECK, "--covers", "base")

    assert result.exit_code == 1
    assert "sin obligaciones" in result.output


def test_link_exige_covers_y_exactamente_una_fuente(initialized):
    no_covers = initialized("matrix", "link", ONE_CLAUSE, "--check", CHECK)
    two_sources = initialized(
        "matrix", "link", ONE_CLAUSE, "--check", CHECK, "--document", "x", "--covers", "base"
    )
    no_source = initialized("matrix", "link", ONE_CLAUSE, "--covers", "base")

    assert no_covers.exit_code == 2
    assert two_sources.exit_code == 2
    assert no_source.exit_code == 2


def test_unlink_quita_la_fuente(initialized, signed):
    initialized("matrix", "link", ONE_CLAUSE, "--check", CHECK, "--covers", "base")

    result = initialized("matrix", "unlink", ONE_CLAUSE, "--check", CHECK)

    assert result.exit_code == 0, result.output
    assert entries(signed)[ONE_CLAUSE].links == []
    assert "Sin cubrir por ningún enlace: op.exp.1.1." in result.output


def test_unlink_con_covers_quita_solo_esas_coberturas(initialized, signed):
    initialized("matrix", "link", TWO_CLAUSES, "--check", CHECK, "--covers", "base")
    # `base` y una cláusula suelta: se vinculan por separado al mismo check.
    initialized("matrix", "unlink", TWO_CLAUSES, "--check", CHECK)
    initialized(
        "matrix", "link", TWO_CLAUSES, "--check", CHECK, "--covers", "op.ext.4.1,op.ext.4.2"
    )

    result = initialized(
        "matrix", "unlink", TWO_CLAUSES, "--check", CHECK, "--covers", "op.ext.4.2"
    )

    assert result.exit_code == 0, result.output
    (link,) = entries(signed)[TWO_CLAUSES].links
    assert link.covers == ["op.ext.4.1"]


def test_unlink_rechaza_lo_no_vinculado_y_covers_ajenos(initialized):
    initialized("matrix", "link", ONE_CLAUSE, "--check", CHECK, "--covers", "base")

    absent = initialized("matrix", "unlink", ONE_CLAUSE, "--document", "politica-seguridad")
    foreign = initialized("matrix", "unlink", ONE_CLAUSE, "--check", CHECK, "--covers", "R1")

    assert absent.exit_code == 1
    assert foreign.exit_code == 1
    assert "no cubre R1" in foreign.output


# --- review -----------------------------------------------------------------------------------


def test_review_compliant_registra_la_revision_con_fuentes_y_huellas(initialized, signed):
    configured(initialized)
    evidence_id = linked_evidence(initialized, signed)

    result = review(initialized, ONE_CLAUSE, "--assessment", "COMPLIANT")

    assert result.exit_code == 0, result.output
    assert f"op.exp.1.1  <- evidencia {evidence_id}" in result.output
    assert "El nombre es declarado; no se autentica" in result.output
    (stored,), errors = read_reviews(signed, ONE_CLAUSE)
    record = stored.record
    assert not errors and isinstance(record, MeasureReviewRecord)
    assert record.review.reviewer == "Ana"
    assert record.review.valid_until - record.review.reviewed_at == timedelta(days=30)
    assert record.recorded_at == NOW
    (obligation,) = record.obligations
    assert [e.evidence_id for e in obligation.evidence] == [evidence_id]
    assert obligation.clauses == ["op.exp.1.1"]


def test_review_compliant_sin_cobertura_se_rechaza_y_lista_lo_que_falta(initialized, signed):
    configured(initialized, TWO_CLAUSES)
    linked_evidence(initialized, signed, TWO_CLAUSES, "op.ext.4.1")

    result = review(initialized, TWO_CLAUSES, "--assessment", "COMPLIANT")

    assert result.exit_code == 1
    assert "cláusula sin cubrir: op.ext.4.2 (base)" in result.output
    assert "op.ext.4.1  <- evidencia EV-" in result.output
    assert read_reviews(signed, TWO_CLAUSES)[0] == []


def test_review_compliant_sin_responsable_ni_periodicidad_lista_ambos(initialized, signed):
    result = review(initialized, ONE_CLAUSE, "--assessment", "COMPLIANT")

    assert result.exit_code == 1
    assert "falta el responsable" in result.output
    assert "falta la periodicidad" in result.output
    assert "cláusula sin cubrir: op.exp.1.1" in result.output
    assert read_reviews(signed, ONE_CLAUSE)[0] == []


def test_review_no_conforme_se_registra_sin_cobertura(initialized, signed):
    initialized("matrix", "set", ONE_CLAUSE, "--every", "30")

    result = review(initialized, ONE_CLAUSE, "--assessment", "NON_COMPLIANT")

    assert result.exit_code == 0, result.output
    (stored,), _ = read_reviews(signed, ONE_CLAUSE)
    assert isinstance(stored.record, MeasureReviewRecord)
    assert stored.record.assessment.value == "NON_COMPLIANT"


def test_review_en_medida_aplicable_exige_assessment(initialized):
    initialized("matrix", "set", ONE_CLAUSE, "--every", "30")

    result = review(initialized, ONE_CLAUSE)

    assert result.exit_code == 1
    assert "--assessment es obligatorio" in result.output


@pytest.mark.parametrize("value", ["NOT_ASSESSED", "cumple", "compliant"])
def test_review_rechaza_valoraciones_no_admitidas(initialized, value):
    result = review(initialized, ONE_CLAUSE, "--assessment", value)

    assert result.exit_code == 1
    assert "no válido" in result.output


@pytest.mark.parametrize("role", ["security_officer", "management", "system_owner", "capitan"])
def test_review_rechaza_los_roles_no_permitidos(initialized, signed, role):
    configured(initialized)
    linked_evidence(initialized, signed)

    result = initialized(
        "matrix",
        *("review", ONE_CLAUSE, "--by", "Ana", "--role", role),
        *("--assessment", "COMPLIANT", "--conclusion", "ok"),
    )

    assert result.exit_code == 1
    assert read_reviews(signed, ONE_CLAUSE)[0] == []


def test_review_acepta_al_delegado_del_administrador(initialized, signed):
    initialized("matrix", "set", ONE_CLAUSE, "--every", "30")

    result = initialized(
        "matrix",
        *("review", ONE_CLAUSE, "--by", "Eva", "--role", "system_administrator_delegate"),
        *("--assessment", "REQUIRES_REVIEW", "--conclusion", "A revisar"),
    )

    assert result.exit_code == 0, result.output


def test_review_rechaza_un_at_futuro_y_acepta_uno_pasado(initialized, signed):
    initialized("matrix", "set", ONE_CLAUSE, "--every", "30")
    future = (NOW + timedelta(seconds=1)).isoformat()
    past = (NOW - timedelta(days=2)).isoformat()

    refused = review(initialized, ONE_CLAUSE, "--assessment", "NON_COMPLIANT", "--at", future)
    accepted = review(initialized, ONE_CLAUSE, "--assessment", "NON_COMPLIANT", "--at", past)

    assert refused.exit_code == 1
    assert "no puede ser futuro" in refused.output
    assert accepted.exit_code == 0, accepted.output
    (stored,), _ = read_reviews(signed, ONE_CLAUSE)
    assert stored.record.review.reviewed_at == NOW - timedelta(days=2)
    assert stored.record.recorded_at == NOW


def test_review_con_un_at_sin_zona_es_un_error_de_uso(initialized):
    result = review(initialized, ONE_CLAUSE, "--assessment", "NON_COMPLIANT", "--at", "2026-01-01")

    assert result.exit_code == 2


def test_review_de_una_no_aplicable_registra_la_decision_sin_assessment(initialized, signed):
    initialized("matrix", "set", NOT_APPLICABLE, "--every", "90")

    result = review(initialized, NOT_APPLICABLE)

    assert result.exit_code == 0, result.output
    (stored,), _ = read_reviews(signed, NOT_APPLICABLE)
    assert isinstance(stored.record, ApplicabilityReview)


def test_review_de_una_no_aplicable_prohibe_assessment(initialized, signed):
    initialized("matrix", "set", NOT_APPLICABLE, "--every", "90")

    result = review(initialized, NOT_APPLICABLE, "--assessment", "COMPLIANT")

    assert result.exit_code == 1
    assert "--assessment está prohibido" in result.output
    assert read_reviews(signed, NOT_APPLICABLE)[0] == []


def test_review_de_una_medida_ajena_a_la_declaracion_se_rechaza(initialized):
    result = review(initialized, "op.zzz.9", "--assessment", "COMPLIANT")

    assert result.exit_code == 1


def test_review_con_la_declaracion_obsoleta_se_rechaza(initialized, signed):
    configured(initialized)
    linked_evidence(initialized, signed)
    text = signed.read_text("controls/measures.yaml")
    signed.write_text("controls/measures.yaml", text + "\n# cambio posterior a la firma\n")

    result = review(initialized, ONE_CLAUSE, "--assessment", "COMPLIANT")

    assert result.exit_code == 1
    assert "no está vigente" in result.output


# --- status -----------------------------------------------------------------------------------


def test_status_sin_revisiones_sale_con_1_y_lista_los_bloqueos(initialized):
    result = initialized("matrix", "status")

    assert result.exit_code == 1
    assert CHECKS_PENDING in result.output
    assert "implantada: no" in line_of(result.output, ONE_CLAUSE)
    assert "bloqueo: MISSING_REVIEW review_missing" in result.output
    assert "Preparación: NOT_READY" in result.output
    assert "no declara conformidad" in result.output


def test_status_de_una_medida_revisada_la_da_por_implantada_y_revisada(initialized, signed):
    configured(initialized)
    evidence_id = linked_evidence(initialized, signed)
    review(initialized, ONE_CLAUSE, "--assessment", "COMPLIANT")

    result = initialized("matrix", "status")

    line = line_of(result.output, ONE_CLAUSE)
    assert "implantada: sí" in line and "revisión regular: sí" in line
    assert f"evidencia {evidence_id}" in line
    assert result.exit_code == 1  # el resto de la matriz sigue bloqueado


def test_status_con_at_anterior_a_la_revision_no_la_ve(initialized, signed):
    configured(initialized)
    linked_evidence(initialized, signed)
    review(initialized, ONE_CLAUSE, "--assessment", "COMPLIANT")

    before = initialized("matrix", "status", "--at", (NOW - timedelta(hours=1)).isoformat())
    after = initialized("matrix", "status", "--at", (NOW + timedelta(days=60)).isoformat())

    assert "implantada: no" in line_of(before.output, ONE_CLAUSE)
    assert "revisión regular: no" in line_of(after.output, ONE_CLAUSE)


def test_status_indica_que_los_checks_estan_pendientes_en_la_medida_con_check(initialized):
    initialized("matrix", "link", ONE_CLAUSE, "--check", CHECK, "--covers", "base")

    result = initialized("matrix", "status")

    assert f"    {CHECKS_PENDING}" in result.output
    assert "check_no_results" in result.output


def test_status_sin_matriz_ni_declaracion_vigente_sale_con_1(ens_cli, signed):
    signed.write_text("controls/measures.yaml", signed.read_text("controls/measures.yaml") + "#\n")

    result = ens_cli("matrix", "status")

    assert result.exit_code == 1
    assert "UNDETERMINED_APPLICABILITY" in result.output


# --- la Declaración no se toca ----------------------------------------------------------------


def test_operar_la_matriz_deja_la_declaracion_vigente_y_measures_yaml_intacto(initialized, signed):
    before = hashlib.sha256(signed.read_bytes("controls/measures.yaml")).hexdigest()

    configured(initialized)
    linked_evidence(initialized, signed)
    review(initialized, ONE_CLAUSE, "--assessment", "COMPLIANT")
    initialized("matrix", "status")
    initialized("matrix", "unlink", ONE_CLAUSE, "--check", CHECK)

    after = hashlib.sha256(signed.read_bytes("controls/measures.yaml")).hexdigest()
    status = initialized("applicability", "status")
    assert after == before
    assert status.exit_code == 0 and "CURRENT" in status.output


# --- escenario 2 de punta a punta -------------------------------------------------------------


def approve_policy(store):
    write_answers(store, fixture_answers())
    build_documents(store)
    role = Role.MANAGEMENT
    approve_document(store, "politica-seguridad", SIGNATORY[role], role, NOW - timedelta(days=1))


def test_documento_real_aprobado_mas_enlace_y_revision_da_la_medida_implantada(initialized, signed):
    approve_policy(signed)
    configured(initialized, "org.1", every="365")
    link = initialized(
        "matrix", "link", "org.1", "--document", "politica-seguridad", "--covers", "base"
    )
    assert link.exit_code == 0, link.output

    reviewed = review(initialized, "org.1", "--assessment", "COMPLIANT")
    status = initialized("matrix", "status")

    assert reviewed.exit_code == 0, reviewed.output
    assert "org.1.5  <- documento politica-seguridad" in reviewed.output
    line = line_of(status.output, "org.1")
    assert "implantada: sí" in line and "revisión regular: sí" in line
    assert "documento politica-seguridad" in line
    (stored,), _ = read_reviews(signed, "org.1")
    (obligation,) = stored.record.obligations
    assert [d.name for d in obligation.documents] == ["politica-seguridad"]


def test_documento_aprobado_sin_revision_sigue_bloqueado(initialized, signed):
    approve_policy(signed)
    configured(initialized, "org.1", every="365")
    initialized("matrix", "link", "org.1", "--document", "politica-seguridad", "--covers", "base")

    status = initialized("matrix", "status")

    line = line_of(status.output, "org.1")
    assert "implantada: no" in line and "revisión regular: no" in line
    assert "bloqueo: MISSING_REVIEW review_missing" in status.output


def test_documento_sin_aprobar_no_permite_registrar_compliant(initialized, signed):
    write_answers(signed, fixture_answers())
    build_documents(signed)
    configured(initialized, "org.1", every="365")
    initialized("matrix", "link", "org.1", "--document", "politica-seguridad", "--covers", "base")

    result = review(initialized, "org.1", "--assessment", "COMPLIANT")

    assert result.exit_code == 1
    assert "cláusula sin cubrir: org.1.1" in result.output
    assert read_reviews(signed, "org.1")[0] == []


def test_matrix_yaml_tiene_el_formato_esperado(initialized, signed):
    loaded = MatrixFile.model_validate(yaml.safe_load(signed.read_text(MATRIX_KEY)))

    assert {e.id for e in loaded.entries} == {
        r.measure_id for r in plan_obligations(signed, NOW).rows
    }
