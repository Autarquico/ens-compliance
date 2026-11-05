"""CLI del expediente: `assessment dossier …`. Cada comando, con éxito y con rechazo."""

import json
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from matrix.acceptance_helpers import Clock, runner, settings_file
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX, load_snapshot
from ens_evidence.schemas import CONTRACTS

from .stages import STEP_AT, basic, restore
from .transition_helpers import CONCLUSIONS, DIRECTION, RECORDED, SECURITY_OFFICER, SYSTEM_OWNER
from .worlds import basic_store, edit_answers

ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{12}")
NAME_NOTICE = "El nombre es declarado; no se autentica."
INTERNAL = "no sustituye a la auditoría de certificación (art. 38.1)"
FORBIDDEN = re.compile(r"\b(conforme|cumple)\b", re.IGNORECASE)
D = ["assessment", "dossier"]
ANALYZE = [
    "--by",
    SECURITY_OFFICER,
    "--conclusion",
    CONCLUSIONS,
    "--elevated-to",
    SYSTEM_OWNER,
    "--elevated",
    "--manual-judgment-review",
]


@pytest.fixture
def cli(docs, tmp_path, monkeypatch):
    """CLI sobre BÁSICA con Declaración y org-docs; el reloj marca `NOW`."""
    clock = Clock(monkeypatch)
    run = runner(
        settings_file(tmp_path, {"backend": "local", "local": {"path": str(tmp_path / "ws")}})
    )

    def go(*args: str, at=NOW):
        clock.now = at
        return run(*args)

    return SimpleNamespace(run=go, store=docs)


@pytest.fixture
def at_stage(tmp_path, monkeypatch):
    """CLI sobre una copia del árbol real de BÁSICA tras `stage`: (cli, id del expediente)."""

    def restored(stage: str):
        stages = basic()
        world = restore(stages, stage, tmp_path, monkeypatch)

        def go(*args: str, at=NOW):
            world.at(at)
            return world.run(*args)

        return SimpleNamespace(run=go, store=world.store), stages.dossier

    return restored


SELF_ASSESS = ["--by", "Ana", "--role", "system_administrator"]
DECLARE = ["--by", DIRECTION, "--acknowledge-incomplete"]


def media(ready):
    def go(*args: str, at=NOW):
        ready.at(at)
        return ready.run(*args)

    return go


def generated(cli) -> str:
    result = cli.run(*D, "generate")
    assert result.exit_code == 0, result.output
    return ID.search(result.output).group()


def key(store, dossier: str, name: str) -> str:
    return f"{ASSESSMENTS_PREFIX}/{dossier}/{name}"


# --- generate / list --------------------------------------------------------------------------


def test_generate_en_basica_imprime_id_categoria_compromiso_y_siguiente_paso(cli):
    result = cli.run(*D, "generate")

    assert result.exit_code == 0, result.output
    dossier = ID.search(result.output).group()
    assert "BÁSICA" in result.output
    assert "La herramienta no declara conformidad." in result.output
    assert INTERNAL not in result.output
    assert "self-assess" in result.output
    assert cli.store.exists(key(cli.store, dossier, "dossier.json"))
    assert cli.store.exists(key(cli.store, dossier, "dossier.md"))
    assert not cli.store.list(key(cli.store, dossier, "package"))
    assert not FORBIDDEN.search(result.output)


def test_generate_sin_declaracion_se_rechaza_con_1_y_no_escribe(tmp_path, monkeypatch):
    store = basic_store(tmp_path / "ws", declared=False)
    clock = Clock(monkeypatch)
    run = runner(
        settings_file(tmp_path, {"backend": "local", "local": {"path": str(tmp_path / "ws")}})
    )
    clock.now = NOW

    result = run(*D, "generate")

    assert result.exit_code == 1
    assert "No se genera el expediente" in result.output
    assert not store.list(ASSESSMENTS_PREFIX)


def test_generate_sin_categorizacion_vigente_sale_con_1(cli):
    result = cli.run(*D, "generate", at=NOW + timedelta(days=3650))

    assert result.exit_code == 1
    assert not cli.store.list(ASSESSMENTS_PREFIX)


def test_list_sin_expedientes_y_con_uno(cli):
    empty = cli.run(*D, "list")
    dossier = generated(cli)
    listed = cli.run(*D, "list")

    assert empty.exit_code == 0
    assert "No hay expedientes" in empty.output
    assert listed.exit_code == 0
    assert f"{dossier}  BÁSICA  GENERATED" in listed.output


