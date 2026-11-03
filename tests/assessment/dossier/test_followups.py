"""Casos de borde del expediente por la CLI: `--at`, designación cambiada, DECLARED con deriva y
aviso de `status` cuando el sistema deja de estar preparado."""

import re
from datetime import timedelta

import pytest
from matrix.acceptance_helpers import APPROVER
from matrix.evaluate_helpers import NOW
from orgdocs.orgdocs_helpers import SIGNATORY

from ens_evidence.orgdocs import approve_document, build_documents

from .stages import AFTER, STEP_AT, STEPS, D, basic, restore, run_at, step
from .test_invariants import RECOLLECTED, RERUN, records, state_of, status_of
from .transition_helpers import DESIGNATION
from .worlds import edit_answers


@pytest.fixture(scope="module")
def chain():
    return basic()


def with_at(world, name, dossier, at, clock):
    world.at(clock)
    return world.run(*D, name, dossier, *STEPS[name], "--at", at.isoformat())


# --- --at fuera de rango -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "name", "at", "problem"),
    [
        ("generated", "self-assess", STEP_AT["self-assess"] + timedelta(days=1), "en el futuro"),
        ("generated", "self-assess", NOW - timedelta(hours=1), "anterior a la generación"),
        (
            "self-assess",
            "analyze",
            STEP_AT["self-assess"] - timedelta(minutes=1),
            "anterior a la transición previa",
        ),
    ],
    ids=["futuro", "anterior-a-la-generacion", "anterior-a-la-transicion-previa"],
)
def test_at_fuera_de_rango_se_rechaza_por_la_cli_y_no_escribe(
    chain, stage, name, at, problem, tmp_path, monkeypatch
):
    world = restore(chain, stage, tmp_path, monkeypatch)
    before = records(world, chain.dossier)

    result = with_at(world, name, chain.dossier, at, STEP_AT[name])

    assert result.exit_code == 1
    assert "--at" in result.output and problem in result.output
    assert records(world, chain.dossier) == before


# --- designación cambiada entre generar y transicionar -----------------------------------------


def test_cambiar_el_responsable_de_seguridad_tras_generar_es_deriva_y_bloquea(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "self-assess", tmp_path, monkeypatch)
    edit_answers(world.store, "resp_seguridad", "Raúl Campos Vidal")
    build_documents(world.store)
    role = APPROVER[DESIGNATION]
    approve_document(
        world.store,
        DESIGNATION,
        SIGNATORY[role],
        role,
        STEP_AT["self-assess"] + timedelta(minutes=5),
    )

    analyzed = step(world, "analyze", chain.dossier)
    status = status_of(world, chain.dossier)

    assert analyzed.exit_code == 1
    assert "el expediente ya no describe el estado actual; genera uno nuevo" in analyzed.output
    assert "org/answers.yaml" in analyzed.output
    assert status.exit_code == 1 and "Deriva: el expediente ya no describe" in status.output
    assert state_of(world, chain.dossier) == "SELF_ASSESSED"
    assert len(records(world, chain.dossier)) == 1


# --- DECLARED con las fuentes cambiadas después ------------------------------------------------


def test_un_expediente_declared_con_fuentes_cambiadas_muestra_la_deriva_y_no_admite_mas(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "declare", tmp_path, monkeypatch)
    edit_answers(world.store, "mision", "Otra misión distinta de la registrada.")

    status = status_of(world, chain.dossier)
    again = step(world, "declare", chain.dossier)
    listed = run_at(world, *D, "list", at=AFTER)

    assert status.exit_code == 1
    assert "Estado: DECLARED" in status.output
    assert "Deriva: el expediente ya no describe el estado actual" in status.output
    assert "org/answers.yaml" in status.output
    assert f"{chain.dossier}  BASICA  DECLARED" in listed.output
    assert again.exit_code == 1 and "ya no describe el estado actual" in again.output
    assert len(records(world, chain.dossier)) == 3


# --- aviso de status cuando el sistema deja de estar preparado ---------------------------------


def test_status_listo_para_declarar_avisa_si_el_sistema_ya_no_esta_preparado(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    world.collect(secret_scanning_off=True, at=RECOLLECTED)
    assert run_at(world, "check", "--all", at=RERUN).exit_code == 4

    status = status_of(world, chain.dossier)

    assert status.exit_code == 1
    assert "Estado: ANALYZED" in status.output
    assert re.search(r"no se puede declarar: .*no está preparada", status.output)
    assert "resuélvelos y vuelve a intentarlo" in status.output
    assert "la dirección decide" not in status.output


def test_status_listo_para_declarar_y_preparado_ofrece_declarar_con_exit_0(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "analyze", tmp_path, monkeypatch)

    status = status_of(world, chain.dossier)

    assert status.exit_code == 0, status.output
    assert "la dirección decide" in status.output
    assert "no se puede declarar" not in status.output
