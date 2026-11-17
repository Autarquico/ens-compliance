"""`/copias` de punta a punta: la app real y las plantillas reales, sin sustituir nada.

Un escenario por bloque (docs/specs/ui-copias.md) más las decisiones C1-C9 del plan. Solo el
reloj es una frontera; todo lo demás corre de verdad.
"""

import re
from dataclasses import replace
from datetime import timedelta
from html import unescape

import pytest
import yaml

from ens_evidence.backup.records import BackupRun, dump
from ens_evidence.backup.runner import backup_status
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles
from ens_evidence.ui.store import UiStore

from .conftest import NOW, Session, auth, snapshot_of, ui_client
from .test_copias_view import (
    CONFIG,
    SNAP,
    check,
    declaration,
    evidence,
    run,
    write_config,
    write_restore,
    write_test,
)
from .test_inventory import FORBIDDEN, favorable, text

HOSTILE = "<script>alert(1)</script>"
HOST = "copias.example.org"
CHECKS = (
    "backups.backup_001_recent",
    "backups.backup_003_repository_accessible",
    "backups.backup_004_integrity",
    "backups.backup_005_restore_test",
    "backups.backup_006_offsite",
)


@pytest.fixture
def web(config, example_store, key, token) -> Session:
    deployed = replace(config, kit_hosts={"copias": HOST})
    return Session(ui_client(deployed, example_store, key), token)


def seed(store, statuses=None, *, items=None, complete=True):
    """Configuración, evidencia y resultados BACKUP-001/003/004/005/006 de `caja`."""
    write_config(store)
    manifest = evidence(store, items=items)
    statuses = statuses or {}
    run(
        store,
        [check(manifest, statuses.get(c, CheckStatus.PASS), c) for c in CHECKS],
        complete=complete,
    )
    return manifest


def block_of(html: str) -> str:
    start = html.index('<article class="card"')
    return html[start : html.index("</article>", start)]


def guide_yaml(html: str) -> dict:
    pre = re.search(r'<pre class="mono">(.*?)</pre>', html, re.DOTALL)
    return yaml.safe_load(unescape(pre.group(1)))


def header_of(block: str) -> str:
    """La insignia y la frase de resumen del bloque, antes de los datos."""
    return block[block.index("</h3>") : block.index("<dl")]


def assert_nothing_favourable(html: str) -> None:
    assert "tone-ok" not in html
    assert not favorable(html)


# --- Scenario: repositorio al día --------------------------------------------------------------


def test_se_ve_la_ultima_copia_los_cinco_resultados_con_fuente_y_fecha_y_la_caducidad_de_la_prueba(
    web, example_store
):
    seed(example_store)
    write_test(example_store, declaration())
    write_restore(example_store)

    page = web.get("/copias")

    assert page.status_code == 200
    body = text(page.text)
    assert "2026-11-08 10:00 UTC" in body and "hace 2 h" in body
    for check_id in CHECKS:
        assert check_id in page.text
    assert page.text.count("2026-11-08 11:59 UTC") >= 1
    assert re.search(r"backups\s*·\s*2026-11-08 11:59 UTC", body)
    assert "Caducidad declarada: 2027-02-01 11:00 UTC" in " ".join(body.split())
    assert "Con problemas" not in page.text


def test_nada_dice_que_la_medida_se_cumpla(web, example_store):
    seed(example_store)
    write_test(example_store, declaration())
    write_restore(example_store)

    page = web.get("/copias").text

    assert "Esta vista no declara conformidad." in page
    assert "Revisión humana" in page
    assert not FORBIDDEN.search(page)
    assert "Sí, según el registro citado" in page  # tono neutro: es lo que dice el registro
    assert re.search(r'tone-neutral"[^>]*>.{0,300}?Sí, según el registro citado', page, re.S)


def test_la_pagina_se_marca_como_activa_en_la_navegacion(web):
    page = web.get("/copias").text

    assert re.search(r'href="/copias"[^>]*aria-current="page"', page)
    assert "Copias" in page


# --- Scenario: copia que dejó de hacerse -------------------------------------------------------


def test_backup_001_en_fail_marca_el_bloque_como_problema_y_ofrece_backrest_y_el_runbook(
    web, example_store
):
    seed(example_store, {CHECKS[0]: CheckStatus.FAIL}, items=[])

    page = web.get("/copias").text
    block = block_of(page)

    assert "Con problemas" in block and "tone-bad" in block
    assert "tone-ok" not in header_of(block)
    assert re.search(r"backup_001_recent</span>.*?<span class=\"badge-code\">FAIL", block, re.S)
    assert 'href="https://copias.example.org"' in block and ">Abrir Backrest<" in block
    assert 'href="/ayuda/kit-copias"' in block
    assert not favorable(header_of(block))


