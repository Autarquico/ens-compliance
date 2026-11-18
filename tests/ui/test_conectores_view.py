"""Vista de conectores: contexto de cada pantalla y flujo de escritura, sin plantillas."""

import logging

import pytest
import yaml
from pydantic import BaseModel

from ens_evidence.connectors.registry import ConnectorLoadError
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.scheduling.collect_requests import (
    ChecksOutcome,
    CollectRequest,
    CollectRequestResult,
    ErrorCode,
    Outcome,
    RequestStatus,
    State,
)
from ens_evidence.ui.views import conectores as view
from ens_evidence.ui.views import conectores_recogida
from ens_evidence.ui.views.conectores_peticion import request_view
from ens_evidence.workspace import WorkspaceError

from .conftest import NOW, snapshot_of
from .test_conectores import artifact_document, collect, put_raw
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


def test_un_conector_registrado_sin_ficha_y_configurado_ofrece_recoger_y_el_post_lo_admite(
    config, example_store, key, token, monkeypatch
):
    class Model(BaseModel):
        asset_id: str

    monkeypatch.setattr(view, "registered_connectors", lambda: ({**real(), "nuevo": Model}, False))
    ui = build_ui(config, example_store, key, token)
    example_store.write_text("connectors/nuevo.yaml", "asset_id: gh-taller\n")

    ui.get("/conectores")
    block = blocks(ui)["nuevo"]

    assert block.config.status == "configurado" and block.can_collect
    assert ui.post("/conectores/nuevo/recoger", {}).status_code == 303
    assert "evidence/collect-requests/nuevo/000001.json" in example_store.list("evidence")
    assert ui.post("/conectores/otro/recoger", {}).status_code == 403


def test_un_conector_que_no_esta_registrado_no_se_admite_aunque_tenga_configuracion(
    config, example_store, key, token
):
    ui = build_ui(config, example_store, key, token)
    example_store.write_text("connectors/fantasma.yaml", "asset_id: x\n")
    before = snapshot_of(example_store)

    assert ui.post("/conectores/fantasma/recoger", {}).status_code == 403
    assert snapshot_of(example_store) == before


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


def test_unos_resultados_anteriores_a_una_recogida_ilegible_salen_como_no_vigentes(ui):
    ui.post("/conectores/github", GITHUB)
    record(ui.store, "github", [result("gh-taller", CheckStatus.PASS, "GITHUB-001")], ago=24)
    put_raw(ui.store, artifact_document({}))
    manifest = next(k for k in ui.store.list("") if k.endswith(".yaml") and "EV-" in k)
    ui.store.write_bytes(yaml.safe_load(ui.store.read_text(manifest))["artifact"]["key"], b"{}")

    ui.get("/conectores")
    evidence = blocks(ui)["github"].evidence

    assert evidence.stale is True and evidence.last_outcome == "unreadable"
    assert evidence.favorable is False and evidence.coverage == "parcial"


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


# --- peticiones de recogida -----------------------------------------------------------------------

REQUEST_PREFIX = "evidence/collect-requests"
EVIDENCE_ID = "EV-20261108-abcd1234"


def _result(**fields) -> CollectRequestResult:
    base = {
        "outcome": Outcome.DONE,
        "at": NOW,
        "collection_status": CollectionStatus.SUCCESS,
        "evidence_id": EVIDENCE_ID,
        "attempts": 1,
        "checks": ChecksOutcome.RECORDED,
    }
    return CollectRequestResult(**(base | fields))


def _status(state: State, result: CollectRequestResult | None = None) -> RequestStatus:
    request = CollectRequest(connector="github", email="ana@example.com", requested_at=NOW)
    return RequestStatus(f"{REQUEST_PREFIX}/github/000001.json", "github", request, state, result)


def test_las_peticiones_vivas_y_caducadas_tienen_su_etiqueta_fija():
    pending = request_view(_status(State.PENDING))
    assert (pending.label, pending.tone, pending.alive) == ("Pendiente", "warn", True)
    assert pending.text == (
        "Se recogerá en cuanto el servicio esté libre; normalmente en menos de un minuto."
    )
    running = request_view(_status(State.RUNNING))
    assert (running.label, running.tone, running.alive) == ("En curso", "warn", True)
    expired = request_view(_status(State.EXPIRED))
    assert (expired.label, expired.tone, expired.alive) == ("Caducada", "neutral", False)
    assert "runbook" in expired.text and "ídela de nuevo" not in expired.text