def test_sin_ajustes_validos_sale_con_2(tmp_path):
    run = runner(tmp_path / "no-existe.yaml")

    assert run(*D, "list").exit_code == 2


# --- status -----------------------------------------------------------------------------------


def test_status_de_un_expediente_vigente_muestra_estado_siguiente_paso_y_huellas(cli):
    dossier = generated(cli)

    result = cli.run(*D, "status", dossier)

    assert result.exit_code == 0, result.output
    assert "Estado: GENERATED" in result.output
    assert "Integridad: íntegro." in result.output
    assert "Deriva: ninguna" in result.output
    assert "autoevaluación" in result.output
    assert re.search(r"Huella instantánea: [0-9a-f]{64}", result.output)
    assert not FORBIDDEN.search(result.output)


def test_status_con_deriva_sale_con_1_y_dice_la_seccion_y_el_siguiente_paso(cli):
    dossier = generated(cli)
    edit_answers(cli.store, "mision", "Otra misión distinta de la registrada.")

    result = cli.run(*D, "status", dossier)

    assert result.exit_code == 1
    assert "ya no describe el estado actual" in result.output
    assert "org/answers.yaml" in result.output
    assert "genera un expediente nuevo" in result.output


@pytest.mark.parametrize("dossier", ["20260101T000000000000Z-000000000000", "../fuera"])
def test_status_de_un_expediente_inexistente_o_mal_formado_sale_con_1(cli, dossier):
    assert cli.run(*D, "status", dossier).exit_code == 1


def test_status_con_un_registro_borrado_de_la_cadena_sale_con_1(at_stage):
    cli, dossier = at_stage("self-assess")
    record = next(
        k for k in cli.store.list(key(cli.store, dossier, "transitions")) if k.endswith(".yaml")
    )
    (cli.store._root / record).unlink()

    result = cli.run(*D, "status", dossier, at=STEP_AT["self-assess"])

    assert result.exit_code == 1
    assert "Integridad: PROBLEMAS" in result.output
    assert "recupera el expediente" in result.output


# --- transiciones -----------------------------------------------------------------------------


def test_self_assess_registra_avisa_del_nombre_y_nombra_al_responsable_de_la_seguridad(at_stage):
    cli, dossier = at_stage("generated")

    result = cli.run(*D, "self-assess", dossier, *SELF_ASSESS, at=STEP_AT["self-assess"])

    assert result.exit_code == 0, result.output
    assert "SELF_ASSESSED registrado" in result.output
    assert NAME_NOTICE in result.output
    assert "responsable de la seguridad" in result.output
    assert not FORBIDDEN.search(result.output)


def test_self_assess_sin_revision_en_cada_fila_se_rechaza(cli):
    dossier = generated(cli)

    result = cli.run(
        *D, "self-assess", dossier, "--by", "Ana", "--role", "system_administrator", at=RECORDED
    )

    assert result.exit_code == 1
    assert "No se registra SELF_ASSESSED" in result.output
    assert "revisión en cada fila" in result.output


def test_self_assess_con_un_rol_ajeno_se_rechaza(at_stage):
    cli, dossier = at_stage("generated")

    result = cli.run(
        *D, "self-assess", dossier, "--by", "Ana", "--role", "management", at=STEP_AT["self-assess"]
    )

    assert result.exit_code == 2
    assert "system_administrator_delegate" in result.output


def test_analyze_registra_con_medida_correctora_y_nombra_a_la_direccion(at_stage):
    cli, dossier = at_stage("self-assess")

    result = cli.run(
        *D,
        "analyze",
        dossier,
        *ANALYZE,
        "--corrective-measure",
        "Revisar los accesos de terceros.",
        at=STEP_AT["analyze"],
    )

    assert result.exit_code == 0, result.output
    assert "ANALYZED registrado" in result.output
    assert NAME_NOTICE in result.output
    assert "dirección" in result.output
    shown = cli.run(*D, "show", dossier)
    assert "Revisar los accesos de terceros." in shown.output


