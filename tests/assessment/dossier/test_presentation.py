"""Presentación del expediente por la CLI: lo que la organización lee, sobre mundos reales."""

import re
from datetime import timedelta

import pytest
from checks.acceptance_helpers import COLLECTED
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX

from .stages import AFTER, STEP_AT, STEPS, D, basic, media, restore, run_at
from .worlds import basic_ready_world

INTERNAL = "no sustituye a la auditoría de certificación (art. 38.1)"
NOTICE = "Revisión interna; " + INTERNAL
LATER = NOW + timedelta(hours=3)
DECLARE_COMMAND = re.compile(r"assessment dossier declare")


def folder(world, dossier: str):
    return world.store._root / ASSESSMENTS_PREFIX / dossier


@pytest.fixture(scope="module")
def media_stages():
    return media()


@pytest.fixture
def media_world(media_stages, tmp_path, monkeypatch):
    return restore(media_stages, "generated", tmp_path, monkeypatch), media_stages.dossier


@pytest.fixture
def declared_world(tmp_path, monkeypatch):
    stages = basic()
    return restore(stages, "declare", tmp_path, monkeypatch), stages.dossier


@pytest.fixture
def not_ready_analyzed(tmp_path, monkeypatch):
    """BÁSICA real cuya última recogida falla (secret scanning apagado): se genera NOT_READY."""
    world = basic_ready_world(tmp_path, monkeypatch)
    world.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    world.check_all(NOW - timedelta(minutes=45))
    generated = run_at(world, *D, "generate", at=STEP_AT["generate"])
    assert generated.exit_code == 0, generated.output
    dossier = re.search(r"\d{8}T\d{12}Z-[0-9a-f]{12}", generated.output).group()
    for name in ("self-assess", "analyze"):
        result = run_at(world, *D, name, dossier, *STEPS[name], at=STEP_AT[name])
        assert result.exit_code == 0, result.output
    return world, dossier, result.output


# --- SEC-8 --------------------------------------------------------------------------------------


def test_status_detecta_un_paquete_editado_y_sale_con_1(media_world):
    world, dossier = media_world
    package = folder(world, dossier) / "package"
    edited = next(p for p in sorted(package.iterdir()) if p.name != "index.json")
    edited.write_text(edited.read_text(encoding="utf-8") + "\nTodo en orden.\n", encoding="utf-8")

    status = run_at(world, *D, "status", dossier, at=AFTER)

    assert status.exit_code == 1, status.output
    assert edited.name in status.output
    assert "íntegro" not in status.output.replace("no íntegro", "")


# --- PROD-10 ------------------------------------------------------------------------------------


def test_la_salida_de_analyze_en_not_ready_no_propone_declarar(not_ready_analyzed):
    _, _, analyzed = not_ready_analyzed

    assert "ANALYZED registrado" in analyzed
    assert "Siguiente paso: ninguno" in analyzed
    assert "expediente nuevo" in analyzed
    assert not DECLARE_COMMAND.search(analyzed)


def test_status_y_show_en_not_ready_tampoco_proponen_declarar(not_ready_analyzed):
    world, dossier, _ = not_ready_analyzed

    status = run_at(world, *D, "status", dossier, at=LATER)
    shown = run_at(world, *D, "show", dossier, at=LATER)

    assert "Siguiente paso: no se puede declarar" in status.output
    assert not DECLARE_COMMAND.search(status.output)
    assert not DECLARE_COMMAND.search(shown.output)


def test_en_un_expediente_ready_analyze_si_propone_declarar(tmp_path, monkeypatch):
    stages = basic()
    ready = restore(stages, "self-assess", tmp_path, monkeypatch)

    analyzed = run_at(
        ready, *D, "analyze", stages.dossier, *STEPS["analyze"], at=STEP_AT["analyze"]
    )

    assert analyzed.exit_code == 0, analyzed.output
    assert DECLARE_COMMAND.search(analyzed.output)


# --- PROD-11 ------------------------------------------------------------------------------------


def test_el_expediente_muestra_que_la_declaracion_reconocio_secciones_incompletas(declared_world):
    world, dossier = declared_world

    shown = run_at(world, *D, "show", dossier, at=AFTER)

    assert shown.exit_code == 0, shown.output
    assert "Declarada reconociendo secciones incompletas: 4. Riesgos" in shown.output


# --- PROD-12 ------------------------------------------------------------------------------------


def test_en_media_el_aviso_de_revision_interna_aparece_una_sola_vez_en_generate_y_status(
    media_stages, media_world
):
    world, dossier = media_world
    status = run_at(world, *D, "status", dossier, at=AFTER)

    assert media_stages.outputs["generate"].count(INTERNAL) == 1
    assert status.output.count(INTERNAL) == 1
    for output in (media_stages.outputs["generate"], status.output):
        assert f"({NOTICE}" not in output
        assert "(Revisión interna" not in output


def test_en_media_el_expediente_lleva_el_aviso_una_sola_vez_y_sin_anidar(media_world):
    world, dossier = media_world

    markdown = (folder(world, dossier) / "dossier.md").read_text(encoding="utf-8")

    assert markdown.count(INTERNAL) == 1
    assert "(Revisión interna" not in markdown
    assert "> > " not in markdown


# --- PROD-13 ------------------------------------------------------------------------------------


@pytest.fixture
def without_markdown(declared_world):
    world, dossier = declared_world
    (folder(world, dossier) / "dossier.md").unlink()
    return world, dossier


@pytest.mark.parametrize("command", ["status", "verify"])
def test_sin_dossier_md_sugiere_recrearlo_con_render(without_markdown, command):
    world, dossier = without_markdown

    result = run_at(world, *D, command, dossier, at=AFTER)

    assert f"assessment dossier render {dossier}" in result.output


@pytest.mark.parametrize("command", ["status", "verify"])
def test_sin_dossier_md_no_dice_integro(without_markdown, command):
    world, dossier = without_markdown

    result = run_at(world, *D, command, dossier, at=AFTER)

    assert "íntegro" not in result.output
