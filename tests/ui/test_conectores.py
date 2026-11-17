"""`/conectores` de punta a punta: la app real y las plantillas reales, sin sustituir nada.

Un bloque por escenario de docs/specs/ui-conectores.md más las decisiones D1-D12 del plan. Solo el
reloj es una frontera; todo lo demás corre de verdad. El inventario de ejemplo trae `gh-taller`
(code_hosting) donde la spec dice `gh-org`.
"""

import hashlib
import json
import logging
import re
from html import unescape

import pytest
import yaml

from ens_evidence.connectors.config import load_connector_config
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.evidence.importer import import_collected
from ens_evidence.evidence.layout import artifact_key, manifest_key
from ens_evidence.evidence.manifests import render_manifest
from ens_evidence.models.contracts import (
    Artifact,
    CheckResult,
    CollectionResult,
    CollectorInfo,
    EvidenceManifest,
    Provenance,
    ProvenanceKind,
)
from ens_evidence.models.enums import CheckStatus
from ens_evidence.scope.inventory import ASSETS_KEY
from ens_evidence.ui.roles import ROLES_KEY, Grant, dump_roles
from ens_evidence.ui.views import conectores_catalogo

from .conftest import NOW, Session, snapshot_of, ui_client
from .test_inventory import FORBIDDEN, assert_no_write_controls, favorable, record, result, text

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
HOSTILE = "<script>alert(1)</script>"
GITHUB_FILE = "connectors/github.yaml"
GITHUB = {"asset_id": "gh-taller", "organization": "acme"}
CLOUDFLARE_ACCOUNT = "a" * 32
CLOUDFLARE = {"asset_id": "cf-taller", "account_id": CLOUDFLARE_ACCOUNT}
ROUTE = {"hostname": "api.example.org", "path": "/agent/*", "reason": "ruta pública por diseño"}
OFFSITE = {
    "location": "Otro proveedor",
    "administrative_separation": "Cuenta distinta",
    "reviewer": "Ana Ruiz",
    "role": "system_owner",
    "reviewed_at": "2026-10-01T10:00:00+00:00",
    "valid_until": "2027-10-01T10:00:00+00:00",
}
RESTIC = {
    "asset_id": "bk-diario",
    "max_backup_age_hours": "26",
    "max_check_age_days": "35",
    "restore_test_validity_days": "90",
    "min_check_read_data_percent": "5",
}
GWS = {
    "asset_id": "ws-identidad",
    "admin_subject": "ana@taller.example.org",
    "domains": "taller.example.org",
}
REPOSITORY = {
    "repository": "local:/srv/copias",
    "max_backup_age_hours": 26,
    "max_check_age_days": 35,
    "restore_test_validity_days": 90,
}
KEYSTORE = {"asset_id": "app-facturas", "max_key_age_days": "90"}
CONFIGS = {
    "github": GITHUB,
    "cloudflare": CLOUDFLARE,
    "google-workspace": GWS,
    "restic": RESTIC,
}


@pytest.fixture
def web(config, example_store, key, token) -> Session:
    return Session(ui_client(config, example_store, key), token)


@pytest.fixture
def readonly(config, example_store, key, token) -> Session:
    example_store.write_text(ROLES_KEY, dump_roles({"ana@example.com": Grant(False, frozenset())}))
    return Session(ui_client(config, example_store, key), token)


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def logged(caplog) -> str:
    return "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records)


def audit_lines(caplog, outcome: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "ens_evidence.ui.audit" and f"outcome={outcome}" in r.getMessage()
    ]


def block_of(html: str, name: str) -> str:
    """El bloque de un conector en el listado: su `<article>` con ese enlace de ficha."""
    for match in re.finditer(r"<article\b.*?</article>", html, re.DOTALL):
        if f'href="/conectores/{name}"' in match.group(0):
            return match.group(0)
    raise AssertionError(f"sin bloque para {name}")


def collect(store, name, status, data=None, asset="gh-taller"):
    return import_collected(
        store,
        CollectionResult(
            source=name,
            collector=name,
            collected_at=NOW,
            asset_id=asset,
            data=data or {},
            collection_status=status,
        ),
        collector_version="1",
        now=NOW,
    )