def test_analyze_sin_confirmar_la_elevacion_se_rechaza_y_dice_que_significa(at_stage):
    cli, dossier = at_stage("self-assess")
    args = [a for a in ANALYZE if a != "--elevated"]

    result = cli.run(*D, "analyze", dossier, *args, at=STEP_AT["analyze"])
    helped = cli.run(*D, "analyze", "--help")

    assert result.exit_code == 1
    assert "--elevated" in result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", helped.output)
    help_text = " ".join(plain.replace("│", " ").split())
    assert "elevado las conclusiones" in help_text
    assert "juicio manual" in help_text


def test_escenario_1_declare_sin_analyze_sale_con_1_y_nombra_el_paso_del_responsable(at_stage):
    cli, dossier = at_stage("self-assess")

    result = cli.run(*D, "declare", dossier, "--by", DIRECTION, at=STEP_AT["declare"])

    assert result.exit_code == 1
    assert "No se registra DECLARED" in result.output
    assert "análisis del responsable de la seguridad" in result.output
    status = cli.run(*D, "status", dossier, at=STEP_AT["declare"])
    assert status.output.count("Estado: SELF_ASSESSED") == 1


def test_declare_registra_el_acto_de_la_organizacion_sin_declarar_la_herramienta(at_stage):
    cli, dossier = at_stage("analyze")

    result = cli.run(*D, "declare", dossier, *DECLARE, at=STEP_AT["declare"])

    assert result.exit_code == 0, result.output
    assert "DECLARED registrado" in result.output
    assert NAME_NOTICE in result.output
    assert "acto de la organización" in result.output
    assert "no los genera ella" in result.output
    assert cli.run(*D, "status", dossier, at=STEP_AT["declare"]).exit_code == 0
    assert "DECLARED" in cli.run(*D, "list").output


def test_declare_con_una_evidencia_inexistente_se_rechaza(at_stage):
    cli, dossier = at_stage("analyze")

    result = cli.run(
        *D,
        "declare",
        dossier,
        "--by",
        DIRECTION,
        "--acknowledge-incomplete",
        "--evidence",
        "EV-20260101-abcdef12",
        at=STEP_AT["declare"],
    )

    assert result.exit_code == 1
    assert "--evidence" in result.output


def test_una_transicion_sobre_un_expediente_con_deriva_se_rechaza(at_stage):
    cli, dossier = at_stage("generated")
    edit_answers(cli.store, "mision", "Otra misión distinta de la registrada.")

    result = cli.run(*D, "self-assess", dossier, *SELF_ASSESS, at=STEP_AT["self-assess"])

    assert result.exit_code == 1
    assert "genera uno nuevo" in result.output


# --- show -------------------------------------------------------------------------------------


def test_show_md_trae_las_secciones_10_y_11_con_la_cadena(at_stage):
    cli, dossier = at_stage("analyze")

    result = cli.run(*D, "show", dossier)

    assert result.exit_code == 0, result.output
    assert "## 10. Conclusiones" in result.output
    assert "## 11. Aprobación/fecha" in result.output
    assert "### SELF_ASSESSED" in result.output
    assert "### ANALYZED" in result.output
    assert CONCLUSIONS in result.output


def test_show_json_es_la_vista_y_save_solo_anade(at_stage):
    cli, dossier = at_stage("self-assess")

    shown = cli.run(*D, "show", dossier, "--format", "json", "--save")
    again = cli.run(*D, "show", dossier, "--format", "json", "--save")

    assert shown.exit_code == 0 and again.exit_code == 0
    body = shown.output[: shown.output.index("\nVista guardada")]
    view = json.loads(body)
    assert [t["record"]["to_state"] for t in view["transitions"]] == ["SELF_ASSESSED"]
    saved = [k for k in cli.store.list(key(cli.store, dossier, "view"))]
    assert len(saved) == 1 and saved[0].endswith(".json")
    assert "Ya estaba guardada" in again.output


def test_show_de_un_expediente_inexistente_sale_con_1(cli):
    assert cli.run(*D, "show", "20260101T000000000000Z-000000000000").exit_code == 1


# --- verify / package / render ----------------------------------------------------------------


def test_verify_reproduce_los_bytes_con_las_entradas_congeladas(cli):
    dossier = generated(cli)

    result = cli.run(*D, "verify", dossier, at=RECORDED)

    assert result.exit_code == 0, result.output
    assert "reproducible con sus entradas congeladas" in result.output


