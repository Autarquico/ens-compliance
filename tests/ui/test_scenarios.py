"""Un test por escenario de docs/specs/web-ui-base.md y de docs/specs/web-ui-flujo.md."""

import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import ens_evidence.cli as cli
from ens_evidence.applicability import DeclarationState, declaration_status
from ens_evidence.assessment.dossier import scan_transitions
from ens_evidence.categorization import categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import matrix_status
from ens_evidence.models.enums import Category, CheckStatus, DossierState
from ens_evidence.orgdocs import DocumentState, all_statuses
from ens_evidence.preaudit.requests import request_prepare
from ens_evidence.risk import RiskState, risk_status
from ens_evidence.scheduling.scheduler import CycleExecutor
from ens_evidence.ui.store import WRITABLE_PREFIXES, UiStore, WriteNotAllowed
from ens_evidence.ui.wizard import STEPS
from ens_evidence.workspace import LocalStore

from . import test_equivalence as flows
from .conftest import (
    NOW,
    ORIGIN,
    WRITE_HEADERS,
    Session,
    auth,
    mint,
    snapshot_of,
    ui_client,
)
from .test_equivalence import fingerprints, run_cli, ui_script

web_flow = flows.web_flow  # la fixture de sesión: el flujo se recorre una vez para todos

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "interfaz-web.md"
GET_ROUTES = [
    "/", "/matriz", "/evidencias", "/expediente", "/preauditoria", "/asistente",
    *(f"/asistente/{s.slug}" for s in STEPS), "/static/ui.css",
    "/herramientas", "/herramientas/claves", "/herramientas/claves/custodios",
    "/herramientas/contrasenas",
]  # fmt: skip
POST_ROUTES = [
    *(f"/asistente/{s.slug}" for s in STEPS),
    "/herramientas/claves",
    "/herramientas/claves/custodios",
    "/herramientas/contrasenas",
]


def unauthenticated_tokens(key, other_key) -> dict[str, str | None]:
    return {
        "sin cabecera": None,
        "caducado": mint(key, exp=1_700_000_000),
        "firmado por otra clave": mint(other_key),
        "alg none": mint(key, alg="none"),
        "basura": "no.es.unjwt",
    }


@pytest.fixture
def client(config, example_store, key):
    return ui_client(config, example_store, key)


def test_sin_access_no_se_entra(client, example_store, key, other_key):
    before = snapshot_of(example_store)
    form = {"name": "x", "description": "y", "accion": "aprobar", "approved_by": "x"}

    for label, token in unauthenticated_tokens(key, other_key).items():
        headers = {} if token is None else auth(token)
        for path in GET_ROUTES:
            response = client.get(path, headers=headers, follow_redirects=False)
            assert response.status_code == 401, (label, "GET", path)
        for path in POST_ROUTES:
            response = client.post(path, data=form, headers={**headers, **WRITE_HEADERS})
            assert response.status_code == 401, (label, "POST", path)

    assert snapshot_of(example_store) == before


def test_el_jwt_de_otra_audiencia_o_emisor_tampoco_entra(client, key, example_store):
    before = snapshot_of(example_store)
    for overrides in ({"aud": ["otra"]}, {"iss": "https://evil.cloudflareaccess.com"}):
        response = client.get("/", headers=auth(mint(key, **overrides)))
        assert response.status_code == 401
    assert snapshot_of(example_store) == before


def test_sin_certificados_nadie_entra(config, example_store, key):
    from starlette.testclient import TestClient

    from ens_evidence.ui.app import create_ui_app
    from ens_evidence.ui.routes import ui_routes

    from .conftest import NOW

    def no_keys():
        return {"keys": []}

    app = create_ui_app(config, example_store, lambda: NOW, no_keys, routes=ui_routes())
    response = TestClient(app, base_url=ORIGIN).get("/", headers=auth(mint(key)))
    assert response.status_code == 401


