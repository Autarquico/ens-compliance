"""El conector: qué lee, qué descarta y cómo falla."""

import json

import pytest

from ens_evidence.connectors.keystore import KeystoreConfig, KeystoreConnector, KeystoreRun
from ens_evidence.models.enums import CollectionStatus

from .conftest import REGISTER
from .fake_openbao import (
    ACCESSOR_ROOT,
    APP_LOOKUP,
    CUSTOM,
    LEAKED_ID,
    META,
    NOW,
    POLICY,
    PUBLIC_KEY,
    TOKEN,
    days_ago,
    kv_entry,
)

SECTIONS = {
    "seal", "token", "audit", "mounts", "kv", "transit", "policies", "accessors", "custodians"
}  # fmt: skip


def section(result, name):
    return result.data["sections"][name]


def test_recoge_las_nueve_secciones_con_exito(collect):
    result = collect()

    assert result.collection_status is CollectionStatus.SUCCESS
    assert set(result.data["sections"]) == SECTIONS
    assert result.asset_id == "openbao"
    assert result.data["thresholds"] == {"max_key_age_days": 90, "max_token_period_hours": 72}


def test_kv_recorre_las_carpetas_y_solo_guarda_metadatos(collect):
    kv = section(collect(), "kv")

    assert [i["path"] for i in kv["items"]] == ["app", "dir/db"]
    assert kv["items"][1] == {
        "path": "dir/db",
        "current_version": 3,
        "last_version_at": days_ago(20).replace("Z", "+00:00"),
    }


def test_transit_lee_la_fecha_de_la_ultima_version_simetrica_y_asimetrica(collect):
    items = {i["name"]: i for i in section(collect(), "transit")["items"]}

    assert items["sim"]["last_version_at"] == days_ago(5).replace("Z", "+00:00")
    assert items["firma"]["last_version_at"] == days_ago(3).replace("Z", "+00:00")
    assert items["firma"]["auto_rotate_period_seconds"] == 86400


def test_la_evidencia_serializada_no_contiene_valores_marcados(collect):
    text = collect().model_dump_json()

    for marker in (CUSTOM, PUBLIC_KEY, META, LEAKED_ID, TOKEN, "acc-app-canario", ACCESSOR_ROOT):
        assert marker not in text
    assert "Ana Pérez" not in text and "Marta Ruiz" not in text


def test_accessors_solo_guarda_politicas_sin_id_ni_meta(collect):
    accessors = section(collect(), "accessors")

    assert accessors["items"] == [{"policies": [POLICY], "orphan": True, "ttl_seconds": 100}] * 2
    assert accessors["summary"] == {
        "count": 2, "listed": 2, "unreadable": 0, "truncated": False
    }  # fmt: skip


def test_nunca_pide_valores_ni_nada_fuera_de_la_lista_blanca(api, collect):
    collect()

    assert {m for m, _ in api.requests} <= {"GET", "LIST", "POST"}
    assert not [p for _, p in api.requests if p.startswith(("kv/data", "kv/subkeys"))]
    posts = {p for m, p in api.requests if m == "POST"}
    assert posts == {"sys/capabilities-self", "auth/token/lookup-accessor"}


def test_las_politicas_guardan_solo_el_hash(collect):
    item = section(collect(), "policies")["items"][0]

    assert item["exists"] is True and len(item["sha256"]) == 64
    assert set(item) == {"name", "exists", "sha256"}


def test_mounts_marca_como_no_cubierto_lo_que_no_es_kv2_ni_transit(api, collect):
    api.set(
        "GET",
        "sys/mounts",
        (
            200,
            {
                "data": {
                    "kv/": {"type": "kv", "options": {"version": "2"}},
                    "transit/": {"type": "transit", "options": {}},
                    "sys/": {"type": "system"},
                    "pki/": {"type": "pki"},
                    "otro/": {"type": "kv", "options": {"version": "1"}},
                }
            },
        ),
    )

    assert section(collect(), "mounts")["summary"]["uncovered"] == ["otro/", "pki/"]


def test_kv_en_v1_en_el_montaje_kv_no_esta_cubierto(api, collect):
    api.set(
        "GET", "sys/mounts", (200, {"data": {"kv/": {"type": "kv", "options": {"version": "1"}}}})
    )

    assert section(collect(), "mounts")["summary"]["uncovered"] == ["kv/"]


def test_listas_vacias_son_404_y_dan_cero_elementos(api, collect):
    api.drop("LIST", "kv/metadata/")
    api.drop("LIST", "transit/keys/")

    result = collect()

    assert section(result, "kv")["items"] == [] and section(result, "transit")["items"] == []
    assert result.collection_status is CollectionStatus.SUCCESS