def test_verify_con_las_fuentes_cambiadas_sale_con_1(cli):
    dossier = generated(cli)
    edit_answers(cli.store, "mision", "Otra misión distinta de la registrada.")

    result = cli.run(*D, "verify", dossier)

    assert result.exit_code == 1
    assert "no da los mismos bytes" in result.output or "no se puede reproducir" in result.output


def test_package_bajo_demanda_en_basica_no_sobrescribe_y_verify_lo_comprueba(cli):
    dossier = generated(cli)

    first = cli.run(*D, "package", dossier)
    second = cli.run(*D, "package", dossier)
    verified = cli.run(*D, "verify", dossier)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 1
    assert "no se sobrescribe" in second.output
    assert verified.exit_code == 0, verified.output
    assert cli.store.exists(key(cli.store, dossier, "package/index.json"))


def test_package_de_un_expediente_inexistente_sale_con_1(cli):
    assert cli.run(*D, "package", "20260101T000000000000Z-000000000000").exit_code == 1


def test_render_recupera_dossier_md_y_nunca_sobrescribe(cli):
    dossier = generated(cli)
    path = cli.store._root / key(cli.store, dossier, "dossier.md")
    path.unlink()

    recovered = cli.run(*D, "render", dossier)
    path.write_text("editado a mano")
    untouched = cli.run(*D, "render", dossier)

    assert recovered.exit_code == 0 and "Recuperado: dossier.md" in recovered.output
    assert untouched.exit_code == 0 and "No falta nada" in untouched.output
    assert path.read_text() == "editado a mano"


def test_en_media_generate_guarda_el_paquete_y_render_lo_recupera(ready):
    run = media(ready)

    generated_ = run(*D, "generate")
    dossier = ID.search(generated_.output).group()
    verified = run(*D, "verify", dossier)
    package = ready.store._root / key(ready.store, dossier, "package")
    for file in package.iterdir():
        file.unlink()
    recovered = run(*D, "render", dossier)
    again = run(*D, "verify", dossier)

    assert generated_.exit_code == 0, generated_.output
    assert INTERNAL in generated_.output
    assert "Paquete por medida guardado" in generated_.output
    assert verified.exit_code == 0, verified.output
    assert recovered.exit_code == 0 and "Recuperado: package" in recovered.output
    assert again.exit_code == 0, again.output


def test_en_media_un_paquete_incompleto_no_se_sobrescribe(ready):
    run = media(ready)
    dossier = ID.search(run(*D, "generate").output).group()
    package = ready.store._root / key(ready.store, dossier, "package")
    (package / "index.json").unlink()

    result = run(*D, "render", dossier)

    assert result.exit_code == 1
    assert "no se sobrescribe" in result.output
    assert not (package / "index.json").exists()


def test_en_media_la_revision_interna_se_registra_con_etiqueta_y_declare_se_rechaza(ready):
    run = media(ready)
    dossier = ID.search(run(*D, "generate").output).group()

    assessed = run(
        *D, "self-assess", dossier, "--by", "Ana", "--role", "system_administrator", at=RECORDED
    )
    declared = run(*D, "declare", dossier, "--by", DIRECTION, at=RECORDED)
    status = run(*D, "status", dossier, at=RECORDED)

    assert assessed.exit_code == 0, assessed.output
    assert INTERNAL in assessed.output
    assert declared.exit_code == 1
    assert "auditoría de certificación (art. 38.1)" in declared.output
    assert INTERNAL in status.output


def test_las_instantaneas_y_transiciones_tienen_esquema():
    assert {"dossier-snapshot", "dossier-transition"} <= set(CONTRACTS)


def test_un_expediente_guardado_se_relee(cli):
    dossier = generated(cli)

    assert load_snapshot(cli.store, dossier).inputs.generated_at == NOW


# --- derivados manipulados, ids y avisos -------------------------------------------------------


def edit(cli, dossier: str, name: str, text: str) -> None:
    (cli.store._root / key(cli.store, dossier, name)).write_text(text)


def test_un_dossier_md_editado_a_mano_bloquea_verify_y_status(cli):
    dossier = generated(cli)
    original = (cli.store._root / key(cli.store, dossier, "dossier.md")).read_text()
    edit(cli, dossier, "dossier.md", original.replace("NOT_READY", "READY") + "\nTodo en orden.\n")

    verified = cli.run(*D, "verify", dossier)
    status = cli.run(*D, "status", dossier)

    assert verified.exit_code == 1 and "dossier.md: no coincide" in verified.output
    assert status.exit_code == 1 and "dossier.md: no coincide" in status.output
    assert "ALTERADO" in cli.run(*D, "list").output
    assert cli.run(*D, "list").exit_code == 1