def test_mfa_exigido_por_access():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "## 3. Comprobar que Access exige MFA" in text
    section = text.split("## 3. Comprobar que Access exige MFA", 1)[1].split("\n## ", 1)[0]
    assert "exija MFA" in text.split("## 3.", 1)[0]
    assert "Authentication method" in section
    assert "Bypass" in section
    assert "segundo factor" in section
    assert "302" in section and "cloudflareaccess.com" in section


def test_alcance_y_categorizacion_equivalentes_a_la_cli(
    tmp_path, workspaces, config, key, token, monkeypatch
):
    cli_store, ui_store = workspaces
    run_cli(tmp_path, monkeypatch)
    ui_script(Session(ui_client(config, ui_store, key), token))

    keys = set(snapshot_of(cli_store))
    assert {"scope/approval.yaml", "scope/categorization.yaml"} <= keys
    assert "scope/categorization-approval.yaml" in keys
    assert keys == set(snapshot_of(ui_store))
    assert snapshot_of(cli_store) == snapshot_of(ui_store)  # bytes: la spec exige ficheros iguales
    assert fingerprints(cli_store) == fingerprints(ui_store)


HOSTILE_KEYS = [
    "connectors/github.yaml",
    "../connectors/x.yaml",
    "settings.yaml",
    "reports/r.md",
    "scope",
    "scope/../connectors/x.yaml",
    "/etc/passwd",
]


@pytest.mark.parametrize("key", HOSTILE_KEYS)
def test_la_ui_no_toca_la_configuracion(client, example_store, key):
    before = snapshot_of(example_store)
    store = client.app.state.store

    for write in (store.write_text, store.write_bytes, store.create_bytes):
        with pytest.raises(Exception) as caught:
            write(key, "x" if write is store.write_text else b"x")
        assert caught.type is WriteNotAllowed or caught.type.__name__ == "InvalidKey"

    assert snapshot_of(example_store) == before


def test_la_ui_no_toca_la_configuracion_por_ningun_handler(client, example_store, token):
    """Ids con forma de ruta en todos los pasos: nada fuera de la lista blanca cambia."""
    example_store.write_text("connectors/github.yaml", "token_env: X\n")
    before = snapshot_of(example_store)
    evil = "../connectors/github"
    form = {
        "name": evil, "description": evil, "asset_id": evil, "asset_type": "laptop",
        "service_id": evil, "service_name": evil, "service_assets": [evil],
        "dependency_id": evil, "asset": evil, "depends_on": evil, "info_id": evil,
        "included_assets": [evil], "included_services": [evil], "accion": "anadir",
        "approved_by": evil,
    }  # fmt: skip

    for path in POST_ROUTES:
        client.post(path, data=form, headers={**auth(token), **WRITE_HEADERS})

    after = snapshot_of(example_store)
    assert after["connectors/github.yaml"] == before["connectors/github.yaml"]
    outside = {k for k in set(after) | set(before) if k.split("/")[0] not in WRITABLE_PREFIXES}
    assert {k: after.get(k) for k in outside} == {k: before.get(k) for k in outside}
    assert not any(".." in k for k in after)
    assert isinstance(client.app.state.store, UiStore)


# --- docs/specs/web-ui-flujo.md ------------------------------------------------------------------


def flow_session(config, store, key, token) -> Session:
    return Session(ui_client(config, store, key), token)


def test_el_flujo_completo_por_la_web_termina_con_el_expediente_declarado(
    web_flow, config, key, token
):
    store = web_flow.store

    assert fingerprints(store)  # alcance aprobado y categorización vigente
    assert categorization_status(store, NOW).computation.category is Category.BASICA
    assert risk_status(store, NOW).state is RiskState.APPROVED
    assert declaration_status(store, NOW).state is DeclarationState.CURRENT
    assert {s.state for s in all_statuses(store)} == {DocumentState.APPROVED}
    rows = matrix_status(store, NOW, checks=StoredCheckResults(reference_now=NOW))[1]
    assert rows and all(r.row.review is not None for r in rows)
    assert scan_transitions(store, web_flow.dossier).dossier.state is DossierState.DECLARED
    page = flow_session(config, store, key, token).get("/expediente").text
    assert "La declaración ya está registrada" in page


