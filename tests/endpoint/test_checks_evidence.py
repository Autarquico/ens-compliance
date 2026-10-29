"""Vigencia, última ejecución, estados de consulta y contrato de los CheckResult de HOST-*."""

from datetime import timedelta

import pytest

from ens_evidence.endpoint.checks import HOST_CHECKS, evaluate_host_checks
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import (
    AT,
    COLLECTED,
    LAPTOP,
    SERVER,
    VALID_UNTIL,
    assets,
    config,
    evaluate,
    evaluate_all,
    host_evidence,
)

ST = CollectionStatus


def test_la_evidencia_vigente_se_evalua_en_los_siete_checks():
    results = evaluate(host_evidence())

    assert list(results) == list(HOST_CHECKS)


def test_un_instante_antes_de_valid_until_la_evidencia_sigue_vigente():
    results = evaluate(host_evidence(), at=VALID_UNTIL - timedelta(microseconds=1))

    assert results["HOST-001"].status is CheckStatus.PASS


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(seconds=1), timedelta(days=90)])
def test_con_evaluated_at_en_o_tras_valid_until_todos_los_checks_son_unknown_caducada(delay):
    results = evaluate(host_evidence(), at=VALID_UNTIL + delay)

    assert {r.status for r in results.values()} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in results.values()} == {"evidence_expired"}
    assert all(not r.evidence_ids or r.status is not CheckStatus.PASS for r in results.values())


def test_la_evidencia_con_fecha_futura_deja_todos_los_checks_unknown():
    future = host_evidence(collected_at=AT + timedelta(hours=1), valid_until=VALID_UNTIL)

    results = evaluate(future)

    assert {r.status for r in results.values()} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in results.values()} == {"evidence_in_future"}


def test_una_ejecucion_futura_no_hace_volver_a_una_anterior_que_pasaba():
    previous = host_evidence(evidence_id="ev-old")
    future = host_evidence(
        evidence_id="ev-new", collected_at=AT + timedelta(hours=1), valid_until=VALID_UNTIL
    )

    results = evaluate(previous, future)

    assert results["HOST-005"].status is CheckStatus.UNKNOWN
    assert results["HOST-001"].reason_code == "evidence_in_future"


def test_la_ejecucion_mas_nueva_que_falla_manda_sobre_una_anterior_que_pasaba():
    passing = host_evidence(evidence_id="ev-old", collected_at=COLLECTED)
    failing = host_evidence(
        evidence_id="ev-new",
        collected_at=COLLECTED + timedelta(days=1),
        rows={"alf": [{"global_state": "0"}]},
    )

    for ordered in ((passing, failing), (failing, passing)):
        result = evaluate(*ordered)["HOST-005"]
        assert result.status is CheckStatus.FAIL


def test_la_ejecucion_mas_nueva_con_consulta_en_error_no_vuelve_a_la_anterior():
    passing = host_evidence(evidence_id="ev-old")
    broken = host_evidence(
        evidence_id="ev-new",
        collected_at=COLLECTED + timedelta(days=1),
        statuses={"alf": ST.ERROR},
    )

    result = evaluate(passing, broken)["HOST-005"]

    assert result.status is CheckStatus.ERROR


def test_los_checks_citan_la_ejecucion_mas_reciente():
    old = host_evidence(evidence_id="ev-old")
    new = host_evidence(evidence_id="ev-new", collected_at=COLLECTED + timedelta(days=1))

    results = evaluate(old, new)

    assert results["HOST-005"].evidence_ids == ["ev-new"]


@pytest.mark.parametrize(
    ("check_id", "query_id"),
    [
        ("HOST-003", "os_version"),
        ("HOST-004", "disk_encryption"),
        ("HOST-004", "root_mount"),
        ("HOST-004", "filevault_status"),
        ("HOST-005", "alf"),
        ("HOST-006", "software_update_policy"),
        ("HOST-007", "apps"),
    ],
)
def test_una_tabla_sin_filas_es_unknown_nunca_pass(check_id, query_id):
    results = evaluate(host_evidence(rows={query_id: []}))

    assert results[check_id].status is CheckStatus.UNKNOWN


@pytest.mark.parametrize(
    ("collection", "status", "reason_code"),
    [
        (ST.ERROR, CheckStatus.ERROR, None),
        (ST.PERMISSION_DENIED, CheckStatus.UNKNOWN, "permission_denied"),
        (ST.NOT_SUPPORTED, CheckStatus.UNKNOWN, "not_supported"),
    ],
)
@pytest.mark.parametrize(
    ("check_id", "query_id"),
    [
        ("HOST-003", "os_version"),
        ("HOST-004", "disk_encryption"),
        ("HOST-005", "alf"),
        ("HOST-006", "software_update_policy"),
        ("HOST-007", "apps"),
    ],
)
def test_una_consulta_no_recogida_se_traduce_segun_el_adr_0002(
    check_id, query_id, collection, status, reason_code
):
    result = evaluate(host_evidence(statuses={query_id: collection}))[check_id]

    assert (result.status, result.reason_code, result.collection_status) == (
        status,
        reason_code,
        collection,
    )