def test_borrar_el_indice_del_paquete_no_oculta_un_paquete_manipulado(cli):
    dossier = generated(cli)
    cli.run(*D, "package", dossier)
    files = cli.store.list(key(cli.store, dossier, "package"))
    measure = next(k for k in files if not k.endswith("index.json"))
    (cli.store._root / measure).write_text("{}")
    (cli.store._root / key(cli.store, dossier, "package/index.json")).unlink()

    result = cli.run(*D, "verify", dossier)

    assert result.exit_code == 1
    assert "index.json: no existe" in result.output


def test_sin_paquete_verify_no_dice_paquete_integro(cli):
    dossier = generated(cli)

    result = cli.run(*D, "verify", dossier, at=RECORDED)

    assert result.exit_code == 0, result.output
    assert "sin paquete" in result.output and "paquete íntegro" not in result.output


def test_un_paquete_regenerado_con_otro_contenido_no_se_verifica(cli):
    dossier = generated(cli)
    cli.run(*D, "package", dossier)
    path = next(
        cli.store._root / k
        for k in cli.store.list(key(cli.store, dossier, "package"))
        if not k.endswith("index.json")
    )

    path.write_text(path.read_text() + " ")

    assert cli.run(*D, "verify", dossier).exit_code == 1


def test_una_vista_guardada_y_editada_bloquea_verify_y_status(at_stage):
    cli, dossier = at_stage("self-assess")
    cli.run(*D, "show", dossier, "--save")
    saved = cli.store.list(key(cli.store, dossier, "view"))[0]
    (cli.store._root / saved).write_text("vista inventada")

    at = STEP_AT["self-assess"]
    assert cli.run(*D, "verify", dossier, at=at).exit_code == 1
    assert "la vista no coincide" in cli.run(*D, "status", dossier, at=at).output


def test_status_de_un_id_con_errata_no_manda_restaurar_la_copia(cli):
    result = cli.run(*D, "status", "20990101T000000000000Z-000000000000")

    assert result.exit_code == 1
    assert "No hay ningún expediente con ese id" in result.output
    assert "cadena rota" not in result.output and "copia" not in result.output


def test_status_de_un_dossier_json_alterado_dice_instantanea_y_no_cadena(cli):
    dossier = generated(cli)
    edit(cli, dossier, "dossier.json", "{}")

    result = cli.run(*D, "status", dossier)

    assert result.exit_code == 1
    assert "instantánea" in result.output and "cadena rota" not in result.output


def test_self_assess_solo_ofrece_los_dos_roles_validos(cli):
    dossier = generated(cli)

    result = cli.run(*D, "self-assess", dossier, "--by", "Ana", "--role", "management")

    assert result.exit_code == 2
    assert "system_administrator_delegate" in result.output
    assert "security_officer" not in result.output


def test_un_expediente_no_preparado_lo_avisa_al_generar_y_en_status(cli):
    result = cli.run(*D, "generate")
    dossier = ID.search(result.output).group()

    status = cli.run(*D, "status", dossier, at=RECORDED)

    assert "El sistema no estaba preparado al generar" in result.output
    assert "El sistema no estaba preparado al generar" in status.output


def test_status_y_generate_listan_las_secciones_incompletas_con_numero_y_nombre(cli):
    result = cli.run(*D, "generate")
    dossier = ID.search(result.output).group()

    status = cli.run(*D, "status", dossier, at=RECORDED)

    assert re.search(r"Secciones incompletas: .*\d+\. \w", result.output)
    assert re.search(r"Secciones incompletas: .*\d+\. \w", status.output)


def test_status_tras_analizar_pide_completar_o_reconocer_las_secciones_incompletas(at_stage):
    cli, dossier = at_stage("analyze")
    at = STEP_AT["declare"]

    status = cli.run(*D, "status", dossier, at=at)
    refused = cli.run(*D, "declare", dossier, "--by", DIRECTION, at=at)

    assert "Secciones incompletas: 4. Riesgos" in status.output
    assert refused.exit_code == 1 and "secciones incompletas" in refused.output