def test_backup_001_en_fail_sin_snapshots_no_inventa_antiguedad(web, example_store):
    seed(example_store, {CHECKS[0]: CheckStatus.FAIL}, items=[])

    body = text(web.get("/copias").text)

    assert "No consta la fecha del último snapshot" in body
    assert "hace " not in body.split("Último snapshot en el repositorio")[1].split("Última")[0]
    assert "no de una copia" in body  # la fecha del check está rotulada como tal


# --- Scenario: sin prueba de restauración ------------------------------------------------------


def test_sin_prueba_dice_que_no_hay_prueba_muestra_la_guia_y_no_da_el_repositorio_por_resuelto(
    web, example_store
):
    seed(example_store, {CHECKS[3]: CheckStatus.UNKNOWN})
    page = web.get("/copias").text
    block = block_of(page)

    assert "Sin prueba vigente" in block
    assert "--repository caja" in unescape(block)
    declared = guide_yaml(block)
    assert declared["system"] == "bk-diario"
    assert declared["review"]["reviewer"].startswith("<")
    assert re.search(r"backup_005_restore_test</span>.*?badge-code\">UNKNOWN", block, re.S)
    assert not favorable(header_of(block))
    assert "No hay pruebas de restauración registradas" in page


# --- Scenario: copia propia de la caja interrumpida --------------------------------------------


def test_la_caja_muestra_la_ejecucion_sin_registro_final_como_interrumpida(web, example_store):
    started = BackupRun(
        run_id="run-1",
        status="started",
        started_at=NOW - timedelta(hours=1),
        repository="s3:https://u:p@s3.example.test/b",
    )
    example_store.write_bytes("evidence/backups/run-1.yaml", dump(started))

    page = web.get("/copias").text
    interrupted = backup_status(UiStore(example_store)).interrupted

    assert [r.run_id for r in interrupted] == ["run-1"]
    assert "interrumpida" in page and "run-1" in page
    assert "Hay copias propias interrumpidas" in page
    assert "u:p@" not in page and "s3.example.test" not in page


def test_el_error_de_una_copia_propia_fallida_nunca_llega_a_la_pagina(web, example_store):
    failed = BackupRun(
        run_id="run-2",
        status="failed",
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=2),
        repository="s3:x",
        error="SECRETO-DEL-ERROR",
    )
    example_store.write_bytes("evidence/backups/run-2.yaml", dump(failed))

    page = web.get("/copias").text

    assert "run-2" in page and "SECRETO-DEL-ERROR" not in page


# --- Scenario: sin copias configuradas ---------------------------------------------------------


def test_sin_configuracion_la_pagina_lo_explica_enlaza_al_asistente_y_nada_es_favorable(web):
    page = web.get("/copias").text

    assert "No hay copias configuradas" in page
    assert 'href="/herramientas/copias"' in page
    assert_nothing_favourable(page)


def test_con_configuracion_ilegible_dice_que_no_se_puede_leer_y_nada_es_favorable(
    web, example_store
):
    example_store.write_text("connectors/backups.yaml", "repositories: [SECRETO")

    page = web.get("/copias").text

    assert "connectors/backups.yaml no es válido" in unescape(page)
    assert 'href="/herramientas/copias"' in page
    assert_nothing_favourable(page)


# --- Scenario: solo lectura --------------------------------------------------------------------


def test_con_o_sin_permiso_de_edicion_se_ve_lo_mismo(web, example_store):
    seed(example_store)
    write_test(example_store, declaration())

    example_store.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(True, frozenset())}))
    editor = web.get("/copias").text
    example_store.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(False, frozenset())}))
    reader = web.get("/copias").text

    def body(html):
        return html[html.index("<h1>") : html.index("</main>")]

    assert body(editor) == body(reader)


def test_la_pagina_no_tiene_ninguna_accion_que_escriba_y_no_cambia_el_workspace(web, example_store):
    seed(example_store)
    before = snapshot_of(example_store)

    page = web.get("/copias")

    assert "<form" not in page.text and 'method="post"' not in page.text
    assert web.post("/copias", {}).status_code in (404, 405)
    assert snapshot_of(example_store) == before


def test_la_tarjeta_de_copias_en_herramientas_enlaza_al_estado(web):
    page = web.get("/herramientas").text

    assert 'href="/copias"' in page and "Ver el estado de las copias" in page


# --- K6: cobertura parcial ---------------------------------------------------------------------


def test_una_ejecucion_incompleta_no_da_por_completa_la_cobertura(web, example_store):
    seed(example_store, complete=False)

    page = web.get("/copias").text

    assert "Cobertura parcial" in page
    assert "Resultados incompletos" in page
    assert not favorable(page)


# --- C1: artefacto no fiable -------------------------------------------------------------------


def corrupt(store, manifest, data: bytes):
    store.write_bytes(manifest.artifact.key, data)


