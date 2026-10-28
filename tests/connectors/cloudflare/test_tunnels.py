import pytest

from ens_evidence.connectors.cloudflare.checks import tunnel_requires_access
from ens_evidence.models.enums import CheckStatus

from .fake_api import (
    ACCOUNT,
    NOW,
    TUNNEL_LOCAL,
    TUNNEL_REMOTE,
    ZONE,
    denied,
    envelope,
    failure,
)


def tunnels_section(result):
    return result.data["sections"]["tunnels"]


def tunnel(result, tunnel_id):
    return next(t for t in tunnels_section(result)["items"] if t["id"] == tunnel_id)


def test_tunel_local_declara_ingress_no_soportado(collect):
    local = tunnel(collect(), TUNNEL_LOCAL)

    assert local["ingress_status"] == "NOT_SUPPORTED"
    assert "local" in local["reason"]


def test_tunel_local_infiere_hostnames_por_cname_al_tunel_marcados_como_inferidos(collect):
    local = tunnel(collect(), TUNNEL_LOCAL)

    assert local["ingress"] == [
        {"hostname": "panel.taller-norte.example", "path": None, "service": None, "inferred": True}
    ]
    assert local["inferred_complete"] is True


def test_la_inferencia_ignora_cname_de_otros_tuneles(collect):
    hostnames = {h["hostname"] for h in tunnel(collect(), TUNNEL_LOCAL)["ingress"]}

    assert "otro.taller-norte.example" not in hostnames
    assert "app.taller-norte.example" not in hostnames


@pytest.mark.parametrize("reply", [denied(), (500, failure(1, "boom"))], ids=["403", "500"])
def test_con_dns_no_success_el_tunel_local_no_infiere_nada(api, collect, reply):
    api.set(f"/zones/{ZONE}/dns_records", reply)

    local = tunnel(collect(), TUNNEL_LOCAL)

    assert local["ingress"] == []
    assert local["inferred_complete"] is False
    assert local["ingress_status"] == "NOT_SUPPORTED"


def test_con_zonas_no_success_el_tunel_local_no_infiere_nada(api, collect):
    api.set("/zones", denied())

    local = tunnel(collect(), TUNNEL_LOCAL)

    assert local["ingress"] == []
    assert local["inferred_complete"] is False


def test_un_tunel_local_no_degrada_el_estado_de_la_seccion(collect):
    assert tunnels_section(collect())["status"] == "SUCCESS"


def test_tunel_remoto_lee_el_ingress_por_api_con_el_servicio_saneado(collect):
    remote = tunnel(collect(), TUNNEL_REMOTE)

    assert remote["ingress_status"] == "SUCCESS"
    assert remote["inferred_complete"] is False
    assert remote["ingress"] == [
        {
            "hostname": "app.taller-norte.example",
            "path": None,
            "service": "https://origin.internal:8443",
            "inferred": False,
        },
        {
            "hostname": "admin.taller-norte.example",
            "path": "^/agent/.*",
            "service": "http://localhost:8080",
            "inferred": False,
        },
        {"hostname": None, "path": None, "service": "http_status", "inferred": False},
    ]


def test_tunel_sin_config_src_con_remote_config_se_trata_como_remoto(api, collect):
    api.set(
        f"/accounts/{ACCOUNT}/cfd_tunnel",
        envelope([{"id": TUNNEL_REMOTE, "remote_config": True}], {"total_pages": 1}),
    )

    remote = tunnel(collect(), TUNNEL_REMOTE)

    assert remote["ingress_status"] == "SUCCESS"
    assert remote["ingress"]


def test_tunel_sin_pista_de_gestion_se_trata_como_local(api, collect):
    api.set(
        f"/accounts/{ACCOUNT}/cfd_tunnel",
        envelope([{"id": TUNNEL_LOCAL}], {"total_pages": 1}),
    )

    assert tunnel(collect(), TUNNEL_LOCAL)["ingress_status"] == "NOT_SUPPORTED"


def test_fallo_al_leer_el_ingress_remoto_es_error_del_tunel_y_de_la_seccion(api, collect):
    api.set(
        f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_REMOTE}/configurations",
        (500, failure(1, "boom")),
    )

    result = collect()

    assert tunnel(result, TUNNEL_REMOTE)["ingress_status"] == "ERROR"
    assert tunnel(result, TUNNEL_REMOTE)["ingress"] == []
    assert tunnels_section(result)["status"] == "ERROR"


def test_se_listan_solo_los_tuneles_no_borrados(api, collect):
    collect()

    assert api.params(f"/accounts/{ACCOUNT}/cfd_tunnel")[0]["is_deleted"] == "false"


def test_nunca_se_piden_endpoints_que_devuelven_secretos(api, collect):
    collect()

    for path in api.paths():
        assert not path.endswith("/token"), path
        assert "service_tokens" not in path, path
        assert "/credentials" not in path, path


@pytest.mark.parametrize(
    "result",
    [{}, {"config": None}, {"config": {}}, None],
    ids=["vacio", "config-null", "config-vacio", "result-null"],
)
def test_configuracion_remota_sin_config_o_sin_ingress_es_error_no_ingress_vacio(
    api, collect, result
):
    api.set(f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_REMOTE}/configurations", envelope(result))

    collected = collect()

    remote = tunnel(collected, TUNNEL_REMOTE)
    assert remote["ingress_status"] == "ERROR"
    assert remote["ingress"] == []
    assert remote["reason"]
    results = tunnel_requires_access(collected, "ev-1", NOW)
    unresolved = next(r for r in results if r.subject == TUNNEL_REMOTE)
    assert unresolved.status is CheckStatus.ERROR


def test_ingress_remoto_vacio_pero_presente_es_success_sin_rutas(api, collect):
    api.set(
        f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_REMOTE}/configurations",
        envelope({"config": {"ingress": []}}),
    )

    remote = tunnel(collect(), TUNNEL_REMOTE)

    assert remote["ingress_status"] == "SUCCESS"
    assert remote["ingress"] == []
