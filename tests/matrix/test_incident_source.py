"""op.exp.9 solo se sustenta con el historial verificado de un incidente cerrado (ADR 0011 y
registro de incidentes). Datos sintéticos; el historial se verifica en vivo con los registros."""

from datetime import timedelta

import pytest
import yaml
from incidents.helpers import RECORDED as INCIDENT_RECORDED
from incidents.world import World
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import load_manifest_by_id
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.matrix import (
    CheckLink,
    DocumentLink,
    ManualEvidenceLink,
    MatrixRejected,
    add_link,
    build_export,
    init_matrix,
    render_markdown,
)
from ens_evidence.matrix.evidence import is_linkable
from ens_evidence.matrix.links import entry_of, load_matrix
from ens_evidence.matrix.messages import blocker_lines
from ens_evidence.matrix.policy import SOURCE_POLICIES, admits, source_policy
from ens_evidence.matrix.status import row_status
from ens_evidence.models import BlockerKind, Readiness
from ens_evidence.models.contracts import ProvenanceKind

from .evaluate_helpers import (
    FAR,
    NOW,
    FakeDocuments,
    assess_now,
    configure,
    details,
    entry,
    record,
    row_of,
)
from .source_helpers import CHECK, manual_evidence

MEASURE = "op.exp.9"
CLAUSES = frozenset({"op.exp.9.1", "op.exp.9.2", "op.exp.9.3"})
POLICY = (BlockerKind.MISSING_EVIDENCE.value, "op_exp_9_requires_incident_history")
MISSING_EVIDENCE = BlockerKind.MISSING_EVIDENCE.value
EXPIRED_EVIDENCE = BlockerKind.EXPIRED_EVIDENCE.value
EXPIRED_REVIEW = BlockerKind.EXPIRED_REVIEW.value
MISSING_REVIEW = BlockerKind.MISSING_REVIEW.value
ID = "INC-2026-001"
# El cierre cae entre el registro de la revisión (NOW - 12 h) y NOW.
CLOSED_AFTER_REVIEW = 352
CLOSED_IN_THE_FUTURE = 24 * 17


@pytest.fixture
def world(tmp_path, signed) -> World:
    return World(tmp_path)


@pytest.fixture
def ens_cli(tmp_path, signed, monkeypatch):
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )

    def run(*args: str):
        return CliRunner().invoke(app, ["--settings", str(settings), *args])

    return run


def history_link(evidence_id: str, covers=("base",)) -> ManualEvidenceLink:
    return ManualEvidenceLink(evidence_id=evidence_id, covers=list(covers))


def closed_history(world: World, **closing) -> str:
    history_id = world.closed_incident(**closing).history_evidence_id
    assert history_id
    return history_id


def linked(store, evidence_id: str, **kwargs):
    matrix_entry = entry(MEASURE, history_link(evidence_id), **kwargs)
    configure(store, matrix_entry)
    return matrix_entry


def reviewed(store, world: World):
    """op.exp.9 con el historial de un incidente cerrado y una revisión favorable."""
    evidence_id = closed_history(world)
    matrix_entry = linked(store, evidence_id)
    record(store, matrix_entry, evidence_ids=[evidence_id])
    return matrix_entry, evidence_id


def op_exp_9(store):
    return row_of(assess_now(store), MEASURE)


def record_evidence_id(world: World) -> str:
    return next(iter(scan_incident(world.store, ID).evidence.values())).id


def edit_manifest(store, evidence_id: str, edit) -> None:
    data = yaml.safe_load(store.read_text(manifest_key(evidence_id)))
    edit(data)
    store.write_text(manifest_key(evidence_id), yaml.safe_dump(data, sort_keys=False))


# --- política de fuentes ---------------------------------------------------------------------


def test_la_politica_es_una_tabla_con_op_exp_9_y_solo_admite_el_historial():
    policy = source_policy(MEASURE)
    assert policy is SOURCE_POLICIES[MEASURE]
    assert admits(policy, "incident-history")
    assert not any(admits(policy, other) for other in ("manual", "incident-record", None))
    assert admits(source_policy("op.exp.1"), None)


# --- registro vacío --------------------------------------------------------------------------