@pytest.mark.parametrize(
    "data",
    [b"{no es json", b"{}", b'{"source":"backups","collector":"backups","data":[]}'],
    ids=["malformado", "vacio", "forma-ajena"],
)
def test_un_artefacto_ilegible_cae_a_la_fecha_del_check_sin_antiguedad_de_copia(
    web, example_store, data
):
    manifest = seed(example_store)
    corrupt(example_store, manifest, data)

    body = text(web.get("/copias").text)

    assert "No consta la fecha del último snapshot" in body
    assert "hace 2 h" not in body
    assert "2026-11-08 11:59 UTC" in body and "no de una copia" in body


def test_un_artefacto_con_hash_incorrecto_no_se_lee(web, example_store):
    manifest = seed(example_store)
    original = example_store.read_bytes(manifest.artifact.key)
    corrupt(example_store, manifest, original.replace(b"SUCCESS", b"SUCCESz", 1))

    assert "No consta la fecha del último snapshot" in text(web.get("/copias").text)


def test_un_artefacto_de_otro_repositorio_no_se_atribuye_a_este(web, example_store):
    write_config(example_store)
    manifest = evidence(example_store, repo_id="otro", asset_id="otro-activo")
    run(example_store, [check(manifest)])

    assert "No consta la fecha del último snapshot" in text(web.get("/copias").text)


def test_un_manifiesto_con_clave_ajena_no_lee_otros_ficheros_del_workspace(web, example_store):
    manifest = seed(example_store)
    key = f"evidence/manifests/{manifest.id}.yaml"
    example_store.write_text(
        key, example_store.read_text(key).replace(manifest.artifact.key, "scope/system.yaml")
    )

    page = web.get("/copias").text

    assert "No consta la fecha del último snapshot" in text(page)
    assert "Taller Norte" not in page


# --- C2: la declaración no resuelve nada -------------------------------------------------------


def rejected(test):
    return test.model_copy(update={"review": test.review.model_copy(update={"approved": False})})


def future(test):
    return test.model_copy(update={"performed_at": NOW + timedelta(days=30)})


@pytest.mark.parametrize(
    "make",
    [rejected, future],
    ids=["rechazada", "fechada-en-el-futuro"],
)
def test_una_prueba_rechazada_o_futura_nunca_se_presenta_como_resuelta(web, example_store, make):
    seed(example_store, {CHECKS[3]: CheckStatus.UNKNOWN})
    write_test(example_store, make(declaration()))
    write_restore(example_store)

    page = web.get("/copias")

    assert page.status_code == 200
    assert re.search(r"backup_005_restore_test</span>.*?badge-code\">UNKNOWN", page.text, re.S)
    assert "tone-ok" not in header_of(block_of(page.text))
    assert (
        "Lo que vale es el resultado de BACKUP-005" in page.text or "no se pudo leer" in page.text
    )


def test_una_prueba_sin_vinculo_con_su_registro_queda_sin_verificar(web, example_store):
    seed(example_store, {CHECKS[3]: CheckStatus.UNKNOWN})
    write_test(example_store, declaration(restore_id="R-AUSENTE"))

    page = web.get("/copias").text

    assert "Sin verificar" in page and "Sí, según el registro citado" not in page
    assert "tone-ok" not in header_of(block_of(page))


# --- C3: coincidencia --------------------------------------------------------------------------


def test_un_registro_citado_de_otro_snapshot_o_repositorio_es_sin_verificar(web, example_store):
    seed(example_store)
    write_test(example_store, declaration(restore_id="R-1"))
    write_test(example_store, declaration("RT-2", restore_id="R-2"))
    write_restore(example_store, "R-1", snapshot="e" * 64)
    write_restore(example_store, "R-2", repository_id="f" * 64)

    page = web.get("/copias").text

    assert page.count("Sin verificar") == 2
    assert "Sí, según el registro citado" not in page


def test_una_prueba_con_su_registro_coincidente_se_muestra_como_declarada_no_como_veredicto(
    web, example_store
):
    seed(example_store)
    write_test(example_store, declaration())
    write_restore(example_store)

    page = web.get("/copias").text

    assert "Sí, según el registro citado" in page
    assert "La prueba la hace y registra una persona" in page


# --- C4: secretos en la configuración ----------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "repositories:\n- {id: caja, asset_id: x, repository: 'SECRETO-VALOR https://u:p@h/x'}\n",
        "SECRETO-CLAVE: 1\n",
        "'https://u:p@h/SECRETO-CLAVE': 1\n",
        "repositories: [SECRETO-VALOR https://u:p@h/x",
    ],
    ids=["valor", "clave-desconocida", "clave-uri", "yaml-roto"],
)
def test_un_secreto_o_una_uri_en_la_configuracion_nunca_llega_al_html(web, example_store, content):
    example_store.write_text("connectors/backups.yaml", content)

    page = web.get("/copias").text

    assert "SECRETO" not in page and "u:p@" not in page and "https://h/" not in page
    assert "connectors/backups.yaml no es válido" in unescape(page)


