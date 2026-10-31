"""Ronda 3 de la revisión externa: lo que la clave de un historial de solo adición dice debe
coincidir con su contenido, y quitar y volver a poner un enlace no recupera una revisión."""

from datetime import timedelta

import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.matrix import (
    ManualEvidenceLink,
    MatrixChange,
    add_link,
    init_matrix,
    read_matrix,
    read_reviews,
    remove_link,
    save_matrix,
    scan_history,
    set_entry,
)
from ens_evidence.matrix.history import _dump, _key, history_prefix
from ens_evidence.matrix.status import row_status
from ens_evidence.models import BlockerKind, MeasureAssessment

from .evaluate_helpers import (
    FAR,
    NOT_SELECTED,
    NOW,
    ONE_CLAUSE,
    RECORDED,
    assess_now,
    configure,
    details,
    entry,
    record,
    row_of,
)
from .helpers import applicability_review
from .source_helpers import manual_evidence

MISSING_REVIEW = BlockerKind.MISSING_REVIEW.value
EXPIRED_REVIEW = BlockerKind.EXPIRED_REVIEW.value
EVIDENCE = "evidence"


def reviewed(store):
    """Medida con enlace a evidencia y revisión COMPLIANT a las 00:00 de `RECORDED`."""
    evidence_id = manual_evidence(store, valid_until=FAR)
    link = ManualEvidenceLink(evidence_id=evidence_id, covers=["base"])
    matrix_entry = entry(ONE_CLAUSE, link)
    configure(store, matrix_entry, at=RECORDED - timedelta(hours=2))
    record(store, matrix_entry, evidence_ids=[evidence_id])
    assert row_of(assess_now(store), ONE_CLAUSE).blockers == ()
    return evidence_id, link


def edit_yaml(store, key, edit):
    data = yaml.safe_load(store.read_text(key))
    edit(data)
    store.write_text(key, yaml.safe_dump(data, sort_keys=False))


def hours(n):
    return RECORDED + timedelta(hours=n)


# --- 1. el instante de la clave de una revisión ---------------------------------------------


def test_adelantar_a_manana_el_recorded_at_de_la_revision_desfavorable_no_recupera_la_favorable(
    signed,
):
    evidence_id, _ = reviewed(signed)
    record(
        signed,
        entry(ONE_CLAUSE, ManualEvidenceLink(evidence_id=evidence_id, covers=["base"])),
        evidence_ids=[evidence_id],
        recorded_at=hours(1),
        assessment=MeasureAssessment.NON_COMPLIANT,
    )
    unfavorable = read_reviews(signed, ONE_CLAUSE)[0][-1].key
    tomorrow = (NOW + timedelta(days=1)).isoformat()

    edit_yaml(signed, unfavorable, lambda d: d.update(recorded_at=tomorrow))
    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_REVIEW, f"review_unreadable:{unfavorable}") in details(row)
    assert any(unfavorable in error for error in result.errors)
    assert row.measure.review is None and not row_status(row).implemented


def test_atrasar_el_recorded_at_de_una_revision_tambien_es_una_discrepancia(signed):
    reviewed(signed)
    (stored,) = read_reviews(signed, ONE_CLAUSE)[0]

    edit_yaml(
        signed,
        stored.key,
        lambda d: d.update(recorded_at=(RECORDED - timedelta(days=3)).isoformat()),
    )

    row = row_of(assess_now(signed), ONE_CLAUSE)
    assert (MISSING_REVIEW, f"review_unreadable:{stored.key}") in details(row)


def test_la_revision_de_aplicabilidad_con_el_recorded_at_editado_es_ilegible(signed):
    from ens_evidence.matrix import add_review

    key = add_review(signed, applicability_review(NOT_SELECTED, RECORDED))
    edit_yaml(signed, key, lambda d: d.update(recorded_at=(NOW + timedelta(days=1)).isoformat()))

    row = row_of(assess_now(signed), NOT_SELECTED)

    assert (MISSING_REVIEW, f"review_unreadable:{key}") in details(row)