def put_raw(store, raw: bytes, *, name="github", asset="gh-taller"):
    """Un manifiesto de recogida cuyo artefacto es `raw` tal cual, con el hash correcto."""
    sha = hashlib.sha256(raw).hexdigest()
    store.write_bytes(artifact_key(sha), raw)
    manifest = EvidenceManifest(
        id=f"EV-20261108-{sha[:8]}",
        source=name,
        asset_id=asset,
        collected_at=NOW,
        artifact=Artifact(storage="workspace", key=artifact_key(sha), sha256=sha),
        provenance=Provenance(
            kind=ProvenanceKind.COLLECTOR, submitted_by="collector:x", submitted_at=NOW
        ),
        collector=CollectorInfo(name=name, version="1"),
    )
    store.write_bytes(manifest_key(manifest.id), render_manifest(manifest))


def artifact_document(data, *, status="SUCCESS", name="github", asset="gh-taller") -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0",
            "source": name,
            "collector": name,
            "collected_at": NOW.isoformat(),
            "asset_id": asset,
            "data": data,
            "collection_status": status,
        }
    ).encode()


def denied(check_id: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        asset_id="gh-taller",
        status=CheckStatus.UNKNOWN,
        evaluated_at=NOW,
        reason="sin permiso",
        collection_status="PERMISSION_DENIED",
        reason_code="permission_denied",
    )


def assert_nothing_favourable(html: str) -> None:
    assert "tone-ok" not in html
    assert not favorable(html)


# --- Scenario: conector sin configurar ---------------------------------------------------------


def test_github_sin_configurar_muestra_la_variable_la_guia_y_el_boton_sin_nada_favorable(web):
    page = web.get("/conectores")

    assert page.status_code == 200
    block = block_of(page.text, "github")
    assert "Sin configurar" in block
    assert "ENS_GITHUB_TOKEN" in block and "deploy/.env" in block
    assert 'href="/ayuda/conector-github"' in block
    assert re.search(r'<a class="btn btn-primary" href="/conectores/github">Configurar</a>', block)
    assert_nothing_favourable(page.text)
    assert not FORBIDDEN.search(page.text)


def test_el_listado_trae_un_bloque_por_cada_conector_registrado_y_ninguno_de_endpoint(web):
    page = web.get("/conectores").text

    for name in ("backups", "cloudflare", "github", "google-workspace", "keystore", "passwords"):
        assert f'href="/conectores/{name}"' in page
    assert 'href="/conectores/restic"' in page
    assert 'href="/conectores/endpoint"' not in page


def test_la_pagina_se_marca_como_activa_en_la_navegacion(web):
    page = web.get("/conectores").text

    assert re.search(r'href="/conectores"[^>]*aria-current="page"', page)


# --- Scenario: configurar un conector ------------------------------------------------------------


def test_guardar_github_escribe_un_fichero_valido_lo_audita_y_el_bloque_pasa_a_configurado(
    web, example_store, audit
):
    response = web.post("/conectores/github", GITHUB)

    assert response.status_code == 303
    assert response.headers["location"] == "/conectores?hecho=github"
    loaded = load_connector_config(example_store, "github", GitHubConfig)
    assert (loaded.asset_id, loaded.organization) == ("gh-taller", "acme")
    [line] = audit_lines(audit, "saved")
    assert "step=conectores action=configurar asset=github" in line and GITHUB_FILE in line
    assert "acme" not in line

    page = web.get("/conectores").text
    block = block_of(page, "github")
    assert "Configurado" in block and "Sin recogidas todavía" in block
    assert_nothing_favourable(block)


def test_guardar_lo_mismo_otra_vez_no_escribe_y_lo_dice(web, example_store):
    web.post("/conectores/github", GITHUB)
    before = snapshot_of(example_store)

    response = web.post("/conectores/github", GITHUB)

    assert response.headers["location"] == "/conectores?hecho=sin-cambios"
    assert snapshot_of(example_store) == before


def test_el_aviso_de_guardado_solo_acepta_un_conector_editable(web):
    assert "No había nada que cambiar" in web.get("/conectores?hecho=sin-cambios").text
    assert HOSTILE not in web.get(f"/conectores?hecho={HOSTILE}").text


def test_la_ficha_de_un_conector_configurado_se_precarga(web):
    web.post("/conectores/github", GITHUB)

    page = web.get("/conectores/github").text

    assert 'value="acme"' in page
    assert "gh-taller" in page


@pytest.mark.parametrize(
    ("conector", "form"),
    [
        ("github", GITHUB),
        ("cloudflare", CLOUDFLARE | {"token_kind": "user", "ui_hostname": "panel.example.org"}),
        ("google-workspace", GWS | {"customer": "my_customer"}),
        ("restic", RESTIC),
    ],
)
def test_cada_conector_editable_guarda_un_fichero_que_el_cargador_acepta(
    web, example_store, conector, form
):
    assert web.post(f"/conectores/{conector}", form).status_code == 303

    spec = conectores_catalogo.CATALOGUE[conector]
    assert load_connector_config(example_store, conector, spec.model)


