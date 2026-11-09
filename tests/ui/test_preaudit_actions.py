"""Pre-auditoría por la web: pedir la preparación, consentir con la huella y decidir hallazgos.

La UI solo deja peticiones; `app` las ejecuta (aquí, con `execute` y un proveedor falso)."""

import logging
import re
from datetime import timedelta

import pytest
from assessment.dossier.stages import basic
from preaudit.helpers import CONTRADICTION, FakeAnthropic, traceable, with_evidence, world_at
from preaudit.test_review import CORRECTED_AT, imported_at
from preaudit.test_service import ANTHROPIC, PREPARED_AT, SENT_AT
from starlette.testclient import TestClient

from ens_evidence.models.preaudit import DispositionState, Layer
from ens_evidence.preaudit.records import scan_dispositions, scan_runs
from ens_evidence.preaudit.requests import (
    REQUESTS_PREFIX,
    Outcome,
    State,
    execute,
    list_requests,
)
from ens_evidence.preaudit.service import prepare, run
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.views.preaudit import PREAUDIT_ROUTES, plain

from .conftest import ORIGIN, Session, jwks_for, mint, snapshot_of

REVIEWED_AT = SENT_AT + timedelta(minutes=1)
ASKED_AT = PREPARED_AT - timedelta(seconds=30)
FORBIDDEN = ("ens-evidence", "--consent", "--by", "--evidence", "--reason", "preaudit run")


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def audited(stages, tmp_path, monkeypatch):
    """Un expediente con la política contradicha por una evidencia, sin preparar."""
    world = world_at(stages, tmp_path, monkeypatch)
    dossier, evidence = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    FakeAnthropic(lambda payload: traceable(payload, evidence)).install(monkeypatch)
    return world, dossier


def web(config, store, key, token, now) -> Session:
    clock = Clock(now)
    app = create_ui_app(config, store, clock, lambda: jwks_for(key), routes=[*PREAUDIT_ROUTES])
    start = int(ASKED_AT.timestamp())
    valid = mint(key, nbf=start - 3600, exp=start + 86400)
    session = Session(TestClient(app, base_url=ORIGIN), valid)
    session.clock = clock
    return session


@pytest.fixture
def fresh(audited, config, key, token):
    world, dossier = audited
    return web(config, world.store, key, token, ASKED_AT), world.store, dossier


