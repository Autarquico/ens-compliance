from ens_evidence.models.enums import CollectionStatus

from .fake_api import ACCOUNT, TUNNEL_REMOTE, ZONE, denied, envelope


def section(result, name):
    return result.data["sections"][name]


def test_recurso_sin_permiso_queda_permission_denied_y_no_degrada_el_resto(collect, api):
    api.set(f"/zones/{ZONE}/dns_records", denied())

    result = collect()

    dns = section(result, "dns")
    assert dns["status"] == "PERMISSION_DENIED"
    assert dns["items"][0]["status"] == "PERMISSION_DENIED"
    assert "403" in dns["items"][0]["reason"]
    assert section(result, "tls")["status"] == "SUCCESS"
    assert result.collection_status is CollectionStatus.SUCCESS


def test_no_configurado_es_success_con_items_vacios_distinto_de_permission_denied(collect, api):
    api.set(f"/accounts/{ACCOUNT}/access/apps", envelope([], {"total_pages": 1}))

    result = collect()

    apps = section(result, "access_applications")
    assert apps["status"] == "SUCCESS"
    assert apps["items"] == []
    assert section(result, "access_policies") == {
        "status": "SUCCESS",
        "reason": None,
        "items": [],
        "summary": {},
    }


def test_access_sin_permiso_no_se_confunde_con_no_configurado(collect, api):
    api.set(f"/accounts/{ACCOUNT}/access/apps", denied())

    result = collect()

    apps = section(result, "access_applications")
    assert apps["status"] == "PERMISSION_DENIED"
    assert apps["items"] == []
    assert "403" in apps["reason"]
    assert section(result, "access_policies")["status"] == "PERMISSION_DENIED"


def test_zonas_sin_permiso_propagan_el_estado_a_dns_tls_y_waf_con_motivo(collect, api):
    api.set("/zones", denied())

    result = collect()

    for name in ("zones", "dns", "tls", "waf"):
        assert section(result, name)["status"] == "PERMISSION_DENIED", name
    assert "depende de zones" in section(result, "dns")["reason"]
    assert section(result, "tunnels")["status"] == "SUCCESS"


def test_un_ajuste_tls_sin_permiso_solo_marca_ese_ajuste(collect, api):
    api.set(f"/zones/{ZONE}/settings/min_tls_version", denied())

    result = collect()

    tls = section(result, "tls")
    settings = tls["items"][0]["settings"]
    assert tls["status"] == "PERMISSION_DENIED"
    assert settings["min_tls_version"]["status"] == "PERMISSION_DENIED"
    assert settings["min_tls_version"]["value"] is None
    assert settings["ssl"] == {"status": "SUCCESS", "value": "strict"}


def test_waf_sin_permiso_es_permission_denied(collect, api):
    api.set(f"/zones/{ZONE}/rulesets", denied())

    assert section(collect(), "waf")["status"] == "PERMISSION_DENIED"


def test_audit_logs_sin_permiso_es_permission_denied_y_no_completo(collect, api):
    api.set(f"/accounts/{ACCOUNT}/logs/audit", denied())

    audit = section(collect(), "audit_logs")

    assert audit["status"] == "PERMISSION_DENIED"
    assert audit["summary"]["complete"] is False
    assert "count" not in audit["summary"]


def test_listado_de_tuneles_sin_permiso_es_permission_denied(collect, api):
    api.set(f"/accounts/{ACCOUNT}/cfd_tunnel", denied())

    tunnels = section(collect(), "tunnels")

    assert tunnels["status"] == "PERMISSION_DENIED"
    assert tunnels["items"] == []


def test_ingress_remoto_sin_permiso_degrada_la_seccion_de_tuneles(collect, api):
    api.set(f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_REMOTE}/configurations", denied())

    tunnels = section(collect(), "tunnels")

    remote = next(t for t in tunnels["items"] if t["id"] == TUNNEL_REMOTE)
    assert remote["ingress_status"] == "PERMISSION_DENIED"
    assert remote["ingress"] == []
    assert tunnels["status"] == "PERMISSION_DENIED"