def test_keystore_se_guarda_y_el_cargador_lo_acepta(web, example_store):
    form = KEYSTORE | {"address": "https://keys.example.org:8200", "max_token_period_hours": "48"}

    assert web.post("/conectores/keystore", form).status_code == 303

    spec = conectores_catalogo.CATALOGUE["keystore"]
    assert load_connector_config(example_store, "keystore", spec.model).max_token_period_hours == 48


def test_un_token_en_max_token_period_hours_se_rechaza(web, example_store):
    before = snapshot_of(example_store)
    form = KEYSTORE | {"max_token_period_hours": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}

    page = web.post("/conectores/keystore", form)

    assert page.status_code == 422
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in page.text
    assert snapshot_of(example_store) == before


def test_passwords_guarda_con_enteros_estrictos(web, example_store):
    form = {"asset_id": "app-facturas", "inactive_days": "7", "max_export_age_minutes": "30"}

    assert web.post("/conectores/passwords", form).status_code == 303

    saved = yaml.safe_load(example_store.read_text("connectors/passwords.yaml"))
    assert saved["inactive_days"] == 7 and isinstance(saved["inactive_days"], int)


def test_el_enlace_con_guion_de_google_workspace_funciona(web, example_store):
    assert web.get("/conectores/google-workspace").status_code == 200
    assert web.post("/conectores/google-workspace", GWS).status_code == 303
    assert example_store.exists("connectors/google-workspace.yaml")


def test_un_administrador_fuera_de_los_dominios_da_un_error_general_y_no_escribe(
    web, example_store
):
    before = snapshot_of(example_store)

    response = web.post("/conectores/google-workspace", GWS | {"admin_subject": "ana@otro.org"})

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    assert "no es válida" in response.text


def test_un_entero_que_no_lo_es_da_un_error_en_su_campo(web, example_store):
    before = snapshot_of(example_store)

    response = web.post("/conectores/restic", RESTIC | {"max_backup_age_hours": "x"})

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    assert 'role="alert"' in response.text or "field-error" in response.text


def test_un_activo_de_otro_tipo_se_rechaza_en_su_campo(web, example_store):
    before = snapshot_of(example_store)

    response = web.post("/conectores/github", GITHUB | {"asset_id": "lap-ana"})

    assert response.status_code == 422
    assert "Alojamiento de código" in unescape(response.text)
    assert snapshot_of(example_store) == before


def test_un_conector_que_no_existe_o_sin_entry_point_da_404(web):
    assert web.get("/conectores/endpoint").status_code == 404  # D12
    assert web.get("/conectores/inventado").status_code == 404


# --- Scenario: el formulario rechaza un secreto (D1) ---------------------------------------------

SECRET_CASES = [
    ("github", GITHUB, "organization"),  # texto
    ("restic", RESTIC, "max_backup_age_hours"),  # entero
    ("cloudflare", CLOUDFLARE, "token_kind"),  # opción
    ("google-workspace", GWS, "domains"),  # líneas
    ("github", GITHUB, "asset_id"),  # activo
]


@pytest.mark.parametrize(("conector", "base", "campo"), SECRET_CASES)
def test_un_valor_con_forma_de_token_en_cualquier_campo_da_422_y_no_se_repite_ni_se_escribe(
    web, example_store, audit, conector, base, campo
):
    before = snapshot_of(example_store)

    response = web.post(f"/conectores/{conector}", {**base, campo: TOKEN})

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    assert TOKEN not in response.text and "ghp_" not in response.text
    assert TOKEN not in logged(audit)
    assert "parece contener un secreto" in response.text
    assert audit_lines(audit, "saved") == []


@pytest.mark.parametrize(("conector", "base", "campo"), SECRET_CASES)
def test_con_el_inventario_ilegible_el_422_tampoco_repite_un_valor_con_forma_de_token(
    web, example_store, audit, conector, base, campo
):
    example_store.write_text(ASSETS_KEY, "assets: [\n")  # YAML roto: el inventario no se lee
    before = snapshot_of(example_store)

    response = web.post(f"/conectores/{conector}", {**base, campo: TOKEN})

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    assert TOKEN not in response.text and "ghp_" not in response.text
    assert TOKEN not in logged(audit)


