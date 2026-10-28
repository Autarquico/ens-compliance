from .fake_api import ACCOUNT, NOW, envelope

AUDIT = f"/accounts/{ACCOUNT}/logs/audit"


def audit_section(result):
    return result.data["sections"]["audit_logs"]


def test_audit_logs_guardan_solo_un_resumen_nunca_las_entradas(collect):
    audit = audit_section(collect())

    assert audit["status"] == "SUCCESS"
    assert audit["items"] == []
    assert set(audit["summary"]) == {
        "requested_since",
        "requested_before",
        "observed_first",
        "observed_last",
        "count",
        "complete",
    }


def test_el_resumen_recoge_ventana_pedida_ventana_observada_y_recuento(collect):
    summary = audit_section(collect())["summary"]

    assert summary["requested_since"] == "2026-05-02T12:00:00Z"
    assert summary["requested_before"] == "2026-06-01T12:00:00Z"
    assert summary["observed_first"] == "2026-05-12T08:30:00Z"
    assert summary["observed_last"] == "2026-05-30T10:00:00Z"
    assert summary["count"] == 3
    assert summary["complete"] is True


def test_la_ventana_se_pide_a_la_api_en_utc_con_el_reloj_inyectado(api, collect):
    collect()

    params = api.params(AUDIT)[0]
    assert params["since"] == "2026-05-02T12:00:00Z"
    assert params["before"] == "2026-06-01T12:00:00Z"
    assert NOW.isoformat().startswith("2026-06-01T12:00")


def test_cero_eventos_se_registra_como_recuento_cero_sin_ventana_observada(api, collect):
    api.set(AUDIT, envelope([], {"cursor": ""}))

    audit = audit_section(collect())

    assert audit["status"] == "SUCCESS"
    assert audit["summary"]["count"] == 0
    assert audit["summary"]["observed_first"] is None
    assert audit["summary"]["observed_last"] is None