@pytest.mark.parametrize(
    ("result", "label", "tone"),
    [
        (_result(), "Terminada", "neutral"),
        (_result(checks=ChecksOutcome.UNCHANGED), "Terminada", "neutral"),
        (
            _result(collection_status=CollectionStatus.ERROR),
            "Terminada con error de recogida",
            "bad",
        ),
        (
            _result(collection_status=CollectionStatus.PERMISSION_DENIED),
            "Terminada con error de recogida",
            "bad",
        ),
        (
            _result(checks=ChecksOutcome.REJECTED),
            "Terminada; comprobaciones no registradas",
            "warn",
        ),
        (
            _result(checks=ChecksOutcome.SKIPPED_SCOPE),
            "Terminada; comprobaciones no registradas",
            "warn",
        ),
        (
            _result(checks=ChecksOutcome.ERROR),
            "Terminada; comprobaciones no registradas",
            "warn",
        ),
        (
            _result(outcome=Outcome.ERROR, code=ErrorCode.INTERRUPTED, evidence_id=None),
            "No se pudo hacer: la ejecución se interrumpió",
            "bad",
        ),
    ],
)
def test_terminada_nunca_es_favorable_por_si_sola(result, label, tone):
    shown = request_view(_status(State.DONE, result))

    assert (shown.label, shown.tone, shown.alive) == (label, tone, False)
    assert shown.tone != "ok"
    assert shown.text == ""
    assert shown.code == ("INTERRUPTED" if label.startswith("No se pudo") else None)


def test_los_dos_fallos_se_distinguen_en_texto_y_el_de_recogida_prevalece():
    both = _result(collection_status=CollectionStatus.ERROR, checks=ChecksOutcome.ERROR)

    shown = request_view(_status(State.DONE, both))

    assert shown.label == "Terminada con error de recogida"
    assert "comprobaciones no registradas" in shown.result.checks_label
    assert shown.result.collection_label == "recogida con error"


def test_un_identificador_no_canonico_no_se_proyecta():
    hostile = CollectRequestResult.model_construct(
        **(_result().model_dump() | {"evidence_id": "<b>x"})
    )

    shown = request_view(_status(State.DONE, hostile)).result

    assert shown.evidence_id is None and shown.evidence_invalid is True


def test_una_peticion_ilegible_no_es_viva_ni_favorable(ui):
    ui.post("/conectores/github", GITHUB)
    ui.store.write_text(f"{REQUEST_PREFIX}/github/000001.json", "{no es json")

    ui.get("/conectores")

    github = blocks(ui)["github"]
    assert github.request.state == "DONE" and github.request.tone == "bad"
    assert github.request.alive is False and github.can_collect is True


def test_recoger_escribe_la_peticion_audita_y_redirige(ui, audit):
    ui.post("/conectores/github", GITHUB)

    response = ui.post("/conectores/github/recoger", {})

    key = f"{REQUEST_PREFIX}/github/000001.json"
    assert response.status_code == 303
    assert response.headers["location"] == "/conectores?pedida=github"
    assert ui.store.exists(key)
    [line] = [
        r.message for r in audit.records if "outcome=saved" in r.message and "recoger" in r.message
    ]
    assert "step=conectores action=recoger asset=github" in line and key in line


def test_tras_pedir_el_listado_muestra_pendiente_sin_boton(ui):
    ui.post("/conectores/github", GITHUB)
    ui.post("/conectores/github/recoger", {})

    ui.get("/conectores?pedida=github")

    github = blocks(ui)["github"]
    assert github.request.state == "PENDING" and github.can_collect is False
    assert github.request.label == "Pendiente"
    assert ui.context["requested_text"].startswith("Petición de recogida de GitHub creada")
    ui.get("/conectores?pedida=<script>")
    assert ui.context["requested_text"] is None


def test_el_boton_sale_solo_con_configuracion_y_permiso(ui):
    ui.get("/conectores")
    assert blocks(ui)["github"].can_collect is False  # sin configurar
    ui.post("/conectores/github", GITHUB)

    ui.get("/conectores")

    github = blocks(ui)["github"]
    assert github.can_collect is True
    assert github.collect_action == "/conectores/github/recoger"


def test_una_segunda_peticion_viva_da_422_y_no_escribe_otra(ui, audit):
    ui.post("/conectores/github", GITHUB)
    ui.post("/conectores/github/recoger", {})
    before = snapshot_of(ui.store)
    audit.clear()

    response = ui.post("/conectores/github/recoger", {})

    assert response.status_code == 422
    assert snapshot_of(ui.store) == before
    assert any("Ya hay una petición" in e for e in ui.context["form_errors"])
    assert not [r for r in audit.records if "outcome=saved" in r.message]


def test_recoger_sin_configurar_da_422_sin_escribir(ui):
    before = snapshot_of(ui.store)

    response = ui.post("/conectores/github/recoger", {})

    assert response.status_code == 422
    assert snapshot_of(ui.store) == before
    assert ui.context["form_errors"] == ["El conector GitHub no está configurado."]


def test_un_fallo_de_persistencia_se_audita_como_fallido(ui, audit, monkeypatch):
    ui.post("/conectores/github", GITHUB)

    def boom(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(view, "request_collect", boom)

    with pytest.raises(OSError):
        ui.post("/conectores/github/recoger", {})

    [line] = [r.message for r in audit.records if "action=recoger" in r.message]
    assert "outcome=failed" in line


def test_recoger_un_conector_desconocido_no_llega_al_handler(ui):
    assert ui.post("/conectores/desconocido/recoger", {}).status_code in (403, 404)


def test_un_lector_no_puede_recoger_aunque_el_conector_este_configurado(ui):
    ui.post("/conectores/github", GITHUB)

    page = view.build_page(ui.store, False, NOW, view.collectable_connectors())

    assert {b.name: b.can_collect for b in page.blocks}["github"] is False