def test_el_registro_vacio_deja_op_exp_9_sin_evidencia_y_bloqueada(signed):
    configure(signed, entry(MEASURE))

    result = assess_now(signed)
    row = row_of(result, MEASURE)

    assert {(MISSING_EVIDENCE, f"clause:{c}") for c in CLAUSES} <= details(row)
    assert row.covered == frozenset()
    assert not row_status(row).implemented
    assert result.readiness.readiness is Readiness.NOT_READY


def test_el_registro_vacio_dice_como_obtener_la_fuente_que_la_medida_admite(signed):
    configure(signed, entry(MEASURE))

    texts = [t for t in blocker_lines(op_exp_9(signed).blockers) if "solo se sustenta" in t]

    assert len(texts) == 1
    assert "incident new" in texts[0] and "incident close" in texts[0]
    assert "incident new --simulated" in texts[0]
    assert "no que haya incidentes reales" in texts[0]
    assert f"matrix link {MEASURE} --evidence EV-… --covers base" in texts[0]


# --- otras fuentes: rechazadas al enlazar y al evaluar ---------------------------------------


def other_link(kind: str, signed, world: World):
    """Un enlace de cada fuente que NO sustenta op.exp.9; el id del registro exige un cierre."""
    if kind == "manual":
        return ManualEvidenceLink(
            evidence_id=manual_evidence(signed, valid_until=FAR), covers=["base"]
        )
    if kind == "incident-record":
        closed_history(world)
        return history_link(record_evidence_id(world))
    if kind == "document":
        return DocumentLink(document="politica", covers=["base"])
    return CheckLink(check_id=CHECK, covers=["base"])


KINDS = ["manual", "incident-record", "document", "check"]


@pytest.mark.parametrize("kind", KINDS)
def test_otra_fuente_se_rechaza_al_enlazar_op_exp_9(signed, world, kind):
    init_matrix(signed, NOW)
    link = other_link(kind, signed, world)

    with pytest.raises(MatrixRejected) as rejected:
        add_link(signed, NOW, MEASURE, link, FakeDocuments())

    expected = (
        "es un registro del incidente; vincula su historial (EV del cierre)"
        if kind == "incident-record"
        else "op.exp.9 solo se sustenta con el historial de un incidente cerrado"
    )
    assert expected in " ".join(rejected.value.errors)
    assert entry_of(load_matrix(signed), MEASURE).links == []


@pytest.mark.parametrize("kind", KINDS)
def test_otra_fuente_escrita_a_mano_en_el_yaml_da_el_bloqueo_de_politica(signed, world, kind):
    link = other_link(kind, signed, world)
    matrix_entry = entry(MEASURE, link)
    configure(signed, matrix_entry)

    row = op_exp_9(signed)

    assert POLICY in details(row)
    assert row.covered == frozenset()
    assert not row_status(row).implemented
    assert not any(s.counts for s in row.supports)


def test_un_incident_record_nunca_se_vincula_ni_en_otra_medida(signed, world):
    init_matrix(signed, NOW)
    closed_history(world)

    with pytest.raises(MatrixRejected) as rejected:
        add_link(signed, NOW, "op.exp.1", history_link(record_evidence_id(world)), FakeDocuments())

    assert "es un registro del incidente; vincula su historial" in " ".join(rejected.value.errors)


def test_la_politica_no_afecta_a_las_demas_medidas(signed):
    init_matrix(signed, NOW)
    link = ManualEvidenceLink(evidence_id=manual_evidence(signed, valid_until=FAR), covers=["base"])

    outcome = add_link(signed, NOW, "op.exp.1", link, FakeDocuments())

    assert outcome.entry.links == [link]


# --- el historial válido sustenta, no implanta -----------------------------------------------


def test_un_historial_valido_se_puede_enlazar(signed, world):
    init_matrix(signed, NOW)
    evidence_id = closed_history(world)

    outcome = add_link(signed, NOW, MEASURE, history_link(evidence_id), FakeDocuments())

    assert outcome.entry.links == [history_link(evidence_id)]
    assert outcome.pending == ()