def test_en_cada_estado_intermedio_la_pagina_de_estado_enlaza_a_un_paso_que_responde(web_flow):
    *intermediate, last = web_flow.milestones

    assert len(intermediate) >= 12
    for milestone in intermediate:
        assert milestone.url, milestone
        assert milestone.status == 200, milestone
    assert intermediate[0].url == "/asistente/aprobacion-alcance"
    assert {m.url.split("/")[1] for m in intermediate} == {"asistente", "matriz", "expediente"}
    assert last.name == "expediente: declare" and last.label


def test_la_web_no_da_por_buena_una_medida_sin_pruebas(web_flow, tmp_path, config, key, token):
    store = LocalStore(tmp_path / "copia")
    for name, data in snapshot_of(web_flow.store).items():
        store.write_bytes(name, data)
    ui = flow_session(config, store, key, token)
    ui.post("/matriz/org.1/desenlazar", {"kind": "org_document", "target": "politica-seguridad"})
    before = snapshot_of(store)

    response = ui.post(
        "/matriz/org.1/revisar",
        {
            "by": "Ana Ruiz",
            "role": "system_administrator",
            "assessment": "COMPLIANT",
            "conclusion": "Revisada.",
        },
    )

    assert response.status_code == 422
    assert "Cláusulas sin cubrir" in response.text
    for clause in ("org.1.1", "org.1.2", "org.1.3", "org.1.4", "org.1.5"):
        assert clause in response.text
    assert snapshot_of(store) == before


def test_scheduler_run_ejecuta_las_peticiones_de_la_ui_sin_proveedores_reales(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "ws")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    built = {}

    class Recording:
        def __init__(self, clock, executor, *args, **kwargs):
            built.update(executor=executor, **kwargs)

        def run(self):
            built["ran"] = True

    monkeypatch.setattr(cli, "Scheduler", Recording)
    request = request_prepare(
        store, "20261108T120000000000Z-0123456789ab", by="Ana", email="ana@example.com", now=NOW
    )

    result = CliRunner().invoke(cli.app, ["--settings", str(settings), "scheduler", "run"])

    assert result.exit_code == 0, result.output
    requests = built["requests"]
    assert built["ran"] and isinstance(built["executor"], CycleExecutor)
    assert requests.__self__ is built["executor"]
    assert requests.__func__ is CycleExecutor.pending_requests
    assert requests(NOW) == [request.key]


# --- docs/specs/kit-backups.md ----------------------------------------------------------------

KIT_PAGES = ["/herramientas", "/herramientas/copias", "/matriz/mp.info.6", "/evidencias"]


def test_los_secretos_del_asistente_se_entregan_una_vez_y_no_quedan_en_la_app(
    session, example_store, caplog
):
    import json
    import logging

    from .test_tools import FORM, SENTINELS, members

    caplog.set_level(logging.DEBUG)
    before = snapshot_of(example_store)

    response = session.post("/herramientas/copias", FORM)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    files = members(response.content)
    repo_password = json.loads(files["copias/config.json"])["repos"][0]["password"]
    secrets = (*SENTINELS, repo_password)
    delivered = files["copias/config.json"] + files["copias/app.env"]
    assert repo_password and all(s.encode() in delivered for s in SENTINELS[1:2] + SENTINELS[3:])
    assert snapshot_of(example_store) == before  # workspace, evidencias y expediente intactos
    stored = b"\n".join([*before.values(), *(k.encode() for k in before)])
    assert not any(s.encode() in stored for s in secrets)
    logged = "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)
    assert not any(s in logged for s in secrets)
    for path in KIT_PAGES:
        page = session.get(path)
        assert not any(s in page.text for s in secrets), path