@pytest.fixture
def prepared(audited, config, key, token):
    world, dossier = audited
    done = prepare(world.store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    return web(config, world.store, key, token, PREPARED_AT), world.store, dossier, done


@pytest.fixture
def judged(audited, config, key, token, tmp_path):
    world, dossier = audited
    done = prepare(world.store, dossier, None, ANTHROPIC, now=PREPARED_AT)
    result = run(
        world.store, dossier, None, done.payload.sha256, "Ana", settings=ANTHROPIC, now=SENT_AT
    )
    [finding] = [f for f in result.record.findings if f.layer is Layer.JUDGMENT]
    correction = imported_at(world, tmp_path, CORRECTED_AT, "Doble factor activado.")
    return (
        web(config, world.store, key, token, REVIEWED_AT),
        world.store,
        dossier,
        finding,
        correction,
    )


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.fixture
def audit(caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    return caplog


def consent(done, **overrides):
    return {
        "dossier": done.dossier,
        "reviewed": "1",
        "confirm_sha256": done.payload.sha256,
        "by": "Ana Pérez",
        **overrides,
    }


# --- preparar ------------------------------------------------------------------------------------


def test_sin_expedientes_lo_dice(config, store, key, token):
    page = web(config, store, key, token, ASKED_AT).get("/preauditoria")

    assert page.status_code == 200
    assert "Todavía no hay ningún expediente" in page.text


def test_preparar_deja_una_peticion_pendiente_y_su_linea_de_auditoria(fresh, audit):
    ui, store, dossier = fresh
    before = snapshot_of(store)

    response = ui.post("/preauditoria/preparar", {"dossier": dossier, "by": "Ana Pérez"})

    assert response.status_code == 303
    [request] = list_requests(store, ASKED_AT)
    assert request.state is State.PENDING and request.request.kind == "prepare"
    assert request.request.email == "ana@example.com"
    assert set(snapshot_of(store)) - set(before) == {request.key}
    line = next(r.message for r in audit.records if "step=preauditoria" in r.message)
    assert "identity=ana@example.com" in line and "action=preparar" in line
    page = ui.get(response.headers["location"]).text
    assert "En cola" in page and "Petición hecha" in page


def test_la_pagina_ofrece_el_ultimo_expediente_y_deja_quien_vacio(fresh):
    ui, _, dossier = fresh

    page = ui.get("/preauditoria").text

    assert f'value="{dossier}" selected' in page
    assert 'name="by" value=""' in page
    assert "@" not in page.split('name="by"')[1].split(">")[0]
    assert "Tu nombre completo. Queda registrado como lo declaras." in page


def test_con_una_peticion_viva_no_se_ofrece_otra_y_el_servidor_la_rechaza(fresh):
    ui, store, dossier = fresh
    ui.post("/preauditoria/preparar", {"dossier": dossier, "by": "Ana Pérez"})
    before = snapshot_of(store)

    page = ui.get("/preauditoria").text
    again = ui.post("/preauditoria/preparar", {"dossier": dossier, "by": "Ana Pérez"})

    assert 'action="/preauditoria/preparar"' not in page
    assert again.status_code == 422 and "sin terminar" in again.text
    assert snapshot_of(store) == before


def test_un_nombre_con_un_secreto_no_crea_peticion(fresh):
    ui, store, dossier = fresh
    before = snapshot_of(store)

    response = ui.post("/preauditoria/preparar", {"dossier": dossier, "by": "   "})

    assert response.status_code == 422
    assert snapshot_of(store) == before


def test_sin_nombre_o_con_un_correo_el_error_esta_en_castellano_sin_by(fresh):
    ui, store, dossier = fresh
    before = snapshot_of(store)

    for by in ("", "ana@example.com"):
        response = ui.post("/preauditoria/preparar", {"dossier": dossier, "by": by})

        assert response.status_code == 422
        assert "Quién lo hace" in response.text
        assert "--by" not in response.text
    assert snapshot_of(store) == before


def test_un_expediente_que_no_existe_es_404(fresh):
    ui, _, _ = fresh

    response = ui.post(
        "/preauditoria/preparar",
        {"dossier": "20261108T120000000000Z-000000000000", "by": "Ana"},
    )

    assert response.status_code == 404


def test_la_preparacion_hecha_muestra_destino_cifras_aviso_y_huella(prepared):
    ui, _, _, done = prepared

    page = ui.get("/preauditoria").text

    assert done.payload.sha256 in page
    assert "Lo que se enviaría" in page
    assert "anthropic" in page
    assert "no se redactan" in page
    assert 'name="reviewed"' in page and 'name="confirm_sha256"' in page
    assert 'name="accept_shell_risk"' not in page
    for text in FORBIDDEN:
        assert text not in page


def test_el_resultado_de_una_peticion_hecha_por_app_se_ve_en_llano(fresh):
    ui, store, dossier = fresh
    ui.post("/preauditoria/preparar", {"dossier": dossier, "by": "Ana Pérez"})
    [request] = list_requests(store, ASKED_AT)

    result = execute(store, request.key, ANTHROPIC, now=lambda: PREPARED_AT)

    assert result.outcome is Outcome.DONE
    ui.clock.now = PREPARED_AT
    page = ui.get("/preauditoria").text
    assert "Hecha" in page and "Lo que se enviaría" in page


# --- consentir -----------------------------------------------------------------------------------


def test_consentir_crea_la_peticion_con_la_huella_y_no_envia(prepared, audit):
    ui, store, dossier, done = prepared

    response = ui.post("/preauditoria/enviar", consent(done))

    assert response.status_code == 303
    [request] = list_requests(store, PREPARED_AT)
    assert request.state is State.PENDING
    assert request.request.kind == "run"
    assert request.request.payload_sha256 == done.payload.sha256
    assert request.request.prepared_key == done.key
    assert request.request.accept_shell_risk is False
    assert request.request.email == "ana@example.com"
    assert not scan_runs(store).records
    line = next(r.message for r in audit.records if "action=enviar" in r.message)
    assert REQUESTS_PREFIX in line


def test_la_peticion_de_envio_la_ejecuta_app_con_la_huella_consentida(prepared):
    ui, store, _, done = prepared
    ui.post("/preauditoria/enviar", consent(done))
    [request] = list_requests(store, PREPARED_AT)

    result = execute(store, request.key, ANTHROPIC, now=lambda: SENT_AT)

    assert result.outcome is Outcome.DONE, result.message
    [record] = scan_runs(store).records
    assert record.record.run.consent.payload_sha256 == done.payload.sha256


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"confirm_sha256": "0" * 64}, "huella no es la de lo que se enviaría"),
        ({"confirm_sha256": ""}, "huella no es la de lo que se enviaría"),
        ({"reviewed": None}, "has revisado lo que se envía"),
    ],
)
def test_sin_marcar_o_con_otra_huella_se_rechaza_y_no_se_escribe(prepared, change, expected):
    ui, store, _, done = prepared
    before = snapshot_of(store)
    data = {k: v for k, v in consent(done, **change).items() if v is not None}

    response = ui.post("/preauditoria/enviar", data)

    assert response.status_code == 422
    assert expected in text_of(response.text)
    assert snapshot_of(store) == before


def test_el_rechazo_no_devuelve_la_huella_escrita_pero_si_el_nombre(prepared):
    ui, _, _, done = prepared

    response = ui.post("/preauditoria/enviar", consent(done, confirm_sha256="1" * 64, by="Luis"))

    assert 'value="Luis"' in response.text
    assert "1" * 64 not in response.text