def test_status_sale_con_1_y_no_da_por_implantada_la_medida_con_el_recorded_at_adelantado(
    tmp_path, signed, monkeypatch
):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    runner = CliRunner()

    def cli(*args):
        return runner.invoke(app, ["--settings", str(settings), *args])

    def at(moment):
        monkeypatch.setattr("ens_evidence.cli.clock", lambda: moment)

    at(hours(-3))
    assert cli("matrix", "init").exit_code == 0
    owner = ["--owner", "Ana", "--owner-role", "system_administrator"]
    assert cli("matrix", "set", ONE_CLAUSE, *owner).exit_code == 0
    assert cli("matrix", "set", ONE_CLAUSE, "--every", "30").exit_code == 0
    evidence_id = manual_evidence(signed, valid_until=FAR)
    link = cli("matrix", "link", ONE_CLAUSE, "--evidence", evidence_id, "--covers", "base")
    assert link.exit_code == 0, link.output
    review = ["matrix", "review", ONE_CLAUSE, "--by", "Ana", "--role", "system_administrator"]
    at(hours(-2))
    assert cli(*review, "--assessment", "COMPLIANT", "--conclusion", "Revisada").exit_code == 0
    at(hours(-1))
    bad = cli(*review, "--assessment", "NON_COMPLIANT", "--conclusion", "Ya no cumple")
    assert bad.exit_code == 0, bad.output
    at(NOW)
    unfavorable = read_reviews(signed, ONE_CLAUSE)[0][-1].key
    edit_yaml(
        signed, unfavorable, lambda d: d.update(recorded_at=(NOW + timedelta(days=1)).isoformat())
    )

    result = cli("matrix", "status", ONE_CLAUSE)

    assert result.exit_code == 1
    assert "implantada: sí" not in result.output
    assert unfavorable in result.output


# --- 2. el instante y la huella del nombre de una firma archivada ------------------------------


def test_retrofechar_el_approved_at_de_una_firma_archivada_deja_el_historial_ilegible(signed):
    from ens_evidence.applicability import declaration_history

    reviewed(signed)
    (key,) = signed.list("controls/declarations")
    edit_yaml(
        signed, key, lambda d: d.update(approved_at=(RECORDED - timedelta(days=30)).isoformat())
    )

    archived, unreadable = declaration_history(signed)
    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert archived == [] and unreadable == [key]
    assert (MISSING_REVIEW, f"declaration_history_unreadable:{key}") in details(row)
    assert not row_status(row).implemented


def test_editar_el_contenido_de_una_firma_archivada_sin_tocar_la_fecha_tambien_la_invalida(signed):
    from ens_evidence.applicability import declaration_history

    (key,) = signed.list("controls/declarations")
    edit_yaml(signed, key, lambda d: d.update(approved_by="Otra persona"))

    assert declaration_history(signed)[1] == [key]


# --- 3. unlink + link del mismo enlace --------------------------------------------------------


def test_unlink_y_link_del_mismo_enlace_no_recuperan_la_revision_caducada(signed):
    evidence_id, link = reviewed(signed)

    remove_link(signed, hours(1), ONE_CLAUSE, EVIDENCE, evidence_id)
    add_link(signed, hours(2), ONE_CLAUSE, link)
    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert row.entry.links == [link]  # el estado final es el revisado
    assert (EXPIRED_REVIEW, "links_changed") in details(row)
    assert not row_status(row).implemented


def test_quitar_y_volver_a_poner_solo_la_cobertura_tampoco_recupera_la_revision(signed):
    evidence_id, link = reviewed(signed)

    remove_link(signed, hours(1), ONE_CLAUSE, EVIDENCE, evidence_id, ["base"])
    add_link(signed, hours(2), ONE_CLAUSE, link)

    assert (EXPIRED_REVIEW, "links_changed") in details(row_of(assess_now(signed), ONE_CLAUSE))


def test_volver_a_vincular_lo_ya_vinculado_no_caduca_la_revision(signed):
    _, link = reviewed(signed)

    add_link(signed, hours(1), ONE_CLAUSE, link)

    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()


def test_cambiar_responsable_o_periodicidad_no_caduca_la_revision(signed):
    reviewed(signed)

    set_entry(signed, hours(1), ONE_CLAUSE, every_days=60)

    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()
    assert len(scan_history(signed, ONE_CLAUSE).changes) == 2