def test_un_historial_valido_sustenta_op_exp_9_pero_no_la_implanta_sin_revision(signed, world):
    linked(signed, closed_history(world))

    row = op_exp_9(signed)

    assert row.covered == CLAUSES
    assert [s.counts for s in row.supports] == [True]
    assert not any(kind == MISSING_EVIDENCE for kind, _ in details(row))
    assert (MISSING_REVIEW, "review_missing") in details(row)
    assert not row_status(row).implemented


def test_el_historial_lleva_las_fechas_del_cierre_y_del_alta(signed, world):
    evidence_id = closed_history(world)
    linked(signed, evidence_id)

    support = op_exp_9(signed).supports[0]

    manifest = load_manifest_by_id(signed, evidence_id)
    closed_at = INCIDENT_RECORDED + timedelta(hours=2)
    assert support.evidence[0].collected_at == closed_at == manifest.collected_at
    assert support.available_at == manifest.provenance.submitted_at


def test_con_revision_favorable_el_historial_si_implanta(signed, world):
    reviewed(signed, world)

    row = op_exp_9(signed)

    assert row.blockers == ()
    assert row_status(row).implemented


# --- bloquea ---------------------------------------------------------------------------------


def test_un_historial_roto_tras_una_revision_favorable_bloquea_en_status_export_y_salida(
    signed, world, ens_cli
):
    _, evidence_id = reviewed(signed, world)
    assert ens_cli("matrix", "status", MEASURE).exit_code == 0

    world.remove(scan_incident(signed, ID).records[-1].key)

    result = assess_now(signed)
    row = row_of(result, MEASURE)
    assert (MISSING_EVIDENCE, f"incident_history_broken:{evidence_id}") in details(row)
    assert not row_status(row).implemented
    exported = next(
        r for r in build_export(signed, result, generated_at=NOW).rows if r.measure_id == MEASURE
    )
    assert not exported.implemented
    assert any(b.detail == f"incident_history_broken:{evidence_id}" for b in exported.blockers)
    assert [link.counts for link in exported.manual] == [False]
    status = ens_cli("matrix", "status", MEASURE)
    assert status.exit_code == 1
    assert "ya no coincide con los registros del incidente" in status.output


def test_un_registro_alterado_rompe_el_historial(signed, world):
    _, evidence_id = reviewed(signed, world)
    key = scan_incident(signed, ID).records[0].key
    signed.write_text(key, signed.read_text(key).replace("Acceso", "Acceso editado"))

    row = op_exp_9(signed)

    assert (MISSING_EVIDENCE, f"incident_history_broken:{evidence_id}") in details(row)
    assert not row_status(row).implemented


def test_un_historial_cerrado_despues_de_la_revision_no_la_sustenta(signed, world):
    world.open()
    world.update(analysis={"summary": "Credenciales reutilizadas."})
    evidence_id = world.close(hours=CLOSED_AFTER_REVIEW).history_evidence_id
    matrix_entry = linked(signed, evidence_id)
    record(signed, matrix_entry, evidence_ids=[evidence_id])

    row = op_exp_9(signed)

    assert (MISSING_EVIDENCE, f"source_after_review:evidence:{evidence_id}") in details(row)
    assert not row_status(row).implemented


def test_un_historial_futuro_no_cuenta(signed, world):
    world.open()
    evidence_id = world.close(hours=CLOSED_IN_THE_FUTURE).history_evidence_id
    linked(signed, evidence_id)

    row = op_exp_9(signed)

    assert (MISSING_EVIDENCE, f"evidence_in_future:{evidence_id}") in details(row)
    assert row.covered == frozenset()


def test_un_historial_fuera_de_la_periodicidad_esta_caducado(signed, world):
    evidence_id = closed_history(world)
    linked(signed, evidence_id, every=5)

    row = op_exp_9(signed)

    assert (EXPIRED_EVIDENCE, f"evidence_expired:{evidence_id}") in details(row)
    assert row.covered == frozenset()


