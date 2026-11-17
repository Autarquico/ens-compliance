"""Vista de conectores: contexto de cada pantalla y flujo de escritura, sin plantillas."""

import logging

import pytest
import yaml
from pydantic import BaseModel

from ens_evidence.connectors.registry import ConnectorLoadError
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.views import conectores as view
from ens_evidence.ui.views import conectores_recogida
from ens_evidence.workspace import WorkspaceError

from .conftest import snapshot_of
from .test_conectores import collect
from .test_inventory import record, result
from .test_inventory_view import build_ui

GITHUB = {"asset_id": "gh-taller", "organization": "taller"}
TOKEN = "ghp_" + "a" * 36
KEY = "connectors/github.yaml"


@pytest.fixture
def ui(config, example_store, key, token):
    return build_ui(config, example_store, key, token)


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def blocks(ui) -> dict:
    return {b.name: b for b in ui.context["page"].blocks}


def test_el_listado_muestra_los_siete_conectores_registrados_sin_configurar(ui):
    assert ui.get("/conectores").status_code == 200

    found = blocks(ui)
    assert set(found) == {
        "backups",
        "cloudflare",
        "github",
        "google-workspace",
        "keystore",
        "passwords",
        "restic",
    }
    assert {b.config.status for b in found.values()} == {"sin-configurar"}
    assert found["backups"].editable is False
    assert found["github"].can_configure is True


def test_una_sola_lectura_de_manifiestos_por_peticion(ui, monkeypatch):
    calls = []
    original = conectores_recogida.list_manifests
    monkeypatch.setattr(
        conectores_recogida, "list_manifests", lambda s: calls.append(1) or original(s)
    )

    ui.get("/conectores")

    assert calls == [1]


def test_guardar_escribe_audita_y_redirige(ui, audit):
    response = ui.post("/conectores/github", GITHUB)

    assert response.status_code == 303
    assert response.headers["location"] == "/conectores?hecho=github"
    assert yaml.safe_load(ui.store.read_text(KEY)) == GITHUB
    [line] = [r.message for r in audit.records if "outcome=saved" in r.message]
    assert "step=conectores action=configurar asset=github" in line and KEY in line
    assert "taller" not in line


def test_guardar_lo_mismo_dice_sin_cambios(ui):
    ui.post("/conectores/github", GITHUB)

    response = ui.post("/conectores/github", GITHUB)

    assert response.headers["location"] == "/conectores?hecho=sin-cambios"


def test_el_listado_muestra_lo_configurado_y_el_aviso_de_guardado(ui):
    ui.post("/conectores/github", GITHUB)

    ui.get("/conectores?hecho=github")

    assert blocks(ui)["github"].config.status == "configurado"
    assert blocks(ui)["github"].config.asset_href == "/inventario/activos/gh-taller"
    assert ui.context["saved"] == "github"
    ui.get("/conectores?hecho=<script>")
    assert ui.context["saved"] is None


@pytest.mark.parametrize(
    ("conector", "campo", "base"),
    [
        ("github", "organization", GITHUB),  # texto
        ("github", "asset_id", GITHUB),  # activo
        ("keystore", "max_key_age_days", {"asset_id": "app-facturas"}),  # entero
        ("cloudflare", "token_kind", {"asset_id": "cf-taller", "account_id": "a" * 32}),  # opción
        (
            "google-workspace",
            "domains",
            {"asset_id": "ws-identidad", "admin_subject": "a@b.org"},
        ),  # líneas
    ],
)
def test_un_secreto_en_cualquier_campo_no_se_escribe_ni_se_repite(ui, audit, conector, campo, base):
    before = snapshot_of(ui.store)

    response = ui.post(f"/conectores/{conector}", {**base, campo: f"ok\n{TOKEN}"})

    assert response.status_code == 422
    assert snapshot_of(ui.store) == before
    assert ui.context["errors"][campo] == [view.SECRET_IN_FIELD]
    assert campo not in ui.context["values"]
    assert TOKEN not in repr(ui.context) and TOKEN not in audit.text


def test_el_activo_debe_ser_del_tipo_del_conector(ui):
    response = ui.post("/conectores/github", {**GITHUB, "asset_id": "lap-ana"})

    assert response.status_code == 422
    assert "Alojamiento de código" in ui.context["errors"]["asset_id"][0]
    assert [c.value for c in ui.context["form"].asset_choices] == ["gh-taller"]
    assert not ui.store.exists(KEY)


