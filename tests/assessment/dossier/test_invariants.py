"""Invariantes del expediente por la CLI, sobre el mundo READY BÁSICA real y el de MEDIA."""

import hashlib
import itertools
import json
import re
from datetime import datetime, timedelta

import pytest
from checks.test_expected import COLLECTED
from risk.helpers import PLAN, REGISTER, action, dump, edit, risk

from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX

from .stages import AFTER, STEP_AT, STEPS, D, basic, media, restore, run_at, step
from .transition_helpers import DIRECTION, SECURITY_OFFICER
from .worlds import PASSWORD

FORBIDDEN = re.compile(r"\b(conforme|cumple)\b", re.IGNORECASE)
SHA256_IN_JSON = re.compile(r'"sha256": "([0-9a-f]{64})"')
RECOLLECTED = COLLECTED + timedelta(hours=3)
RERUN = STEP_AT["analyze"] + timedelta(minutes=30)  # entre analyze y declare
CHAIN = ("self-assess", "analyze", "declare")
STATE_AFTER = {0: "GENERATED", 1: "SELF_ASSESSED", 2: "ANALYZED", 3: "DECLARED"}


@pytest.fixture(scope="module")
def chain():
    return basic()


@pytest.fixture(scope="module")
def internal():
    return media()


def folder_of(world, dossier):
    return world.store._root / ASSESSMENTS_PREFIX / dossier


def records(world, dossier) -> list[str]:
    return sorted(p.name for p in (folder_of(world, dossier) / "transitions").glob("0*.yaml"))


def status_of(world, dossier):
    return run_at(world, *D, "status", dossier, at=AFTER)


def state_of(world, dossier) -> str:
    return re.search(r"Estado: (\w+)", status_of(world, dossier).output).group(1)


# --- nunca DECLARED sin las transiciones humanas ----------------------------------------------


@pytest.mark.parametrize("order", list(itertools.permutations(CHAIN)), ids="-".join)
def test_nunca_se_llega_a_declared_sin_las_tres_transiciones_humanas_en_su_orden(
    chain, order, tmp_path, monkeypatch
):
    world = restore(chain, "generated", tmp_path, monkeypatch)

    results = {name: step(world, name, chain.dossier) for name in order}

    done = [name for name in order if results[name].exit_code == 0]
    assert state_of(world, chain.dossier) == STATE_AFTER[len(done)]
    assert (state_of(world, chain.dossier) == "DECLARED") == (order == CHAIN)
    assert len(records(world, chain.dossier)) == len(done)


# --- READY en la evaluación actual y deriva ----------------------------------------------------