def test_una_consulta_ausente_del_resultado_cuenta_como_error():
    evidence = host_evidence()
    del evidence.result.data["queries"]["alf"]

    assert evaluate(evidence)["HOST-005"].status is CheckStatus.ERROR


def test_todo_resultado_valida_contra_el_modelo_y_declara_la_atestacion_manual():
    healthy = host_evidence()
    linux = host_evidence("linux")
    expired = host_evidence(asset_id="lap-bea", evidence_id="ev-3")
    results = evaluate_all(
        healthy,
        linux,
        expired,
        asset_ids=(LAPTOP, SERVER, "lap-bea", "lap-sin-evidencia"),
        at=VALID_UNTIL + timedelta(days=1),
    ) + evaluate_all(healthy, linux, asset_ids=(LAPTOP, SERVER, "lap-sin-evidencia"))

    assert results
    for result in results:
        assert CheckResult.model_validate(result.model_dump()) == result
        assert result.subject == result.asset_id
        assert "atestación manual" in result.reason
        assert result.evaluated_at.tzinfo is not None


def test_todo_pass_cita_el_evidence_id_de_la_evidencia():
    results = evaluate_all(
        host_evidence(evidence_id="ev-lap"),
        host_evidence("linux", evidence_id="ev-srv"),
        asset_ids=(LAPTOP, SERVER),
    )

    passes = [r for r in results if r.status is CheckStatus.PASS]
    assert passes
    assert {r.asset_id: r.evidence_ids for r in passes if r.check_id == "HOST-001"} == {
        LAPTOP: ["ev-lap"],
        SERVER: ["ev-srv"],
    }
    assert all(r.evidence_ids for r in passes)


def test_un_umbral_endurecido_tras_importar_caduca_la_evidencia_aunque_valid_until_siga_lejos():
    strict = config().model_copy(update={"max_evidence_age_days": 7})
    evidence = host_evidence()

    before = evaluate(
        evidence, endpoint_config=strict, at=COLLECTED + timedelta(days=7, seconds=-1)
    )
    after = evaluate(evidence, endpoint_config=strict, at=COLLECTED + timedelta(days=7))

    assert before["HOST-001"].status is CheckStatus.PASS
    assert {r.reason_code for r in after.values()} == {"evidence_expired"}


def test_un_umbral_ampliado_no_resucita_lo_que_valid_until_ya_caduco():
    wide = config().model_copy(update={"max_evidence_age_days": 365})

    results = evaluate(host_evidence(), endpoint_config=wide, at=VALID_UNTIL)

    assert {r.reason_code for r in results.values()} == {"evidence_expired"}


def test_la_fecha_futura_dentro_del_margen_de_reloj_de_la_importacion_se_tolera():
    skewed = host_evidence(collected_at=AT + timedelta(minutes=4))

    assert evaluate(skewed)["HOST-001"].status is CheckStatus.PASS


def test_la_fecha_futura_mas_alla_del_margen_de_reloj_es_unknown():
    skewed = host_evidence(collected_at=AT + timedelta(minutes=6))

    assert evaluate(skewed)["HOST-001"].reason_code == "evidence_in_future"


def test_la_evidencia_de_otro_equipo_con_el_mismo_activo_no_pasa_ningun_check():
    replaced = host_evidence(identity=("uuid-otro", "lap-ana.test"))

    results = evaluate(replaced)

    assert results["HOST-001"].status is CheckStatus.FAIL
    assert {r.reason_code for r in results.values()} == {"enrollment_changed"}
    assert "otro equipo" in results["HOST-001"].reason
    assert all(r.evidence_ids == [] for r in results.values())


def test_el_cambio_de_hostname_inscrito_tambien_invalida_la_evidencia():
    renamed = host_evidence(identity=("uuid-lap-ana", "otro.test"))

    assert evaluate(renamed)["HOST-005"].reason_code == "enrollment_changed"


def test_la_identidad_se_compara_sin_distinguir_mayusculas():
    same = host_evidence(identity=("UUID-LAP-ANA", "LAP-ANA.TEST"))

    assert evaluate(same)["HOST-001"].status is CheckStatus.PASS


def test_evidencia_sin_identidad_no_pasa():
    evidence = host_evidence()
    del evidence.result.data["package"]["hardware_uuid"]

    assert evaluate(evidence)["HOST-001"].status is CheckStatus.FAIL


def test_con_evidencia_ilegible_ningun_host_vuelve_a_la_ejecucion_anterior_que_pasaba():
    previous = host_evidence(evidence_id="ev-old")
    ids = (LAPTOP, SERVER)

    results = evaluate_host_checks(assets(*ids), [previous], config(), AT, ["EV-1: YAML no válido"])

    assert len(results) == len(ids) * len(HOST_CHECKS)
    assert {r.status for r in results} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in results} == {"unreadable_evidence"}
    assert all("EV-1: YAML no válido" in r.reason and not r.evidence_ids for r in results)