def test_un_403_en_una_ruta_sudo_es_permission_denied_con_motivo(api, collect):
    api.set("GET", "sys/audit", (403, {"errors": ["permission denied"]}))
    api.set("LIST", "auth/token/accessors", (403, {"errors": ["permission denied"]}))

    result = collect()

    for name in ("audit", "accessors"):
        assert section(result, name)["status"] == "PERMISSION_DENIED"
        assert "no tiene permiso" in section(result, name)["reason"]
    assert section(result, "kv")["status"] == "SUCCESS"


def test_sellado_no_lee_nada_mas_y_las_secciones_quedan_en_error(api, collect):
    api.set("GET", "sys/seal-status", (200, {**json_seal(), "sealed": True}))

    result = collect()

    assert section(result, "seal")["summary"]["sealed"] is True
    for name in SECTIONS - {"seal", "custodians"}:
        assert section(result, name)["status"] == "ERROR"
        assert "sellado" in section(result, name)["reason"]
    assert [p for _, p in api.requests] == ["sys/seal-status"]
    assert result.collection_status is CollectionStatus.ERROR


def test_sin_inicializar_tampoco_se_lee_mas(api, collect):
    api.set("GET", "sys/seal-status", (200, {**json_seal(), "initialized": False}))

    result = collect()

    assert "sin inicializar" in section(result, "kv")["reason"]


def test_sin_respuesta_de_openbao_todo_queda_en_error(api, collect):
    api.set("GET", "sys/seal-status", (503, {"errors": ["boom"]}))

    result = collect()

    assert section(result, "seal")["status"] == "ERROR"
    assert section(result, "audit")["status"] == "ERROR"
    assert result.collection_status is CollectionStatus.ERROR


@pytest.mark.parametrize("env", [{}, {"ENS_KEYSTORE_TOKEN": "  "}, {"ENS_KEYSTORE_TOKEN": "a\nb"}])
def test_sin_token_valido_solo_se_lee_seal_status(api, collect, env):
    result = collect(env=env)

    assert section(result, "seal")["status"] == "SUCCESS"
    assert "ENS_KEYSTORE_TOKEN" in section(result, "kv")["reason"]
    assert [p for _, p in api.requests] == ["sys/seal-status"]
    assert api.tokens == [None]


def lookup(ttl: int, period: int = 72 * 3600, renewable: bool = True):
    return (200, {"data": {**APP_LOOKUP, "ttl": ttl, "period": period, "renewable": renewable}})


def test_renueva_si_el_ttl_es_menor_que_la_mitad_del_periodo(api, collect):
    replies = iter([lookup(10 * 3600), lookup(72 * 3600)])
    api.set("GET", "auth/token/lookup-self", lambda r: _next(replies, r))
    api.set("POST", "auth/token/renew-self", (200, {"auth": {"lease_duration": 259200}}))

    token = section(collect(), "token")["summary"]

    assert api.count("POST", "auth/token/renew-self") == 1
    assert token["renewed"] is True and token["ttl_seconds"] == 72 * 3600


@pytest.mark.parametrize(
    "reply",
    [lookup(36 * 3600), lookup(40 * 3600), lookup(10 * 3600, renewable=False), lookup(5, period=0)],
)
def test_no_renueva_con_ttl_igual_o_mayor_que_la_mitad_o_sin_periodo(api, collect, reply):
    api.set("GET", "auth/token/lookup-self", reply)

    token = section(collect(), "token")["summary"]

    assert api.count("POST", "auth/token/renew-self") == 0
    assert token["renewed"] is False


def test_el_token_guarda_politicas_periodo_ttl_y_capacidades(collect):
    token = section(collect(), "token")["summary"]

    assert token["policies"] == [POLICY]
    assert token["period_seconds"] == 72 * 3600
    assert all(caps == ["deny"] for caps in token["capabilities"].values())
    assert "kv/data/ens-probe" in token["capabilities"]


def test_mas_de_un_accessor_ilegible_se_cuenta_sin_perder_los_demas(api, collect):
    from .fake_openbao import ACCESSOR_APP

    def lookup_accessor(request):
        import httpx

        accessor = json.loads(request.content)["accessor"]
        if accessor == "acc-otro":
            return httpx.Response(400, json={"errors": ["invalid accessor"]})
        return httpx.Response(200, json={"data": {"policies": [POLICY]}})

    api.set("POST", "auth/token/lookup-accessor", lookup_accessor)

    accessors = section(collect(), "accessors")

    assert accessors["summary"]["unreadable"] == 1 and len(accessors["items"]) == 1
    assert ACCESSOR_APP not in json.dumps(accessors)