def test_una_copia_que_deja_de_hacerse_pone_la_tarjeta_en_rojo_y_dice_que_hay_problemas(
    session, example_store
):
    from .test_tools import record

    record(example_store, CheckStatus.FAIL)

    html = session.get("/herramientas").text

    assert "Con problemas" in html and 'class="badge tone-bad"' in html


def test_la_tarjeta_en_rojo_enlaza_al_runbook(session, example_store):
    from .test_tools import record

    record(example_store, CheckStatus.FAIL)

    assert 'href="/ayuda/kit-copias"' in session.get("/herramientas").text


# --- docs/specs/kit-secrets.md ----------------------------------------------------------------

KEY_PAGES = [
    "/herramientas",
    "/herramientas/claves",
    "/herramientas/claves/custodios",
    "/matriz/op.exp.10",
    "/evidencias",
]


def test_el_paquete_de_claves_no_lleva_secretos_y_la_app_no_guarda_nada(
    session, example_store, caplog
):
    import logging

    from .test_tools_claves import FORM, members

    caplog.set_level(logging.DEBUG)
    before = snapshot_of(example_store)

    response = session.post("/herramientas/claves", FORM)

    assert response.status_code == 200
    files = members(response.content)
    delivered = b"\n".join(files.values()).lower()
    assert not any(marker in delivered for marker in (b"hvs.", b"hvb.", b"root_token", b"unseal"))
    assert snapshot_of(example_store) == before
    logged = "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)
    assert "hvs." not in logged and "hvb." not in logged
    for path in KEY_PAGES:
        assert "hvs." not in session.get(path).text, path


def test_openbao_sellado_pone_la_tarjeta_de_claves_en_rojo_con_el_enlace_al_runbook(
    session, example_store
):
    from .test_tools_claves import UNSEALED_SEALED, card, record

    record(example_store, UNSEALED_SEALED)

    html = card(session.get("/herramientas").text)

    assert "Sellado" in html and 'class="badge tone-bad"' in html
    assert 'href="/ayuda/kit-claves"' in html


def test_con_openbao_sellado_op_exp_10_pierde_la_evidencia_vigente_del_kit(
    session, raw_client, token, example_store
):
    from ens_evidence.checks.model import CheckInput, StoredCheckRun
    from ens_evidence.checks.store import record_run
    from ens_evidence.evidence.importer import import_collected
    from ens_evidence.evidence.layout import sha256_hex
    from ens_evidence.matrix.evidence import manifest_sha256
    from ens_evidence.matrix.links import add_link
    from ens_evidence.matrix.model import CheckLink
    from ens_evidence.models.contracts import CheckResult, CollectionResult, CollectionStatus
    from ens_evidence.scope import scope_status

    from .conftest import NOW
    from .test_tools_claves import SEALED

    def row_of_the_link() -> str:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", session.get("/matriz/op.exp.10").text))
        return text.split("check keystore.unsealed", 1)[1].split("Quitar enlace", 1)[0]

    def record_unsealed(seq: int, status: CheckStatus, reason: str, evidence) -> None:
        finished = NOW - timedelta(hours=3 - seq)
        record_run(
            example_store,
            StoredCheckRun(
                seq=seq,
                connector="keystore",
                engine_version="1",
                started_at=finished - timedelta(minutes=5),
                finished_at=finished,
                complete=True,
                scope_fingerprint=scope_status(example_store).fingerprint,
                config_sha256=sha256_hex(example_store.read_bytes("connectors/keystore.yaml")),
                inputs=[
                    CheckInput(evidence_id=evidence.id, manifest_sha256=manifest_sha256(evidence))
                ],
                results=[
                    CheckResult(
                        check_id="keystore.unsealed",
                        asset_id="app-facturas",
                        status=status,
                        evaluated_at=finished,
                        evidence_ids=[evidence.id],
                        reason=reason,
                    )
                ],
            ),
            NOW,
        )

    flows.web_script_until_review(session, raw_client, token)
    example_store.write_text(
        "connectors/keystore.yaml", "asset_id: app-facturas\nmax_key_age_days: 365\n"
    )
    evidence = import_collected(
        example_store,
        CollectionResult(
            source="keystore",
            collector="keystore",
            collected_at=NOW - timedelta(hours=4),
            asset_id="app-facturas",
            data={"sealed": False},
            collection_status=CollectionStatus.SUCCESS,
        ),
        collector_version="1",
        now=NOW - timedelta(hours=4),
    )
    add_link(
        example_store,
        NOW,
        "op.exp.10",
        CheckLink(check_id="keystore.unsealed", covers=["op.exp.10.1"]),
    )

    record_unsealed(1, CheckStatus.PASS, "OpenBao está inicializado y desellado", evidence)
    assert row_of_the_link().strip().endswith("Cuenta")

    record_unsealed(2, CheckStatus.FAIL, SEALED, evidence)
    assert row_of_the_link().strip().endswith("No cuenta todavía")
    sealed_page = session.get("/matriz/op.exp.10").text
    assert "Cumple" not in sealed_page and "el kit de claves" not in sealed_page


