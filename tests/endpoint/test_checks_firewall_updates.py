"""HOST-005 (firewall) y HOST-006 (actualizaciones automáticas)."""

import pytest

from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import evaluate, host_evidence

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN
KEY = "AutomaticallyInstallMacOSUpdates"


def firewall(state, *, macos="15.1", osquery="5.14.1"):
    evidence = host_evidence(
        rows={
            "alf": [{"global_state": state}],
            "os_version": [{"name": "macOS", "version": macos}],
            "osquery_info": [{"version": osquery}],
        },
    )
    return evaluate(evidence)["HOST-005"]


@pytest.mark.parametrize(
    ("state", "status"),
    [("0", FAIL), ("1", PASS), ("2", PASS), ("3", UNKNOWN), ("", UNKNOWN), ("on", UNKNOWN)],
)
def test_global_state_decide_el_firewall(state, status):
    assert firewall(state).status is status


def test_macos_15_con_osquery_anterior_a_5_14_1_es_unknown_aunque_alf_diga_activo():
    result = firewall("1", osquery="5.14.0")

    assert result.status is UNKNOWN
    assert result.reason_code == "osquery_too_old"


def test_macos_15_con_version_de_osquery_ilegible_es_unknown():
    assert firewall("1", osquery="desconocida").status is UNKNOWN


def test_macos_14_no_exige_osquery_reciente():
    assert firewall("1", macos="14.6", osquery="5.12.0").status is PASS


def test_macos_15_con_osquery_posterior_pasa():
    assert firewall("1", osquery="5.15.0").status is PASS


def test_dos_filas_de_alf_son_unknown():
    evidence = host_evidence(rows={"alf": [{"global_state": "1"}, {"global_state": "0"}]})

    assert evaluate(evidence)["HOST-005"].status is UNKNOWN


@pytest.mark.parametrize("check_id", ["HOST-005", "HOST-006"])
def test_linux_es_unknown_not_supported_en_firewall_y_actualizaciones(check_id):
    result = evaluate(host_evidence("linux"))[check_id]

    assert result.status is UNKNOWN
    assert result.reason_code == "not_supported"
    assert result.collection_status is CollectionStatus.NOT_SUPPORTED


def updates(*rows):
    return evaluate(host_evidence(rows={"software_update_policy": list(rows)}))["HOST-006"]


def policy(value, manual="0", name=KEY):
    return {"name": name, "value": value, "user_scoped": "0", "manual": manual}


def test_preferencia_gestionada_activa_pasa():
    assert updates(policy("1")).status is PASS


def test_preferencia_gestionada_desactivada_falla():
    assert updates(policy("0")).status is FAIL


def test_preferencia_activa_no_gestionada_es_unknown():
    assert updates(policy("1", manual="1")).status is UNKNOWN


def test_preferencia_ausente_o_solo_de_comprobar_o_descargar_es_unknown():
    assert updates().status is UNKNOWN
    assert updates(policy("1", name="AutomaticCheckEnabled")).status is UNKNOWN
    assert updates(policy("1", name="AutomaticDownload")).status is UNKNOWN


def test_preferencias_contradictorias_son_unknown():
    assert updates(policy("1"), policy("0")).status is UNKNOWN


def test_valor_no_interpretable_es_unknown():
    assert updates(policy("quizá")).status is UNKNOWN


def test_macos_15_sin_fila_unica_de_osquery_info_es_unknown():
    for rows in ([], [{"version": "5.15.0"}, {"version": "5.15.0"}]):
        evidence = host_evidence(rows={"osquery_info": rows})

        assert evaluate(evidence)["HOST-005"].status is UNKNOWN


def test_macos_15_con_osquery_info_no_recogida_no_pasa():
    evidence = host_evidence(statuses={"osquery_info": CollectionStatus.ERROR})

    assert evaluate(evidence)["HOST-005"].status is not PASS


def test_preferencia_de_ambito_usuario_no_cuenta_como_del_equipo():
    result = updates({**policy("1"), "user_scoped": "1"})

    assert result.status is UNKNOWN
    assert result.reason_code == "user_scoped"


def test_una_preferencia_de_usuario_desactivada_no_hace_fallar_al_equipo():
    assert updates({**policy("0"), "user_scoped": "1"}).status is UNKNOWN


def test_una_fila_sin_user_scoped_no_cuenta_como_del_equipo():
    row = {"name": KEY, "value": "1", "manual": "0"}

    result = updates(row)

    assert result.status is UNKNOWN
    assert result.reason_code == "no_scope"
    assert updates(policy("1"), row).status is UNKNOWN


def test_la_preferencia_del_equipo_manda_sobre_la_de_un_usuario():
    assert updates(policy("1"), {**policy("0"), "user_scoped": "1"}).status is PASS