def test_sin_periodicidad_el_historial_no_tiene_validez(signed, world):
    evidence_id = closed_history(world)
    linked(signed, evidence_id, every=None)

    row = op_exp_9(signed)

    assert (MISSING_EVIDENCE, f"evidence_no_validity:{evidence_id}") in details(row)
    assert row.covered == frozenset()


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["provenance"].update(submitted_by="Otra persona"),
        lambda d: d.update(valid_until=(NOW + timedelta(days=700)).isoformat()),
    ],
    ids=["procedencia", "valid_until"],
)
def test_un_manifiesto_alterado_tras_la_revision_la_invalida(signed, world, edit):
    _, evidence_id = reviewed(signed, world)
    assert op_exp_9(signed).blockers == ()

    edit_manifest(signed, evidence_id, edit)
    row = op_exp_9(signed)

    assert not row_status(row).implemented
    assert row.blockers
    assert (EXPIRED_REVIEW, f"evidence_changed:{evidence_id}") in details(row) or any(
        d.startswith("incident_history_broken") for _, d in details(row)
    )


def test_un_historial_sustituido_por_el_de_otro_incidente_no_cuenta(signed, world):
    first = closed_history(world)
    world.incident_id = "INC-2026-002"
    world.open(id="INC-2026-002")
    world.close(hours=3)
    matrix_entry = linked(signed, first)
    manifest = load_manifest_by_id(signed, first)
    edit_manifest(signed, first, lambda d: d.update(asset_id="incident:INC-2026-002"))
    assert manifest.asset_id == "incident:INC-2026-001"

    row = op_exp_9(signed)

    assert row.covered == frozenset()
    assert matrix_entry.links


# --- simulacro -------------------------------------------------------------------------------


def simulated_history(world: World, monkeypatch) -> str:
    world.approve_procedure(monkeypatch)
    procedure = world.procedure().model_dump(mode="json")
    world.open(id="SIM-2026-001", simulated=True, procedure=procedure)
    world.update(analysis={"summary": "Ejercicio de contención."})
    history_id = world.close().history_evidence_id
    assert history_id
    return history_id


def test_un_simulacro_aparece_etiquetado_en_la_fila_y_en_el_export(signed, world, monkeypatch):
    evidence_id = simulated_history(world, monkeypatch)
    linked(signed, evidence_id)

    result = assess_now(signed)
    row = row_of(result, MEASURE)
    exported = next(
        r for r in build_export(signed, result, generated_at=NOW).rows if r.measure_id == MEASURE
    )

    assert row.supports[0].simulated
    assert row_status(row).sources == (f"evidencia {evidence_id} (simulacro)",)
    assert [link.simulated for link in exported.manual] == [True]
    markdown = render_markdown(build_export(signed, result, generated_at=NOW))
    assert f"{evidence_id} (simulacro)" in markdown


def test_un_incidente_real_no_se_etiqueta_como_simulacro(signed, world):
    evidence_id = closed_history(world)
    linked(signed, evidence_id)

    result = assess_now(signed)
    row = row_of(result, MEASURE)

    assert not row.supports[0].simulated
    assert row_status(row).sources == (f"evidencia {evidence_id}",)
    exported = build_export(signed, result, generated_at=NOW)
    assert "simulacro" not in render_markdown(exported)


def test_el_simulacro_se_senala_en_las_fuentes_del_export(signed, world, monkeypatch):
    evidence_id = simulated_history(world, monkeypatch)
    matrix_entry = linked(signed, evidence_id)
    record(signed, matrix_entry, evidence_ids=[evidence_id])

    result = assess_now(signed)
    lines = render_markdown(build_export(signed, result, generated_at=NOW)).splitlines()

    source = next(line for line in lines if line.startswith(f"- {evidence_id}"))
    assert source.startswith(f"- {evidence_id} (simulacro) ·")


def test_es_enlazable_solo_la_evidencia_manual_o_el_historial_de_procedencia_manual(signed, world):
    history = load_manifest_by_id(signed, closed_history(world))
    record_manifest = next(iter(scan_incident(signed, ID).evidence.values()))
    automatic = history.model_copy(
        update={
            "provenance": history.provenance.model_copy(update={"kind": ProvenanceKind.COLLECTOR})
        }
    )

    assert is_linkable(history)
    assert not is_linkable(record_manifest)
    assert not is_linkable(automatic)