def test_un_secreto_oculto_en_una_linea_de_varias_no_se_repite_ni_se_escribe(
    web, example_store, audit
):
    before = snapshot_of(example_store)

    response = web.post(
        "/conectores/google-workspace", GWS | {"domains": f"taller.example.org\n{TOKEN}"}
    )

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    assert TOKEN not in response.text and TOKEN not in logged(audit)


def test_una_clave_con_forma_de_credencial_enviada_a_mano_no_llega_al_fichero(web, example_store):
    web.post("/conectores/github", GITHUB | {"token": "x", "password": "y"})

    assert yaml.safe_load(example_store.read_text(GITHUB_FILE)) == GITHUB


def test_cloudflare_guarda_token_kind_que_el_cargador_permite(web, example_store):
    form = CLOUDFLARE | {"token_kind": "user"}

    assert web.post("/conectores/cloudflare", form).status_code == 303

    saved = yaml.safe_load(example_store.read_text("connectors/cloudflare.yaml"))
    assert saved["token_kind"] == "user"  # D2: «token_kind» no se confunde con una credencial


# --- Scenario: recogida con credencial ausente ---------------------------------------------------


def test_credencial_ausente_dice_la_variable_la_fecha_y_nunca_el_mensaje_en_bruto(
    web, example_store
):
    web.post("/conectores/github", GITHUB)
    collect(example_store, "github", "ERROR", {"error": "ENS_GITHUB_TOKEN no está definida"})

    block = block_of(web.get("/conectores").text, "github")

    body = " ".join(text(block).split())
    assert "Motivo: credencial ausente" in body
    assert "ENS_GITHUB_TOKEN" in body and "deploy/.env" in body
    assert "2026-11-08" in body
    assert "no está definida" not in body
    assert_nothing_favourable(block)


def test_un_mensaje_de_error_hostil_nunca_llega_a_la_pagina(web, example_store, audit):
    web.post("/conectores/github", GITHUB)
    collect(example_store, "github", "ERROR", {"error": f"{HOSTILE} {TOKEN}"})

    page = web.get("/conectores").text

    assert HOSTILE not in page and "&lt;script&gt;" not in page
    assert TOKEN not in page and TOKEN not in logged(audit)
    assert "Motivo: error del conector" in " ".join(text(page).split())


def test_un_codigo_del_planificador_con_un_secreto_no_repite_el_sufijo(web, example_store):
    web.post("/conectores/github", GITHUB)
    collect(example_store, "github", "ERROR", {"error": f"secret_detected:{TOKEN}"})

    page = web.get("/conectores").text

    assert TOKEN not in page and "ghp_" not in page


# --- Scenario: recogida parcial ------------------------------------------------------------------


def test_recogida_parcial_nombra_la_seccion_sin_permiso_y_los_checks_no_salen_como_resueltos(
    web, example_store
):
    web.post("/conectores/github", GITHUB)
    sections = {
        "repositories": {"status": "SUCCESS"},
        "branch_protection": {"status": "PERMISSION_DENIED", "reason": HOSTILE},
    }
    collect(example_store, "github", "SUCCESS", {"sections": sections})
    record(
        example_store,
        "github",
        [
            result("gh-taller", CheckStatus.PASS, "GITHUB-001"),
            denied("GITHUB-002"),
        ],
    )

    page = web.get("/conectores").text
    block = block_of(page, "github")

    assert "Parcial" in block or "parcial" in block
    assert "branch_protection" in block and "PERMISSION_DENIED" in block
    assert HOSTILE not in page
    assert "GITHUB-001" in block and "GITHUB-002" in block
    assert "permission_denied" in block and "Cobertura parcial" in block
    assert "tone-ok" not in block.split("Resultados de las comprobaciones", 1)[1].split("<table")[0]
    detail = web.get("/inventario/activos/gh-taller").text
    assert "GITHUB-001" in detail and "GITHUB-002" in detail


def test_una_recogida_completa_no_se_marca_parcial_ni_favorable(web, example_store):
    web.post("/conectores/github", GITHUB)
    collect(
        example_store, "github", "SUCCESS", {"sections": {"repositories": {"status": "SUCCESS"}}}
    )

    block = block_of(web.get("/conectores").text, "github")

    assert "Recogida parcial" not in block
    assert "no que se cumpla nada" in " ".join(text(block).split())


# --- D8: artefacto mal formado pero con el hash correcto --------------------------------------