def test_la_uri_del_repositorio_no_se_muestra(web, example_store):
    seed(example_store)

    page = web.get("/copias").text

    assert CONFIG["repositories"][0]["repository"] not in unescape(page)
    assert "s3.example.test" not in page


# --- C6: ausencia ------------------------------------------------------------------------------


def test_configuracion_sin_ninguna_ejecucion_no_da_nada_por_bueno(web, example_store):
    write_config(example_store)

    page = web.get("/copias").text
    body = text(page)

    assert "Sin resultados todavía" in body and "Sin evidencia" in body
    assert "No consta la fecha del último snapshot" in body
    assert_nothing_favourable(page)


# --- C8: valores hostiles ----------------------------------------------------------------------


@pytest.mark.parametrize("hostile", [HOSTILE, "a\nb: 1", "x'\"y: z", "k: v #c"])
def test_un_activo_hostil_se_escapa_y_la_guia_sigue_siendo_yaml_de_un_solo_campo(
    web, example_store, hostile
):
    config = {"repositories": [{**CONFIG["repositories"][0], "asset_id": hostile}]}
    write_config(example_store, config)

    page = web.get("/copias").text

    assert HOSTILE not in page
    declared = guide_yaml(page)
    assert declared["system"] == hostile
    assert set(declared) == {
        "test_id",
        "system",
        "performed_at",
        "isolated_target",
        "backup",
        "result",
        "review",
    }


def test_un_run_id_hostil_de_la_caja_se_escapa(web, example_store):
    started = BackupRun.model_construct(
        run_id=HOSTILE,
        status="started",
        started_at=NOW - timedelta(hours=1),
        repository="s3:x",
        finished_at=None,
        snapshot_id=None,
        error=None,
    )
    example_store.write_bytes("evidence/backups/hostil.yaml", dump(started))

    page = web.get("/copias")

    assert page.status_code == 200
    assert HOSTILE not in page.text


def test_un_snapshot_id_hostil_no_llega_sin_escapar(web, example_store):
    seed(example_store)
    write_test(example_store, declaration())
    forged = example_store.read_text("evidence/restore-tests/RT-1.yaml").replace(SNAP, HOSTILE)
    example_store.write_text("evidence/restore-tests/RT-1.yaml", forged)
    write_restore(example_store, snapshot=HOSTILE)
    completed = BackupRun.model_construct(
        run_id="run-h",
        status="completed",
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        repository="s3:x",
        snapshot_id=HOSTILE,
        error=None,
    )
    example_store.write_bytes("evidence/backups/run-h.yaml", dump(completed))

    page = web.get("/copias")

    assert page.status_code == 200
    assert HOSTILE not in page.text


# --- C9: el enlace a Backrest ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "headers"),
    [
        ("", {}),
        ("?host=evil.example&backrest=https://evil.example", {}),
        ("", {"X-Forwarded-Host": "evil.example"}),
        (
            "?kit_hosts=evil.example",
            {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"},
        ),
    ],
)
def test_el_enlace_a_backrest_no_cambia_con_la_query_ni_con_cabeceras_de_host(
    raw_client_deployed, token, query, headers
):
    page = raw_client_deployed.get(f"/copias{query}", headers={**auth(token), **headers})

    assert page.status_code == 200
    assert 'href="https://copias.example.org"' in page.text
    assert "evil.example" not in page.text


def test_otro_host_en_la_peticion_no_se_convierte_en_el_enlace(raw_client_deployed, token):
    page = raw_client_deployed.get("/copias", headers={**auth(token), "Host": "evil.example"})

    assert "evil.example" not in page.text
    assert 'href="https://evil' not in page.text


def test_sin_host_configurado_no_hay_enlace_y_se_dice(raw_client, token):
    page = raw_client.get(
        "/copias", headers={**auth(token), "X-Forwarded-Host": "evil.example"}
    ).text

    assert "Backrest no está desplegado en esta caja" in page
    assert ">Abrir Backrest<" not in page and "evil.example" not in page
    assert 'href="/herramientas/copias"' in page


@pytest.fixture
def raw_client_deployed(config, example_store, key):
    return ui_client(replace(config, kit_hosts={"copias": HOST}), example_store, key)


def test_una_prueba_rechazada_dice_que_su_revision_no_la_aprueba(web, example_store):
    seed(example_store, {CHECKS[3]: CheckStatus.UNKNOWN})
    write_test(example_store, rejected(declaration()))
    write_restore(example_store)

    assert "la marca como no aprobada" in block_of(web.get("/copias").text)