def test_declarar_con_un_check_que_falla_despues_de_generar_se_rechaza(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    world.collect(secret_scanning_off=True, at=RECOLLECTED)
    failed = run_at(world, "check", "--all", at=RERUN)
    assert failed.exit_code == 4, failed.output

    declared = step(world, "declare", chain.dossier)

    assert declared.exit_code == 1
    assert "No se registra DECLARED" in declared.output
    assert "la evaluación actual no está READY" in declared.output
    assert state_of(world, chain.dossier) == "ANALYZED"


def test_una_ejecucion_nueva_equivalente_de_check_all_no_deriva_y_permite_declarar(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    world.collect(at=RECOLLECTED)
    rerun = run_at(world, "check", "--all", at=RERUN)
    assert rerun.exit_code == 0, rerun.output

    status = status_of(world, chain.dossier)
    declared = step(world, "declare", chain.dossier)

    assert "Deriva: ninguna" in status.output
    assert declared.exit_code == 0, declared.output
    assert state_of(world, chain.dossier) == "DECLARED"


def edit_matrix(world):
    edit(world.store, "controls/matrix.yaml", "review_every_days: 365", "review_every_days: 180")


def add_register(world):
    dump(world.store, REGISTER, {"risks": [risk()]})
    dump(world.store, PLAN, {"actions": [action()]})


@pytest.mark.parametrize(
    ("change", "section"),
    [(edit_matrix, "7. Resultado por medida"), (add_register, "4. Riesgos")],
    ids=["matrix", "risk-register"],
)
def test_editar_las_fuentes_entre_analyze_y_declare_se_rechaza_con_la_seccion(
    chain, change, section, tmp_path, monkeypatch
):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    change(world)

    declared = step(world, "declare", chain.dossier)

    assert declared.exit_code == 1
    status = status_of(world, chain.dossier)
    assert "el expediente ya no describe el estado actual; genera uno nuevo" in declared.output
    assert section in declared.output
    assert status.exit_code == 1 and section in status.output
    assert len(records(world, chain.dossier)) == 2


# --- integridad de la cadena y de la instantánea ----------------------------------------------


def assert_blocked(world, dossier, problem: str) -> None:
    """Status y la siguiente transición dicen el problema, y no se registra nada."""
    before = records(world, dossier)
    status = status_of(world, dossier)
    declared = step(world, "declare", dossier)

    assert status.exit_code == 1, status.output
    assert "Integridad: PROBLEMAS" in status.output and problem in status.output
    assert "no admite más transiciones" in status.output
    assert declared.exit_code == 1, declared.output
    assert "no admite más transiciones" in declared.output and problem in declared.output
    assert records(world, dossier) == before


def test_editar_a_mano_un_registro_de_transicion_bloquea_el_expediente(
    chain, tmp_path, monkeypatch
):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    record = folder_of(world, chain.dossier) / "transitions" / records(world, chain.dossier)[-1]
    record.chmod(0o644)
    record.write_text(record.read_text().replace(SECURITY_OFFICER, "Otra Persona Distinta"))

    assert_blocked(world, chain.dossier, "la clave no coincide con el contenido")


def test_borrar_el_ultimo_registro_de_la_cadena_bloquea_el_expediente(chain, tmp_path, monkeypatch):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    (folder_of(world, chain.dossier) / "transitions" / records(world, chain.dossier)[-1]).unlink()

    assert_blocked(world, chain.dossier, "reserva de la secuencia 2 sin registro")


def test_cambiar_un_byte_de_dossier_json_bloquea_el_expediente(chain, tmp_path, monkeypatch):
    world = restore(chain, "analyze", tmp_path, monkeypatch)
    path = folder_of(world, chain.dossier) / "dossier.json"
    text = path.read_text(encoding="utf-8")
    digest = SHA256_IN_JSON.search(text).group(1)
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    path.chmod(0o644)
    path.write_text(text.replace(digest, flipped, 1), encoding="utf-8")

    assert_blocked(world, chain.dossier, "el contenido no coincide con la huella del id")
    assert run_at(world, *D, "verify", chain.dossier, at=AFTER).exit_code == 1


# --- personas -----------------------------------------------------------------------------------


def with_option(args: list[str], option: str, value: str) -> list[str]:
    swapped = list(args)
    swapped[swapped.index(option) + 1] = value
    return swapped


@pytest.mark.parametrize(
    ("name", "stage", "args", "problem"),
    [
        (
            "analyze",
            "self-assess",
            with_option(STEPS["analyze"], "--by", DIRECTION),
            "--by: Marta Ibáñez Soler no es la persona declarada en resp_seguridad",
        ),
        (
            "analyze",
            "self-assess",
            with_option(STEPS["analyze"], "--elevated-to", "Pedro Ajeno"),
            "--elevated-to: Pedro Ajeno no es la persona declarada en resp_sistema",
        ),
        (
            "declare",
            "analyze",
            with_option(STEPS["declare"], "--by", SECURITY_OFFICER),
            "--by: Lucía Navarro Gil no es la persona declarada en firmante_direccion",
        ),
    ],
    ids=["analyze-by-direccion", "analyze-elevado-a-otro", "declare-by-responsable-seguridad"],
)
def test_una_persona_equivocada_en_analyze_o_declare_se_rechaza_sin_registrar(
    chain, name, stage, args, problem, tmp_path, monkeypatch
):
    world = restore(chain, stage, tmp_path, monkeypatch)
    before = records(world, chain.dossier)

    result = run_at(world, *D, name, chain.dossier, *args, at=STEP_AT[name])

    assert result.exit_code == 1, result.output
    assert f"No se registra {'DECLARED' if name == 'declare' else 'ANALYZED'}" in result.output
    assert problem in result.output
    assert records(world, chain.dossier) == before


# --- secretos ---------------------------------------------------------------------------------


@pytest.mark.parametrize("option", ["--conclusion", "--corrective-measure"])
def test_un_secreto_en_las_conclusiones_o_las_medidas_se_rechaza_sin_mostrar_el_valor(
    chain, option, tmp_path, monkeypatch
):
    world = restore(chain, "self-assess", tmp_path, monkeypatch)
    secret = f"Se anotó password={PASSWORD} en el acta"
    args = (
        with_option(STEPS["analyze"], option, secret)
        if option == "--conclusion"
        else [
            *STEPS["analyze"],
            option,
            secret,
        ]
    )

    result = run_at(world, *D, "analyze", chain.dossier, *args, at=STEP_AT["analyze"])

    assert result.exit_code == 1
    assert "contiene una credencial" in result.output
    assert PASSWORD not in result.output
    assert len(records(world, chain.dossier)) == 1
    assert PASSWORD not in "".join(
        p.read_text(encoding="utf-8") for p in folder_of(world, chain.dossier).rglob("*.*")
    )


# --- vocabulario ------------------------------------------------------------------------------


def test_ninguna_salida_dice_conforme_ni_cumple(chain, internal, tmp_path, monkeypatch):
    world = restore(chain, "declare", tmp_path / "b", monkeypatch)
    media_world = restore(internal, "generated", tmp_path / "m", monkeypatch)
    texts = [*chain.outputs.values(), *internal.outputs.values()]
    for w, dossier in ((world, chain.dossier), (media_world, internal.dossier)):
        texts += [
            run_at(w, *D, command, dossier, *extra, at=AFTER).output
            for command in ("status", "show", "verify")
            for extra in ([], ["--format", "json"] if command == "show" else [])
        ]
        texts += [
            p.read_text(encoding="utf-8")
            for p in folder_of(w, dossier).rglob("*")
            if p.is_file() and p.suffix in {".md", ".json", ".yaml"}
        ]
    texts.append(run_at(world, *D, "list", at=AFTER).output)

    found = [m.group() for text in texts for m in FORBIDDEN.finditer(text)]

    assert found == []


# --- fuentes ----------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["basica", "media"])
def test_las_fuentes_existen_con_su_sha256_real_y_sus_marcas_de_tiempo_llevan_zona(
    chain, internal, which, tmp_path, monkeypatch
):
    stages, stage = (chain, "declare") if which == "basica" else (internal, "generated")
    world = restore(stages, stage, tmp_path, monkeypatch)
    snapshot = json.loads((folder_of(world, stages.dossier) / "dossier.json").read_text("utf-8"))
    sources = [s for section in snapshot["sections"] for s in section["sources"]]

    assert sources
    for source in sources:
        raw = (world.store._root / source["key"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["sha256"], source["key"]
        assert source["timestamps"]
        for stamp in source["timestamps"]:
            assert datetime.fromisoformat(stamp).utcoffset() is not None, source["key"]
    for stamp in (snapshot["inputs"]["generated_at"], snapshot["inputs"]["evaluated_at"]):
        assert datetime.fromisoformat(stamp).utcoffset() is not None