def test_un_artefacto_mal_formado_con_hash_correcto_omite_las_secciones_y_lo_avisa(
    web, example_store
):
    web.post("/conectores/github", GITHUB)
    sections = {
        TOKEN: {"status": "SUCCESS"},
        "Mayusculas": {"status": "SUCCESS"},
        "buena": {"status": "SUCCESS"},
        "rara": {"status": f"MAGIA {HOSTILE}"},
        "sin_estado": {"reason": HOSTILE},
    }
    put_raw(example_store, artifact_document({"sections": sections}))

    page = web.get("/conectores").text

    block = block_of(page, "github")
    assert "buena" in block and "no legibles" in block
    assert TOKEN not in page and HOSTILE not in page and "Mayusculas" not in page
    assert "MAGIA" not in page


def test_un_artefacto_alterado_con_su_clave_no_se_lee_y_se_dice(web, example_store):
    web.post("/conectores/github", GITHUB)
    put_raw(example_store, artifact_document({"error": "x"}))
    manifest_id = next(k for k in example_store.list("") if k.endswith(".yaml") and "EV-" in k)
    stored = yaml.safe_load(example_store.read_text(manifest_id))
    example_store.write_bytes(stored["artifact"]["key"], b"{}")

    page = web.get("/conectores")

    assert page.status_code == 200
    assert "no se puede leer" in page.text.lower() or "no legible" in page.text.lower()


# --- Scenario: solo lectura ------------------------------------------------------------------


def test_un_lector_ve_el_mismo_estado_sin_botones_de_configurar(readonly, example_store):
    collect(example_store, "github", "ERROR", {"error": "ENS_GITHUB_TOKEN no está definida"})

    page = readonly.get("/conectores")

    assert page.status_code == 200
    assert "Motivo: credencial ausente" in " ".join(text(block_of(page.text, "github")).split())
    assert "ENS_GITHUB_TOKEN" in page.text
    assert ">Configurar<" not in page.text
    assert_no_write_controls(page.text)


def test_un_lector_no_ve_el_formulario_de_la_ficha(readonly):
    page = readonly.get("/conectores/github").text

    assert 'method="post"' not in page
    assert "Solo lectura" in page


def test_un_post_de_un_lector_da_403_auditado_y_no_escribe(readonly, example_store, audit):
    before = snapshot_of(example_store)

    response = readonly.post("/conectores/github", GITHUB)

    assert response.status_code == 403
    assert snapshot_of(example_store) == before
    [line] = audit_lines(audit, "denied")
    assert "path=/conectores/github" in line and "acme" not in line


def test_un_post_a_backups_da_403_incluso_para_un_editor(web, example_store, audit):
    before = snapshot_of(example_store)

    response = web.post("/conectores/backups", {"repositories": "x"})

    assert response.status_code == 403
    assert snapshot_of(example_store) == before
    assert "reason=unknown_operation" in audit_lines(audit, "denied")[0]


# --- D3: backups de solo lectura con estado por repositorio ------------------------------------


def test_backups_se_muestra_sin_formulario_con_el_estado_de_cada_repositorio(web, example_store):
    example_store.write_text(
        "connectors/backups.yaml",
        yaml.safe_dump(
            {
                "repositories": [
                    {**REPOSITORY, "id": "caja", "asset_id": "bk-diario"},
                    {**REPOSITORY, "id": "nube", "asset_id": "bk-externo"},
                ]
            }
        ),
    )
    data = {
        "repositories": [
            {"id": "caja", "collection_status": "SUCCESS", "error": None},
            {"id": "nube", "collection_status": "ERROR", "error": f"{HOSTILE} {TOKEN}"},
            {"id": "ajeno", "collection_status": "ERROR", "error": None},
        ]
    }
    collect(example_store, "backups", "ERROR", data, asset="bk-diario")

    listing = web.get("/conectores").text
    detail = web.get("/conectores/backups")

    block = block_of(listing, "backups")
    assert "caja" in block and "nube" in block and "ajeno" not in block
    assert "SUCCESS" in block and "ERROR" in block
    assert 'href="/copias"' in block and 'href="/herramientas/copias"' in block
    assert HOSTILE not in listing and TOKEN not in listing
    assert detail.status_code == 200 and 'action="/conectores/backups"' not in detail.text
    assert ">Configurar<" not in block


# --- Scenario: configuración inválida en disco (D6, D7) ---------------------------------------