def test_unlink_y_link_por_la_cli_no_recuperan_la_revision(tmp_path, signed, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    runner = CliRunner()

    def cli(moment, *args):
        monkeypatch.setattr("ens_evidence.cli.clock", lambda: moment)
        return runner.invoke(app, ["--settings", str(settings), *args])

    evidence_id = manual_evidence(signed, valid_until=FAR)
    owner = ["--owner", "Ana", "--owner-role", "system_administrator"]
    cli(hours(-5), "matrix", "init")
    cli(hours(-5), "matrix", "set", ONE_CLAUSE, *owner)
    cli(hours(-5), "matrix", "set", ONE_CLAUSE, "--every", "30")
    link = ["matrix", "link", ONE_CLAUSE, "--evidence", evidence_id, "--covers", "base"]
    assert cli(hours(-4), *link).exit_code == 0
    done = cli(
        hours(-3),
        *("matrix", "review", ONE_CLAUSE, "--by", "Ana", "--role", "system_administrator"),
        *("--assessment", "COMPLIANT", "--conclusion", "Revisada"),
    )
    assert done.exit_code == 0, done.output
    assert row_of(assess_now(signed), ONE_CLAUSE).blockers == ()

    unlink = ["matrix", "unlink", ONE_CLAUSE, "--evidence", evidence_id]
    assert cli(hours(-2), *unlink).exit_code == 0
    assert cli(hours(-1), *link).exit_code == 0

    assert (EXPIRED_REVIEW, "links_changed") in details(row_of(assess_now(signed), ONE_CLAUSE))


# --- matrix.yaml editado a mano ----------------------------------------------------------------


def hand_edit(store, edit):
    matrix, errors = read_matrix(store)
    assert not errors
    entries = [edit(e) if e.id == ONE_CLAUSE else e for e in matrix.entries]
    save_matrix(store, matrix.model_copy(update={"entries": entries}))


def test_una_entrada_editada_a_mano_bloquea_la_medida_con_un_error_visible(signed):
    reviewed(signed)

    hand_edit(signed, lambda e: e.model_copy(update={"links": []}))
    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_REVIEW, f"matrix_edited:{ONE_CLAUSE}") in details(row)
    assert any("no coincide con el último estado registrado" in e for e in result.errors)
    assert not row_status(row).implemented


def test_editar_a_mano_algo_que_no_son_los_enlaces_tambien_bloquea(signed):
    reviewed(signed)

    hand_edit(signed, lambda e: e.model_copy(update={"review_every_days": 365}))

    assert (MISSING_REVIEW, f"matrix_edited:{ONE_CLAUSE}") in details(
        row_of(assess_now(signed), ONE_CLAUSE)
    )


def test_la_edicion_a_mano_de_una_medida_no_bloquea_a_las_demas(signed):
    reviewed(signed)
    hand_edit(signed, lambda e: e.model_copy(update={"links": []}))

    other = row_of(assess_now(signed), "op.ext.4")

    assert not any(d.startswith("matrix_edited") for _, d in details(other))


def test_quitar_a_mano_el_enlace_y_volver_a_vincularlo_con_la_cli_no_recupera_la_revision(signed):
    _, link = reviewed(signed)
    hand_edit(signed, lambda e: e.model_copy(update={"links": []}))

    add_link(signed, hours(2), ONE_CLAUSE, link)
    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert not any(d.startswith("matrix_edited") for _, d in details(row))  # ya registrado
    assert (EXPIRED_REVIEW, "links_changed") in details(row)
    assert not row_status(row).implemented


def test_matrix_init_registra_lo_editado_a_mano_y_la_revision_sigue_caducada(signed):
    reviewed(signed)
    hand_edit(signed, lambda e: e.model_copy(update={"links": []}))

    init_matrix(signed, hours(1))
    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert not any(d.startswith("matrix_edited") for _, d in details(row))
    assert (EXPIRED_REVIEW, "links_changed") in details(row)


def test_borrar_a_mano_la_entrada_de_una_medida_con_historial_bloquea(signed):
    reviewed(signed)
    matrix, _ = read_matrix(signed)
    save_matrix(
        signed,
        matrix.model_copy(update={"entries": [e for e in matrix.entries if e.id != ONE_CLAUSE]}),
    )

    assert (MISSING_REVIEW, f"matrix_edited:{ONE_CLAUSE}") in details(
        row_of(assess_now(signed), ONE_CLAUSE)
    )


