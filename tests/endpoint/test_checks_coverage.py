"""HOST-001 (inventariado) y HOST-002 (agente activo)."""

import pytest

from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import LAPTOP, SERVER, config, evaluate, evaluate_all, host_evidence


def of(results, check_id, asset_id):
    return next(r for r in results if r.check_id == check_id and r.asset_id == asset_id)


def test_host_001_pasa_con_el_activo_inscrito_y_evidencia_vigente():
    result = evaluate(host_evidence(evidence_id="ev-9"))["HOST-001"]

    assert result.status is CheckStatus.PASS
    assert result.evidence_ids == ["ev-9"]


def test_host_001_falla_para_el_activo_del_alcance_sin_evidencia():
    results = evaluate_all(host_evidence(), asset_ids=(LAPTOP, SERVER))

    server = of(results, "HOST-001", SERVER)
    assert server.status is CheckStatus.FAIL
    assert server.reason_code == "no_evidence"
    assert server.evidence_ids == []
    assert of(results, "HOST-001", LAPTOP).status is CheckStatus.PASS


def test_sin_evidencia_el_resto_de_checks_del_activo_son_unknown():
    results = evaluate_all(asset_ids=(LAPTOP,))

    others = [r for r in results if r.check_id != "HOST-001"]
    assert {r.status for r in others} == {CheckStatus.UNKNOWN}


def test_host_001_falla_para_el_activo_sin_inscripcion_aunque_haya_evidencia():
    results = evaluate(host_evidence(), endpoint_config=config(enrolled=(SERVER,)))

    assert results["HOST-001"].status is CheckStatus.FAIL
    assert results["HOST-001"].reason_code == "not_enrolled"
    assert results["HOST-005"].status is CheckStatus.UNKNOWN


def test_la_evidencia_de_otro_activo_no_cubre_al_activo():
    results = evaluate_all(host_evidence("linux"), asset_ids=(LAPTOP, SERVER))

    assert of(results, "HOST-001", LAPTOP).status is CheckStatus.FAIL


def test_los_activos_que_no_son_laptop_ni_server_no_se_evaluan():
    results = evaluate_all(host_evidence(), asset_ids=(LAPTOP, "app-facturas"))

    assert {r.asset_id for r in results} == {LAPTOP}


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_host_002_es_siempre_unknown_not_supported(platform):
    result = evaluate(host_evidence(platform))["HOST-002"]

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code == "not_supported"
    assert result.collection_status is CollectionStatus.NOT_SUPPORTED
    assert result.evidence_ids == []