def test_los_nombres_de_un_listado_se_codifican_como_un_solo_segmento(api, collect):
    api.set("LIST", "kv/metadata/", (200, {"data": {"keys": ["a b?", "../x"]}}))
    api.set("GET", "kv/metadata/a%20b%3F", (200, kv_entry(days_ago(1))))
    api.set("GET", "kv/metadata/..%2Fx", (200, kv_entry(days_ago(1))))

    kv = section(collect(), "kv")

    assert [i["path"] for i in kv["items"]] == ["../x", "a b?"]
    assert not [p for _, p in api.requests if "/../" in p or p.endswith("/..")]


def test_un_nombre_dotdot_literal_no_se_pide(api, collect):
    api.set("LIST", "kv/metadata/", (200, {"data": {"keys": [".."]}}))

    kv = section(collect(), "kv")

    assert kv["status"] == "ERROR" and "no válido" in kv["reason"]
    assert ("GET", "kv/metadata/..") not in api.requests


def test_custodios_solo_recuento_y_fechas(collect):
    summary = section(collect(), "custodians")["summary"]

    assert summary == {
        "registered": True,
        "unreadable": [],
        "count": 3,
        "key_shares": 3,
        "key_threshold": 2,
        "reviewed_at": "2026-11-09T10:00:00+00:00",
        "valid_until": "2027-11-09T10:00:00+00:00",
    }


def test_sin_registro_de_custodios(collect):
    summary = section(collect(custody=None, unreadable_registers=["evidence/x"]), "custodians")

    assert summary["summary"] == {"registered": False, "unreadable": ["evidence/x"]}


def test_la_configuracion_exige_max_key_age_days_y_rechaza_extras():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KeystoreConfig(asset_id="openbao")
    with pytest.raises(ValidationError):
        KeystoreConfig(asset_id="openbao", max_key_age_days=90, token="x")
    with pytest.raises(ValidationError):
        KeystoreConfig(asset_id="openbao", max_key_age_days=90, address="http://u:p@host")
    assert KeystoreConfig(asset_id="openbao", max_key_age_days=90).max_token_period_hours == 72


def test_prepare_exige_un_activo_application_y_lee_los_custodios(monkeypatch):
    from types import SimpleNamespace

    from ens_evidence.connectors.keystore import connector as module
    from ens_evidence.scope.models import AssetType
    from ens_evidence.workspace import WorkspaceError

    assets = SimpleNamespace(
        assets=[
            SimpleNamespace(id="openbao", type=AssetType.APPLICATION),
            SimpleNamespace(id="disco", type=AssetType.BACKUP),
        ]
    )
    monkeypatch.setattr(module, "load_assets", lambda store: (assets, []))
    monkeypatch.setattr(module, "latest_register", lambda store, now, asset_id: (REGISTER, ["x"]))
    connector = KeystoreConnector(clock=lambda: NOW, env={})

    run = connector.prepare(KeystoreConfig(asset_id="openbao", max_key_age_days=90), object())
    assert isinstance(run, KeystoreRun) and run.custody is REGISTER
    assert run.unreadable_registers == ["x"]
    with pytest.raises(WorkspaceError, match="application"):
        connector.prepare(KeystoreConfig(asset_id="disco", max_key_age_days=90), object())


def json_seal():
    from .fake_openbao import SEAL_OPEN

    return dict(SEAL_OPEN)


def _next(replies, request):
    import httpx

    status, body = next(replies)
    return httpx.Response(status, json=body)


def test_sin_period_en_lookup_self_el_periodo_es_creation_ttl_como_en_openbao_2_7_2(api, collect):
    real = {k: v for k, v in APP_LOOKUP.items() if k != "period"}
    real |= {"creation_ttl": 72 * 3600, "ttl": 10 * 3600}
    replies = iter([(200, {"data": real}), (200, {"data": {**real, "ttl": 72 * 3600}})])
    api.set("GET", "auth/token/lookup-self", lambda r: _next(replies, r))
    api.set("POST", "auth/token/renew-self", (200, {"auth": {"lease_duration": 259200}}))

    token = section(collect(), "token")["summary"]

    assert token["period_seconds"] is None
    assert token["creation_ttl_seconds"] == 72 * 3600
    assert token["renewed"] is True  # sin esto el token del kit caducaría a las 72 h