BROKEN_CLOUDFLARE = (
    "asset_id: cf-taller\n"
    "account_id: NO-ES-UNA-CUENTA-SECRETO-DEL-FICHERO\n"
    "clave_ajena_del_fichero: valor-ajeno-del-fichero\n"
    f"public_routes:\n- hostname: {ROUTE['hostname']}\n  path: {ROUTE['path']}\n"
    f"  reason: {ROUTE['reason']}\n"
)
FILE_VALUES = ("NO-ES-UNA-CUENTA", "clave_ajena_del_fichero", "valor-ajeno-del-fichero")


def test_un_fichero_invalido_dice_configuracion_no_valida_sin_valores_del_fichero(
    web, example_store
):
    example_store.write_text("connectors/cloudflare.yaml", BROKEN_CLOUDFLARE)

    listing = web.get("/conectores").text
    form = web.get("/conectores/cloudflare").text

    assert "Configuración no válida" in block_of(listing, "cloudflare")
    for page in (listing, form):
        for value in FILE_VALUES:
            assert value not in page


def test_un_fichero_invalido_deja_el_formulario_vacio_y_pide_confirmacion(web, example_store):
    example_store.write_text("connectors/cloudflare.yaml", BROKEN_CLOUDFLARE)

    form = web.get("/conectores/cloudflare").text

    assert "confirmar_sobrescritura" in form
    assert 'value="cf-taller" selected' not in form and CLOUDFLARE_ACCOUNT not in form
    assert not re.search(r'name="account_id"[^>]*value="[^"]', form)


def test_guardar_sobre_un_invalido_sin_confirmar_da_422_y_deja_el_fichero(web, example_store):
    example_store.write_text("connectors/cloudflare.yaml", BROKEN_CLOUDFLARE)
    before = snapshot_of(example_store)

    response = web.post("/conectores/cloudflare", CLOUDFLARE)

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    for value in FILE_VALUES:
        assert value not in response.text


def test_confirmado_se_sustituye_y_se_conserva_public_routes_y_se_descarta_lo_ajeno(
    web, example_store
):
    example_store.write_text("connectors/cloudflare.yaml", BROKEN_CLOUDFLARE)

    response = web.post("/conectores/cloudflare", CLOUDFLARE | {"confirmar_sobrescritura": "1"})

    assert response.status_code == 303
    saved = yaml.safe_load(example_store.read_text("connectors/cloudflare.yaml"))
    assert saved == {**CLOUDFLARE, "public_routes": [ROUTE]}


def test_confirmado_pero_con_claves_conservadas_invalidas_se_bloquea_y_nombra_el_campo(
    web, example_store
):
    broken = "asset_id: cf-taller\npublic_routes:\n- hostname: api.example.org\n  reason: ''\n"
    example_store.write_text("connectors/cloudflare.yaml", broken)

    response = web.post("/conectores/cloudflare", CLOUDFLARE | {"confirmar_sobrescritura": "1"})

    assert response.status_code == 422
    assert "Hay que corregir «public_routes» en el fichero a mano." in unescape(response.text)
    assert example_store.read_text("connectors/cloudflare.yaml") == broken


def test_un_fichero_que_no_es_yaml_se_trata_como_invalido_sin_mostrar_su_contenido(
    web, example_store
):
    example_store.write_text(GITHUB_FILE, "{ [ SECRETO-DEL-FICHERO: ::\n")

    page = web.get("/conectores")

    assert page.status_code == 200
    assert "SECRETO-DEL-FICHERO" not in page.text
    assert "Configuración no válida" in block_of(page.text, "github")


# --- Conservación de lo que el formulario no muestra ---------------------------------------


def test_guardar_cloudflare_conserva_public_routes(web, example_store):
    example_store.write_text(
        "connectors/cloudflare.yaml", yaml.safe_dump({**CLOUDFLARE, "public_routes": [ROUTE]})
    )

    assert web.post("/conectores/cloudflare", CLOUDFLARE | {"ui_hostname": "panel.example.org"})

    saved = yaml.safe_load(example_store.read_text("connectors/cloudflare.yaml"))
    assert saved["public_routes"] == [ROUTE]
    assert saved["ui_hostname"] == "panel.example.org"


def test_guardar_restic_conserva_offsite(web, example_store):
    example_store.write_text(
        "connectors/restic.yaml",
        yaml.safe_dump(
            {**{k: (int(v) if v.isdigit() else v) for k, v in RESTIC.items()}, "offsite": OFFSITE}
        ),
    )

    response = web.post("/conectores/restic", RESTIC | {"max_backup_age_hours": "48"})

    assert response.status_code == 303
    saved = yaml.safe_load(example_store.read_text("connectors/restic.yaml"))
    assert saved["offsite"] == OFFSITE and saved["max_backup_age_hours"] == 48


