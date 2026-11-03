"""Aceptación del expediente: la CLI sobre mundos reales (BÁSICA READY y MEDIA), sin dobles."""

import json

import pytest

from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX

from .stages import AFTER, D, basic, media, restore, run_at, step
from .transition_helpers import DIRECTION, SYSTEM_OWNER

INTERNAL = "no sustituye a la auditoría de certificación (art. 38.1)"


@pytest.fixture(scope="module")
def happy():
    return basic()


@pytest.fixture(scope="module")
def media_stages():
    return media()


def at_stage(stages, stage, tmp_path, monkeypatch):
    return restore(stages, stage, tmp_path, monkeypatch)


# --- camino feliz en BÁSICA --------------------------------------------------------------------


def test_el_camino_completo_en_basica_registra_las_cuatro_etapas_por_la_cli(happy):
    assert happy.exit_codes == {"generate": 0, "self-assess": 0, "analyze": 0, "declare": 0}
    assert "BÁSICA" in happy.outputs["generate"]
    assert "SELF_ASSESSED registrado" in happy.outputs["self-assess"]
    assert "ANALYZED registrado" in happy.outputs["analyze"]
    assert (
        f"DECLARED registrado en {happy.dossier} por {DIRECTION} (dirección)"
        in (happy.outputs["declare"])
    )


def test_el_estado_final_es_declared(happy, tmp_path, monkeypatch):
    world = at_stage(happy, "declare", tmp_path, monkeypatch)

    status = run_at(world, *D, "status", happy.dossier, at=AFTER)
    listed = run_at(world, *D, "list", at=AFTER)

    assert status.exit_code == 0, status.output
    assert "Estado: DECLARED" in status.output
    assert "Integridad: íntegro." in status.output
    assert "Deriva: ninguna" in status.output
    assert f"{happy.dossier}  BÁSICA  DECLARED" in listed.output


def test_la_vista_dice_que_la_declaracion_la_registro_la_direccion_como_acto_de_la_organizacion(
    happy, tmp_path, monkeypatch
):
    world = at_stage(happy, "declare", tmp_path, monkeypatch)

    view = run_at(world, *D, "show", happy.dossier, at=AFTER)

    assert view.exit_code == 0, view.output
    assert (
        f"Declaración de conformidad registrada por {DIRECTION} (dirección); "
        "acto de la organización, la herramienta no declara"
    ) in view.output
    assert "### SELF_ASSESSED" in view.output and "### ANALYZED" in view.output


def test_verify_reproduce_los_bytes_del_expediente_declarado(happy, tmp_path, monkeypatch):
    world = at_stage(happy, "declare", tmp_path, monkeypatch)

    verified = run_at(world, *D, "verify", happy.dossier, at=AFTER)

    assert verified.exit_code == 0, verified.output
    assert "reproducible con sus entradas congeladas" in verified.output


def test_el_analisis_sin_medidas_correctoras_registra_la_elevacion_al_responsable_del_sistema(
    happy, tmp_path, monkeypatch
):
    world = at_stage(happy, "declare", tmp_path, monkeypatch)

    view = run_at(world, *D, "show", happy.dossier, at=AFTER).output

    assert f"Elevado a {SYSTEM_OWNER} (responsable del sistema)" in view
    assert "elevación confirmada: sí" in view
    assert "- Sin medidas correctoras registradas." in view


# --- escenario: estados en orden --------------------------------------------------------------


@pytest.mark.parametrize("stage", ["generated", "self-assess"])
def test_declarar_sin_el_analisis_se_rechaza_y_nombra_al_responsable_de_la_seguridad(
    happy, stage, tmp_path, monkeypatch
):
    world = at_stage(happy, stage, tmp_path, monkeypatch)

    declared = step(world, "declare", happy.dossier)
    status = run_at(world, *D, "status", happy.dossier, at=AFTER)

    assert declared.exit_code == 1
    assert "No se registra DECLARED" in declared.output
    assert "análisis del responsable de la seguridad" in declared.output
    assert "Estado: DECLARED" not in status.output
    assert f"Estado: {'GENERATED' if stage == 'generated' else 'SELF_ASSESSED'}" in status.output


# --- escenario: MEDIA -------------------------------------------------------------------------


def snapshot_of(world, dossier):
    path = world.store._root / ASSESSMENTS_PREFIX / dossier / "dossier.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_en_media_el_json_y_el_markdown_avisan_de_la_auditoria_de_certificacion(
    media_stages, tmp_path, monkeypatch
):
    world = at_stage(media_stages, "generated", tmp_path, monkeypatch)
    folder = world.store._root / ASSESSMENTS_PREFIX / media_stages.dossier

    snapshot = json.loads((folder / "dossier.json").read_text(encoding="utf-8"))
    markdown = (folder / "dossier.md").read_text(encoding="utf-8")

    assert snapshot["category"] == "MEDIA"
    assert INTERNAL in snapshot["notice"]
    assert "Requiere auditoría de certificación externa" in json.dumps(snapshot, ensure_ascii=False)
    assert INTERNAL in markdown
    assert "Requiere auditoría de certificación externa" in markdown
    assert INTERNAL in media_stages.outputs["generate"]


def test_en_media_no_hay_ruta_de_declaracion_y_declare_se_rechaza(
    media_stages, tmp_path, monkeypatch
):
    world = at_stage(media_stages, "generated", tmp_path, monkeypatch)

    status = run_at(world, *D, "status", media_stages.dossier, at=AFTER)
    declared = step(world, "declare", media_stages.dossier)

    assert status.exit_code == 0, status.output
    assert "declare" not in status.output
    assert "declare" not in media_stages.outputs["generate"]
    assert declared.exit_code == 1
    assert "auditoría de certificación (art. 38.1)" in declared.output
    assert "Estado: GENERATED" in run_at(world, *D, "status", media_stages.dossier, at=AFTER).output


def test_en_media_el_paquete_tiene_una_entrada_por_cada_fila_de_la_declaracion(
    media_stages, tmp_path, monkeypatch
):
    world = at_stage(media_stages, "generated", tmp_path, monkeypatch)
    snapshot = snapshot_of(world, media_stages.dossier)
    declaration = snapshot["sections"][4]["content"]["declaration"]
    rows = {m["id"] for m in declaration["selected"]} | {
        m["id"] for m in declaration["not_selected"]
    }
    folder = world.store._root / ASSESSMENTS_PREFIX / media_stages.dossier / "package"
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))

    assert len(rows) == len(declaration["selected"]) + len(declaration["not_selected"]) >= 69
    assert {entry["file"].removesuffix(".json") for entry in index["measures"]} == rows
    assert {p.name.removesuffix(".json") for p in folder.glob("*.json")} - {"index"} == rows
    assert index["internal_review"] and INTERNAL in index["internal_review"]


def test_en_media_la_autoevaluacion_y_el_analisis_se_permiten_como_revision_interna(
    media_stages, tmp_path, monkeypatch
):
    world = at_stage(media_stages, "generated", tmp_path, monkeypatch)

    assessed = step(world, "self-assess", media_stages.dossier)
    analyzed = step(world, "analyze", media_stages.dossier)
    declared = step(world, "declare", media_stages.dossier)
    status = run_at(world, *D, "status", media_stages.dossier, at=AFTER)

    assert (assessed.exit_code, analyzed.exit_code, declared.exit_code) == (0, 0, 1), (
        assessed.output,
        analyzed.output,
    )
    assert INTERNAL in assessed.output and INTERNAL in analyzed.output
    assert INTERNAL in status.output
    assert "Estado: ANALYZED" in status.output