def test_openbao_instalado_sin_claves_ni_auditoria_no_sale_como_al_dia(session, example_store):
    from ens_evidence.connectors.keystore import checks as keystore_checks

    from .test_tools_claves import UNSEALED_OK, card, record

    audit = (keystore_checks.AUDIT_ENABLED, CheckStatus.FAIL, "no hay ningún dispositivo")
    keys = (keystore_checks.KEYS_ROTATED, CheckStatus.FAIL, "no hay claves gestionadas")
    record(example_store, UNSEALED_OK, audit, keys)

    html = card(session.get("/herramientas").text)

    assert "Con problemas" in html and "Al día" not in html


# --- docs/specs/kit-access-panel.md -----------------------------------------------------------


def test_con_todas_las_herramientas_desplegadas_cada_tarjeta_da_su_estado_y_abrir(
    config, example_store, key, token
):
    from ens_evidence.kit.registry import KIT_TOOLS

    from . import test_tools, test_tools_claves, test_tools_contrasenas

    on_box = [tool for tool in KIT_TOOLS if tool.on_box]
    hosts = {tool.id: f"{tool.id}.example.org" for tool in on_box}
    test_tools.record(example_store, CheckStatus.PASS)
    test_tools_claves.record(example_store, test_tools_claves.UNSEALED_OK)
    test_tools_contrasenas.record(example_store, test_tools_contrasenas.SECOND_OK)
    deployed = Session(ui_client(replace(config, kit_hosts=hosts), example_store, key), token)

    html = deployed.get("/herramientas").text

    cards = re.findall(
        r'<section class="card" aria-labelledby="h-(\w+)">(.*?)</section>', html, re.S
    )
    assert [tool_id for tool_id, _ in cards] == [tool.id for tool in KIT_TOOLS]
    for tool in on_box:
        tool_id, body = next(card for card in cards if card[0] == tool.id)
        assert tool.name in body
        assert "Al día" in body, tool_id
        assert f'href="https://{hosts[tool_id]}"' in body and ">Abrir<" in body, tool_id
        assert tool.cta not in body, tool_id


def test_la_ficha_propone_la_herramienta_con_sus_clausulas_y_el_boton_al_asistente(
    web,
):
    from ens_evidence.kit.registry import tool_for

    for measure_id in ("op.exp.10", "mp.info.6"):
        tool = tool_for(measure_id)
        html = web.get(f"/matriz/{measure_id}").text

        offer = html.split('id="h-kit"', 1)[1].split("</section>", 1)[0]
        assert tool.name.lower() in " ".join(offer.split()).lower(), measure_id
        for clause in tool.partial_clauses:
            assert f"{clause}</span>: cubierta en parte" in offer, clause
        for clause in tool.not_covered_clauses:
            assert f"{clause}</span>: no cubierta" in offer, clause
        assert f'href="{tool.wizard_url}">{tool.cta}</a>' in offer, measure_id