def test_claves_ajenas_al_catalogo_no_llegan_al_escritor(ui):
    ui.post("/conectores/github", {**GITHUB, "extra": "x", "token": "y"})

    assert yaml.safe_load(ui.store.read_text(KEY)) == GITHUB


def test_un_fichero_invalido_sale_vacio_y_pide_confirmacion(ui):
    ui.store.write_text(KEY, "asset_id: gh-taller\norganization: taller\nmalo: 1\n")

    ui.get("/conectores/github")
    assert ui.context["values"] == {}
    assert ui.context["form"].needs_confirmation is True
    assert blocks_status(ui) == "invalida"

    refused = ui.post("/conectores/github", GITHUB)
    assert refused.status_code == 422
    assert ui.context["form_errors"]

    done = ui.post("/conectores/github", {**GITHUB, view.CONFIRM_FIELD: "1"})
    assert done.status_code == 303
    assert yaml.safe_load(ui.store.read_text(KEY)) == GITHUB


def blocks_status(ui) -> str:
    return ui.context["block"].config.status


def test_un_fallo_de_persistencia_se_audita_sin_texto_de_excepcion(
    config, example_store, key, token, audit, monkeypatch
):
    ui = build_ui(config, example_store, key, token, raise_errors=False)

    def boom(*args, **kwargs):
        raise WorkspaceError("TEXTO-INTERNO")

    monkeypatch.setattr(view, "save_connector_config", boom)

    assert ui.post("/conectores/github", GITHUB).status_code == 500
    [line] = [r.message for r in audit.records if "outcome=failed" in r.message]
    assert KEY in line and "TEXTO-INTERNO" not in line


def test_backups_es_de_solo_lectura(ui):
    assert ui.get("/conectores/backups").status_code == 200
    assert ui.context["form"] is None
    assert ui.context["block"].editable is False
    assert ui.post("/conectores/backups", {}).status_code == 403


def test_un_conector_registrado_sin_ficha_sale_generico(ui, monkeypatch):
    class Model(BaseModel):
        pass

    monkeypatch.setattr(view, "registered_connectors", lambda: ({**real(), "nuevo": Model}, False))

    ui.get("/conectores")
    generic = blocks(ui)["nuevo"]

    assert (generic.editable, generic.credential) == (False, None)
    assert generic.config.status == "sin-configurar"
    assert ui.get("/conectores/nuevo").status_code == 200
    assert ui.get("/conectores/otro").status_code == 404
    assert ui.post("/conectores/nuevo", {}).status_code == 403


def real():
    return {n: s.model for n, s in view.CATALOGUE.items() if s.model is not None}


def test_la_ficha_del_activo_enlaza_los_conectores_que_lo_citan(ui):
    ui.post("/conectores/github", GITHUB)

    ui.get("/inventario/activos/gh-taller")

    links = ui.context["detail"].connectors
    assert [(c.name, c.href) for c in links] == [("github", "/conectores/github")]


def test_unos_resultados_anteriores_a_una_recogida_fallida_salen_como_no_vigentes(ui):
    ui.post("/conectores/github", GITHUB)
    record(ui.store, "github", [result("gh-taller", CheckStatus.PASS, "GITHUB-001")], ago=24)
    collect(ui.store, "github", "ERROR")

    ui.get("/conectores")
    evidence = blocks(ui)["github"].evidence

    assert evidence.stale is True and evidence.last_outcome == "failed"
    assert evidence.favorable is False and evidence.coverage == "parcial"
    assert evidence.tone == "warn" and "parcial" in evidence.label


def test_unos_resultados_posteriores_a_la_recogida_siguen_vigentes(ui):
    ui.post("/conectores/github", GITHUB)
    collect(ui.store, "github", "ERROR")
    record(ui.store, "github", [result("gh-taller", CheckStatus.PASS, "GITHUB-001")], ago=0)

    ui.get("/conectores")
    evidence = blocks(ui)["github"].evidence

    assert evidence.stale is False and evidence.last_outcome is None
    assert evidence.favorable is True


def test_si_el_registro_de_conectores_falla_se_avisa_en_la_pagina(ui, monkeypatch):
    def broken(self, entries):
        raise ConnectorLoadError("x")

    monkeypatch.setattr(view.ConnectorRegistry, "load_entry_points", broken)

    ui.get("/conectores")

    assert ui.context["page"].registry_unreadable is True
    assert "github" in blocks(ui)


def test_con_el_registro_sano_la_pagina_no_avisa(ui):
    ui.get("/conectores")

    assert ui.context["page"].registry_unreadable is False