# --- el historial de la matriz es de solo adición y se contrasta con su clave ------------------


def history_keys(store):
    return store.list(history_prefix(ONE_CLAUSE))


def test_cada_orden_deja_un_registro_con_orden_instante_y_huella_en_la_clave(signed):
    evidence_id, link = reviewed(signed)
    remove_link(signed, hours(1), ONE_CLAUSE, EVIDENCE, evidence_id)

    scan = scan_history(signed, ONE_CLAUSE)

    assert [c.seq for c in scan.changes] == [1, 2] and not scan.unreadable
    first, second = history_keys(signed)
    assert first.rsplit("/", 1)[1].startswith("000001-2026")
    assert second.rsplit("/", 1)[1].startswith("000002-")
    assert scan.changes[1].entry.links == []


def test_un_registro_con_el_contenido_alterado_es_ilegible_y_bloquea(signed):
    reviewed(signed)
    (key,) = history_keys(signed)
    edit_yaml(signed, key, lambda d: d["entry"].update(review_every_days=1))

    result = assess_now(signed)
    row = row_of(result, ONE_CLAUSE)

    assert (MISSING_REVIEW, f"matrix_history_unreadable:{key}") in details(row)
    assert any(key in e for e in result.errors)
    assert row.measure.review is None and not row_status(row).implemented


def test_retrofechar_un_registro_de_la_matriz_lo_deja_ilegible(signed):
    evidence_id, link = reviewed(signed)
    remove_link(signed, hours(1), ONE_CLAUSE, EVIDENCE, evidence_id)
    _, unlink = history_keys(signed)

    edit_yaml(
        signed, unlink, lambda d: d.update(recorded_at=(RECORDED - timedelta(days=9)).isoformat())
    )
    add_link(signed, hours(2), ONE_CLAUSE, link)

    assert unlink in scan_history(signed, ONE_CLAUSE).unreadable
    assert (MISSING_REVIEW, f"matrix_history_unreadable:{unlink}") in details(
        row_of(assess_now(signed), ONE_CLAUSE)
    )


def test_un_hueco_en_la_numeracion_del_historial_bloquea(signed):
    """Sin borrado en el almacén, el hueco se fabrica saltándose números: es lo que deja
    quitar un registro intermedio."""
    _, link = reviewed(signed)
    (first,) = history_keys(signed)
    skipped = MatrixChange(
        measure_id=ONE_CLAUSE, seq=3, recorded_at=hours(1), entry=entry(ONE_CLAUSE, link)
    )
    data = _dump(skipped)
    signed.create_bytes(_key(skipped, data), data)

    scan = scan_history(signed, ONE_CLAUSE)

    assert scan.unreadable == (f"{history_prefix(ONE_CLAUSE)}/seq",) and first
    assert any(
        d.startswith("matrix_history_unreadable")
        for _, d in details(row_of(assess_now(signed), ONE_CLAUSE))
    )


def test_un_registro_de_otra_medida_copiado_a_mano_es_ilegible(signed):
    reviewed(signed)
    (key,) = history_keys(signed)
    stray = key.replace(ONE_CLAUSE, "op.ext.4")
    signed.create_bytes(stray, signed.read_bytes(key))

    assert scan_history(signed, "op.ext.4").unreadable == (stray,)


# --- evidencias: el id codifica el día del alta ------------------------------------------------


def test_retrofechar_submitted_at_de_una_evidencia_la_deja_sin_verificar(signed):
    from ens_evidence.evidence.layout import manifest_key

    evidence_id, _ = reviewed(signed)
    key = manifest_key(evidence_id)

    edit_yaml(
        signed,
        key,
        lambda d: d["provenance"].update(submitted_at=(RECORDED - timedelta(days=40)).isoformat()),
    )
    row = row_of(assess_now(signed), ONE_CLAUSE)

    assert not row_status(row).implemented
    assert (BlockerKind.MISSING_EVIDENCE.value, f"evidence_unverified:{evidence_id}") in details(
        row
    )