# Una medida n.a. no recibe propuesta: se prueba en test_tools.py sobre kit_offer; hoy ninguna
# medida del kit admite n.a. (son obligatorias en todos los niveles): no hay ficha que pintar.


def test_con_copias_con_evidencia_y_claves_sin_datos_solo_op_exp_10_propone(web, signed):
    from . import test_tools

    test_tools.record(signed, CheckStatus.PASS)

    assert 'id="h-kit"' not in web.get("/matriz/mp.info.6").text
    assert 'id="h-kit"' in web.get("/matriz/op.exp.10").text


@pytest.mark.parametrize(
    "statuses,complete", [((CheckStatus.FAIL,), True), ((CheckStatus.PASS,), False)]
)
def test_una_herramienta_con_problemas_o_caida_no_se_propone_como_si_faltara(
    web,
    signed,
    statuses,
    complete,
):
    from . import test_tools

    test_tools.record(signed, *statuses, complete=complete)

    assert 'id="h-kit"' not in web.get("/matriz/mp.info.6").text


# --- docs/specs/kit-passwords.md --------------------------------------------------------------

PWD_EMAIL = "persona01@example.test"
PWD_USER = "00000000-0000-4000-8000-000000000001"


def passwords_card(store, session, tmp_path, doc: dict | None) -> str:
    """La tarjeta tras recoger de verdad un export y evaluar los checks reales sobre él."""
    import json

    from ens_evidence.connectors.passwords import PasswordsConfig, PasswordsConnector
    from ens_evidence.connectors.passwords import checks as passwords_checks

    from .test_tools_contrasenas import card, record

    path = tmp_path / "usuarios.json"
    if doc is not None:
        path.write_text(json.dumps(doc), encoding="utf-8")
    config = PasswordsConfig(
        asset_id="app-facturas", export_path=str(path), inactive_days=90, max_export_age_minutes=60
    )
    result = PasswordsConnector(clock=lambda: NOW).collect(config)
    outcomes = [
        outcome
        for check in (passwords_checks.second_factor, passwords_checks.no_inactive_accounts)
        for outcome in check(result, "EV-20261108-aaaaaaaa", NOW)
    ]
    record(store, *[(o.check_id, o.status, o.reason) for o in outcomes])
    return card(session.get("/herramientas").text)


def password_export(second_factor: bool) -> dict:
    moment = (NOW - timedelta(minutes=5)).isoformat()
    return {
        "format": 1,
        "exported_at": moment,
        "vaultwarden_migration": "20261007120000",
        "snapshot_ok": True,
        "last_good_snapshot_at": moment,
        "users": [
            {
                "id": PWD_USER,
                "enabled": True,
                "invited": False,
                "created_at": (NOW - timedelta(days=100)).isoformat(),
                "second_factor": second_factor,
                "last_active": (NOW - timedelta(days=1)).isoformat(),
            }
        ],
    }


def test_un_usuario_sin_doble_factor_pone_la_tarjeta_de_contrasenas_en_problemas(
    session, example_store, tmp_path
):
    html = passwords_card(example_store, session, tmp_path, password_export(second_factor=False))

    assert "Con problemas" in html and "Al día" not in html
    assert PWD_EMAIL not in html
    assert 'href="/ayuda/kit-contrasenas"' in html


def test_con_el_doble_factor_en_todos_la_tarjeta_de_contrasenas_esta_al_dia(
    session, example_store, tmp_path
):
    html = passwords_card(example_store, session, tmp_path, password_export(second_factor=True))

    assert "Al día" in html


@pytest.mark.parametrize("doc", [None, {"format": 2}], ids=["export ausente", "export erróneo"])
def test_un_export_ausente_o_erroneo_nunca_deja_la_tarjeta_de_contrasenas_al_dia(
    session, example_store, tmp_path, doc
):
    html = passwords_card(example_store, session, tmp_path, doc)

    assert "Al día" not in html and "Caída" in html