# --- D10: fallo del backend auditado sin valores ------------------------------------------------


def test_un_fallo_de_escritura_del_backend_se_audita_sin_valores(
    config, example_store, key, token, audit, monkeypatch
):
    from starlette.testclient import TestClient

    from ens_evidence.ui.app import create_ui_app
    from ens_evidence.ui.routes import ui_routes
    from ens_evidence.workspace import LocalStore, WorkspaceError

    from .conftest import ORIGIN, jwks_for

    def boom(self, key_, content):
        raise WorkspaceError(f"TEXTO-INTERNO {content}")

    monkeypatch.setattr(LocalStore, "write_text", boom)
    app = create_ui_app(
        config, example_store, lambda: NOW, lambda: jwks_for(key), routes=ui_routes()
    )
    session = Session(TestClient(app, base_url=ORIGIN, raise_server_exceptions=False), token)

    response = session.post("/conectores/github", GITHUB)

    assert response.status_code == 500
    [line] = audit_lines(audit, "failed")
    assert GITHUB_FILE in line and "acme" not in line and "TEXTO-INTERNO" not in line
    assert "TEXTO-INTERNO" not in response.text and "acme" not in response.text


# --- D5: la guía de cada conector se sirve ---------------------------------------------------


@pytest.mark.parametrize("name", list(conectores_catalogo.CATALOGUE))
def test_el_enlace_a_la_guia_de_cada_conector_devuelve_200(web, name):
    block = block_of(web.get("/conectores").text, name)
    href = re.search(r'href="(/ayuda/conector-[^"]+)"', block).group(1)

    assert href == f"/ayuda/conector-{name}"
    assert web.get(href).status_code == 200


# --- Inventario: la ficha del activo enlaza a su conector -----------------------------------


def test_la_ficha_del_activo_enlaza_al_conector_que_lo_cita(web):
    web.post("/conectores/github", GITHUB)

    page = web.get("/inventario/activos/gh-taller").text

    assert "Conectores que citan este activo" in page
    assert 'href="/conectores/github"' in page


def test_la_ficha_de_un_activo_que_ningun_conector_cita_lo_dice(web):
    page = web.get("/inventario/activos/lap-ana").text

    assert "Ningún conector configurado cita este activo." in page
    assert 'href="/conectores/' not in page


def test_el_enlace_del_conector_aparece_en_la_navegacion_de_todas_las_paginas(web):
    assert 'href="/conectores"' in web.get("/inventario").text


# --- Copy -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/conectores", *[f"/conectores/{n}" for n in (*CONFIGS, "keystore", "passwords")]]
)
def test_ninguna_pagina_usa_palabras_prohibidas_ni_dice_nada_favorable_sin_evidencia(web, path):
    page = web.get(path).text

    assert not FORBIDDEN.search(page), FORBIDDEN.search(page)
    assert_nothing_favourable(page)
    assert "declara conformidad" in page


def test_las_paginas_con_estado_configurado_sin_recogidas_tampoco_son_favorables(web):
    for name, form in CONFIGS.items():
        assert web.post(f"/conectores/{name}", form).status_code == 303

    page = web.get("/conectores").text

    assert not FORBIDDEN.search(page)
    assert_nothing_favourable(page)
    assert page.count("Sin recogidas todavía") >= len(CONFIGS)


def test_el_texto_del_bloque_se_escapa_con_valores_hostiles_en_la_configuracion(web, example_store):
    example_store.write_text(
        GITHUB_FILE, yaml.safe_dump({"asset_id": "gh-taller", "organization": HOSTILE})
    )

    page = web.get("/conectores").text

    assert HOSTILE not in page


# --- Revisión externa r1 ---------------------------------------------------------------------


def _saved(store, name) -> dict:
    return yaml.safe_load(store.read_text(f"connectors/{name}.yaml"))


@pytest.mark.parametrize("bad", [[], ["gh-taller"], {"a": 1}, 5, None])
def test_un_asset_id_que_no_es_texto_no_rompe_la_ficha_de_ningun_activo(web, example_store, bad):
    example_store.write_text("connectors/github.yaml", yaml.safe_dump({**GITHUB, "asset_id": bad}))
    repo = {**REPOSITORY, "id": "caja", "asset_id": bad}
    example_store.write_text("connectors/backups.yaml", yaml.safe_dump({"repositories": [repo]}))

    page = web.get("/inventario/activos/gh-taller")

    assert page.status_code == 200
    assert 'href="/conectores/github"' not in page.text