def test_un_destino_de_shell_exige_aceptar_el_riesgo(
    stages, tmp_path, monkeypatch, config, key, token
):
    from preaudit.helpers import fake_codex

    from ens_evidence.models.preaudit import Provider
    from ens_evidence.workspace import PreAuditSettings

    world = world_at(stages, tmp_path, monkeypatch)
    dossier, _ = with_evidence(world, tmp_path, f"{CONTRADICTION}\n", document=True)
    fake_codex(tmp_path, monkeypatch)
    done = prepare(
        world.store,
        dossier,
        None,
        PreAuditSettings(provider=Provider.CODEX, model="modelo"),
        now=PREPARED_AT,
    )
    ui = web(config, world.store, key, token, PREPARED_AT)
    page = ui.get("/preauditoria").text
    before = snapshot_of(world.store)

    refused = ui.post("/preauditoria/enviar", consent(done))
    accepted = ui.post("/preauditoria/enviar", consent(done, accept_shell_risk="1"))

    assert 'name="accept_shell_risk"' in page and "OpenAI" in page
    assert refused.status_code == 422 and "riesgo de la shell" in refused.text
    assert set(snapshot_of(world.store)) == set(before) | {
        r.key for r in list_requests(world.store, PREPARED_AT)
    }
    assert accepted.status_code == 303
    [request] = list_requests(world.store, PREPARED_AT)
    assert request.request.accept_shell_risk is True


# --- decidir -------------------------------------------------------------------------------------


def test_los_hallazgos_de_juicio_van_antes_que_los_deterministas(judged):
    ui, _, _, finding, _ = judged

    page = ui.get("/preauditoria").text

    assert finding.reason in page
    assert f'action="/preauditoria/hallazgos/{finding.id}"' in page
    assert page.index("Propuestas de la revisión automática") < page.index(
        "Comprobaciones automáticas"
    )
    assert "Criterio:" in page and "Fuente:" in page
    for text in FORBIDDEN:
        assert text not in page


def test_descartar_registra_la_disposicion_como_la_cli(judged, audit):
    ui, store, dossier, finding, _ = judged

    response = ui.post(
        f"/preauditoria/hallazgos/{finding.id}",
        {
            "dossier": dossier,
            "decision": "dismissed",
            "reason": "El documento citado ya no está en vigor.",
            "by": "Ana Pérez",
        },
    )

    assert response.status_code == 303
    [record] = scan_dispositions(store, now=REVIEWED_AT).records
    disposition = record.record.disposition
    assert disposition.state is DispositionState.DISMISSED
    assert (disposition.finding_id, disposition.by) == (finding.id, "Ana Pérez")
    assert any("action=decidir" in r.message for r in audit.records)
    assert "Ya está decidido" in ui.get(response.headers["location"]).text


def test_resolver_con_una_evidencia_elegida_de_la_lista(judged):
    ui, store, dossier, finding, correction = judged
    page = ui.get("/preauditoria").text
    assert f'<option value="{correction}"' in page

    response = ui.post(
        f"/preauditoria/hallazgos/{finding.id}",
        {"dossier": dossier, "decision": "resolved", "evidence": correction, "by": "Ana Pérez"},
    )

    assert response.status_code == 303
    [record] = scan_dispositions(store, now=REVIEWED_AT).records
    assert record.record.disposition.evidence_id == correction


def test_descartar_sin_motivo_se_rechaza_en_llano_y_no_escribe(judged):
    ui, store, dossier, finding, _ = judged
    before = snapshot_of(store)

    response = ui.post(
        f"/preauditoria/hallazgos/{finding.id}",
        {"dossier": dossier, "decision": "dismissed", "by": "Ana Pérez"},
    )

    assert response.status_code == 422
    assert "El motivo:" in response.text and "--reason" not in response.text
    assert snapshot_of(store) == before


@pytest.mark.parametrize(
    "path", ["/preauditoria/hallazgos/no-es-un-id", "/preauditoria/hallazgos/" + "A" * 64]
)
def test_un_id_de_hallazgo_invalido_es_404(judged, path):
    ui, _, _, _, _ = judged

    assert ui.post(path, {"decision": "accepted", "by": "Ana"}).status_code == 404


def test_una_decision_desconocida_o_un_hallazgo_ajeno_no_escribe(judged):
    ui, store, dossier, finding, _ = judged
    before = snapshot_of(store)

    unknown = ui.post(
        f"/preauditoria/hallazgos/{finding.id}",
        {"dossier": dossier, "decision": "open", "by": "Ana"},
    )
    foreign = ui.post(
        f"/preauditoria/hallazgos/{'a' * 64}",
        {"dossier": dossier, "decision": "accepted", "by": "Ana"},
    )

    assert unknown.status_code == foreign.status_code == 422
    assert snapshot_of(store) == before


def test_plain_quita_las_ordenes_de_cli():
    text = plain("--evidence: falla (`--accept-shell-risk`); revisa `preaudit` y vuelve")

    assert "--" not in text and "`" not in text
    assert "La evidencia:" in text and "la pre-auditoría" in text