def test_un_asset_id_de_texto_junto_a_otro_que_no_lo_es_sigue_enlazando_backups(web, example_store):
    repos = [
        {**REPOSITORY, "id": "uno", "asset_id": ["x"]},
        {**REPOSITORY, "id": "dos", "asset_id": "bk-diario"},
    ]
    example_store.write_text("connectors/backups.yaml", yaml.safe_dump({"repositories": repos}))

    page = web.get("/inventario/activos/bk-diario")

    assert page.status_code == 200 and 'href="/conectores/backups"' in page.text


@pytest.mark.parametrize(
    ("conector", "base", "campo", "valor", "ausente"),
    [
        ("cloudflare", CLOUDFLARE, "token_kind", "user", "token_kind"),
        ("cloudflare", CLOUDFLARE, "ui_hostname", "ui.example.org", "ui_hostname"),
        ("google-workspace", GWS, "customer", "C01abc", "customer"),
        ("keystore", KEYSTORE, "address", "https://keys.example.org:8200", "address"),
        ("keystore", KEYSTORE, "max_token_period_hours", "48", "max_token_period_hours"),
        (
            "passwords",
            {"asset_id": "app-facturas", "inactive_days": "7", "max_export_age_minutes": "30"},
            "export_path",
            "/srv/x.csv",
            "export_path",
        ),
    ],
)
def test_vaciar_un_opcional_ya_guardado_lo_quita_del_fichero_y_el_cargador_lo_acepta(
    web, example_store, conector, base, campo, valor, ausente
):
    spec = conectores_catalogo.CATALOGUE[conector]
    assert web.post(f"/conectores/{conector}", base | {campo: valor}).status_code == 303
    assert _saved(example_store, conector)[campo]

    response = web.post(f"/conectores/{conector}", base | {campo: ""})

    assert response.headers["location"] == f"/conectores?hecho={conector}"
    assert ausente not in _saved(example_store, conector)
    load_connector_config(example_store, conector, spec.model)


def test_vaciar_un_opcional_conserva_lo_que_el_formulario_no_muestra(web, example_store):
    web.post("/conectores/cloudflare", CLOUDFLARE | {"token_kind": "user"})
    saved = _saved(example_store, "cloudflare") | {"public_routes": [ROUTE]}
    example_store.write_text("connectors/cloudflare.yaml", yaml.safe_dump(saved))

    web.post("/conectores/cloudflare", CLOUDFLARE | {"token_kind": ""})

    after = _saved(example_store, "cloudflare")
    assert "token_kind" not in after and after["public_routes"] == [ROUTE]


def test_sin_cambios_solo_si_no_cambia_nada_tras_vaciar_un_opcional(web, example_store):
    web.post("/conectores/cloudflare", CLOUDFLARE | {"token_kind": "user"})
    web.post("/conectores/cloudflare", CLOUDFLARE)  # lo quita
    before = snapshot_of(example_store)

    response = web.post("/conectores/cloudflare", CLOUDFLARE)

    assert response.headers["location"] == "/conectores?hecho=sin-cambios"
    assert snapshot_of(example_store) == before


def test_una_recogida_posterior_ilegible_no_deja_los_checks_antiguos_como_vigentes(
    web, example_store
):
    web.post("/conectores/github", GITHUB)
    record(example_store, "github", [result("gh-taller", CheckStatus.PASS, "GITHUB-001")], ago=24)
    put_raw(example_store, artifact_document({}))
    manifest_id = next(k for k in example_store.list("") if k.endswith(".yaml") and "EV-" in k)
    stored = yaml.safe_load(example_store.read_text(manifest_id))
    example_store.write_bytes(stored["artifact"]["key"], b"{}")

    page = web.get("/conectores").text

    block = block_of(page, "github")
    assert (
        "Estos resultados son anteriores a una recogida posterior que no se pudo verificar."
        in block
    )
    assert "tone-ok" not in block.split("Resultados de las comprobaciones")[1].split("<table")[0]


def test_la_tabla_de_secciones_dice_su_fuente_y_su_fecha(web, example_store):
    web.post("/conectores/github", GITHUB)
    collect(example_store, "github", "SUCCESS", {"sections": {"repos": {"status": "SUCCESS"}}})

    block = block_of(web.get("/conectores").text, "github")

    caption = re.search(r"<caption>Estado por sección.*?</caption>", block, re.DOTALL)
    assert caption and "Fuente: recogida" in caption.group(0)
    assert "github" in caption.group(0) and "EV-" in caption.group(0)
