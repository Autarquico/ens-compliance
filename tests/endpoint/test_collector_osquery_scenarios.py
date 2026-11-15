"""Escenarios de la spec collector-osquery a través de `evaluate_host_checks`.

Invariante de todos los tests: ningún `PASS` sin evidencia positiva, vigente y citada.
"""

from datetime import timedelta

import pytest

from ens_evidence.endpoint.agent_view import Admission, identity_digest
from ens_evidence.endpoint.checks import AGENT_SAYS, ATTESTED, evaluate_host_checks
from ens_evidence.endpoint.config import HostEnrollment
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import AT, COLLECTED, LAPTOP, LINUX_ROWS, SERVER, assets
from .test_agent_view import (
    DARWIN_FULL,
    GEN,
    LAPTOP_UUID,
    LINUX_FULL,
    agent,
    alf_rows,
    cfg,
    contact,
    script,
)

ADMITTED = Admission(COLLECTED, GEN)
HOST_CHECKS = tuple(f"HOST-00{n}" for n in range(3, 10))
EVALUATED_AFTER_ALL_EXPIRED = COLLECTED + timedelta(days=90)


def evaluate(*, scripts=(), agents=(), admission=None, at=AT, asset=LAPTOP, config_=None):
    """Punto de entrada público + invariante: todo PASS cita la evidencia que lo sostiene."""
    admissions = {asset: admission} if admission else {}
    results = evaluate_host_checks(
        assets(asset),
        list(scripts),
        config_ or cfg(),
        at,
        agent_evidence=list(agents),
        admissions=admissions,
    )
    by_id = {r.check_id: r for r in results}
    for result in results:
        if result.status is CheckStatus.PASS:
            assert result.evidence_ids, f"{result.check_id}: PASS sin evidencia citada"
    return by_id


def none_pass(results, check_ids=HOST_CHECKS):
    return all(results[c].status is not CheckStatus.PASS for c in check_ids)


# --- Scenario: agente desconectado -----------------------------------------------------------


def test_un_contacto_mas_antiguo_que_el_umbral_da_fail_en_host_002():
    silent = contact(AT - timedelta(hours=25))

    result = evaluate(agents=[silent], admission=ADMITTED)["HOST-002"]

    assert result.status is CheckStatus.FAIL
    assert result.reason_code == "agent_silent"
    assert result.evidence_ids == ["ct-1"]


def test_agente_desconectado_da_fail_aunque_toda_la_demas_evidencia_haya_caducado():
    results = evaluate(
        scripts=[script(rows=DARWIN_FULL)],
        agents=[agent(DARWIN_FULL), contact(COLLECTED + timedelta(days=1))],
        admission=ADMITTED,
        at=EVALUATED_AFTER_ALL_EXPIRED,
    )

    assert results["HOST-002"].status is CheckStatus.FAIL
    assert results["HOST-001"].status is not CheckStatus.PASS
    assert none_pass(results)


def test_agente_desconectado_no_invalida_los_otros_checks_con_evidencia_vigente():
    results = evaluate(
        agents=[agent(DARWIN_FULL), contact(AT - timedelta(hours=25))], admission=ADMITTED
    )

    assert results["HOST-002"].status is CheckStatus.FAIL
    assert results["HOST-005"].status is CheckStatus.PASS


def test_el_ultimo_contacto_es_el_que_cuenta_entre_varios():
    old = contact(AT - timedelta(days=3), evidence_id="ct-old")
    recent = contact(AT - timedelta(hours=1), evidence_id="ct-new")

    result = evaluate(agents=[old, recent], admission=ADMITTED)["HOST-002"]

    assert result.status is CheckStatus.PASS
    assert result.evidence_ids == ["ct-new"]


def test_agente_revocado_con_contacto_reciente_no_da_pass():
    revoked_generation = contact(AT - timedelta(hours=1), generation=1)
    readmitted = Admission(AT - timedelta(hours=30), 2)

    results = evaluate(agents=[revoked_generation], admission=readmitted)

    assert results["HOST-002"].status is not CheckStatus.PASS


def test_agente_sin_alta_vigente_con_contacto_reciente_no_da_pass():
    results = evaluate(agents=[contact(AT - timedelta(hours=1))], admission=None)

    assert results["HOST-002"].status is not CheckStatus.PASS


# --- Scenario: misma evaluación que v0.1 -----------------------------------------------------

PARITY_CASES = {
    "macos-conforme": ("darwin", LAPTOP, DARWIN_FULL),
    "macos-firewall-apagado": ("darwin", LAPTOP, DARWIN_FULL | alf_rows("0")),
    "macos-sin-cifrar": (
        "darwin",
        LAPTOP,
        DARWIN_FULL
        | {
            "disk_encryption_darwin": [
                {"name": "/dev/disk3s1", "encrypted": "0", "filevault_status": "off"}
            ]
        },
    ),
    "macos-sin-actualizaciones-automaticas": (
        "darwin",
        LAPTOP,
        DARWIN_FULL
        | {
            "software_update_policy": [
                {
                    "name": "AutomaticallyInstallMacOSUpdates",
                    "value": "0",
                    "user_scoped": "0",
                    "manual": "0",
                }
            ]
        },
    ),
    "linux-conforme": ("linux", SERVER, LINUX_FULL),
    "linux-sin-cifrar": (
        "linux",
        SERVER,
        LINUX_FULL | {"disk_encryption": [{"name": "/dev/mapper/vg-root", "encrypted": "0"}]},
    ),
    "linux-freshclam-parado": (
        "linux",
        SERVER,
        LINUX_FULL
        | {"clamav_units": [{"id": "clamav-freshclam.service", "active_state": "failed"}]},
    ),
}


@pytest.mark.parametrize("case", PARITY_CASES)
def test_las_mismas_filas_por_script_y_por_agente_dan_los_mismos_estados(case):
    platform, asset, rows = PARITY_CASES[case]

    from_script = evaluate(scripts=[script(platform, rows=rows)], asset=asset)
    from_agent = evaluate(agents=[agent(rows, platform=platform, asset=asset)], asset=asset)

    assert from_script["HOST-001"].status is CheckStatus.PASS
    assert from_agent["HOST-001"].status is CheckStatus.PASS
    assert from_agent.keys() == from_script.keys()
    for check_id in HOST_CHECKS:
        if check_id not in from_script:  # HOST-009 no aplica en Linux
            continue
        assert from_agent[check_id].status is from_script[check_id].status, check_id
        assert from_agent[check_id].reason_code == from_script[check_id].reason_code, check_id


def test_sin_las_consultas_de_antivirus_ni_el_script_ni_el_agente_dan_pass():
    """Asimetría diseñada: consulta ausente es ERROR en el script y UNKNOWN no_data en el agente."""
    from_script = evaluate(scripts=[script("linux", rows=LINUX_ROWS)], asset=SERVER)
    from_agent = evaluate(agents=[agent(LINUX_ROWS, platform="linux", asset=SERVER)], asset=SERVER)

    assert from_script["HOST-008"].status is not CheckStatus.PASS
    assert from_agent["HOST-008"].status is CheckStatus.UNKNOWN
    assert from_agent["HOST-008"].reason_code == "no_data"


def test_el_caso_de_paridad_con_fail_realmente_contiene_un_fail():
    platform, asset, rows = PARITY_CASES["macos-firewall-apagado"]

    results = evaluate(scripts=[script(platform, rows=rows)], asset=asset)

    assert results["HOST-005"].status is CheckStatus.FAIL


@pytest.mark.parametrize("case", ["macos-conforme", "macos-firewall-apagado", "linux-conforme"])
def test_con_evidencia_de_script_y_de_agente_a_la_vez_los_resultados_coinciden_con_cada_fuente(
    case,
):
    platform, asset, rows = PARITY_CASES[case]
    only_script = evaluate(scripts=[script(platform, rows=rows)], asset=asset)

    both = evaluate(
        scripts=[script(platform, rows=rows)],
        agents=[agent(rows, platform=platform, asset=asset)],
        asset=asset,
    )

    assert both["HOST-001"].status is CheckStatus.PASS
    for check_id in HOST_CHECKS:
        if check_id in only_script:
            assert both[check_id].status is only_script[check_id].status, check_id


def test_el_prefijo_del_motivo_indica_la_fuente_sin_cambiar_el_estado():
    from_script = evaluate(scripts=[script(rows=DARWIN_FULL)])["HOST-005"]
    from_agent = evaluate(agents=[agent(DARWIN_FULL)])["HOST-005"]

    assert from_script.reason.startswith(ATTESTED)
    assert from_agent.reason.startswith(AGENT_SAYS)
    assert from_script.status is from_agent.status


# --- La última observación manda por check ---------------------------------------------------

NEXT_DAY = COLLECTED + timedelta(days=1)


def test_un_error_mas_reciente_del_script_no_queda_tapado_por_un_pass_anterior_del_agente():
    broken = script(
        rows=alf_rows("1") | {"alf": []},
        at=NEXT_DAY,
        evidence_id="sc-new",
        statuses={"alf": CollectionStatus.ERROR},
    )

    results = evaluate(scripts=[broken], agents=[agent(DARWIN_FULL)])

    assert results["HOST-005"].status is not CheckStatus.PASS
    assert results["HOST-005"].evidence_ids == ["sc-new"]


def test_un_pass_mas_reciente_del_agente_manda_sobre_un_fail_anterior_del_script():
    results = evaluate(
        scripts=[script(rows=alf_rows("0"))],
        agents=[agent(DARWIN_FULL, at=NEXT_DAY)],
    )

    assert results["HOST-005"].status is CheckStatus.PASS
    assert results["HOST-005"].evidence_ids == ["ag-1"]


def test_cada_check_elige_su_fuente_segun_sus_propias_consultas():
    results = evaluate(
        scripts=[script(rows=DARWIN_FULL)],
        agents=[agent(alf_rows("0"), at=NEXT_DAY)],
    )

    assert results["HOST-005"].status is CheckStatus.FAIL
    assert results["HOST-004"].reason.startswith(ATTESTED)
    assert results["HOST-004"].status is CheckStatus.PASS


# --- Contacto reciente sin snapshots ---------------------------------------------------------


def test_contacto_reciente_sin_snapshots_pasa_host_002_y_nada_mas():
    results = evaluate(agents=[contact(AT - timedelta(minutes=5))], admission=ADMITTED)

    assert results["HOST-002"].status is CheckStatus.PASS
    assert none_pass(results, ("HOST-001", *HOST_CHECKS))


# --- Respuestas en vivo ----------------------------------------------------------------------


def test_una_respuesta_en_vivo_mas_reciente_que_el_snapshot_programado_se_ignora():
    live_fail = agent(alf_rows("0"), at=AT - timedelta(minutes=1), live=True, evidence_id="live")

    results = evaluate(agents=[agent(DARWIN_FULL), live_fail])

    assert results["HOST-005"].status is CheckStatus.PASS
    assert "live" not in results["HOST-005"].evidence_ids


def test_una_respuesta_en_vivo_no_rejuvenece_un_snapshot_caducado():
    live = agent(DARWIN_FULL, at=EVALUATED_AFTER_ALL_EXPIRED - timedelta(hours=1), live=True)

    results = evaluate(
        agents=[agent(DARWIN_FULL), live],
        at=EVALUATED_AFTER_ALL_EXPIRED,
    )

    assert none_pass(results, ("HOST-001", *HOST_CHECKS))


def test_un_equipo_con_solo_respuestas_en_vivo_no_da_pass_en_ningun_check():
    results = evaluate(agents=[agent(DARWIN_FULL, live=True)])

    assert none_pass(results, ("HOST-001", *HOST_CHECKS))


def test_las_respuestas_en_vivo_no_cuentan_como_contacto_de_host_002():
    live = agent(DARWIN_FULL, at=AT - timedelta(minutes=1), live=True)

    results = evaluate(agents=[live], admission=Admission(AT - timedelta(hours=30), GEN))

    assert results["HOST-002"].status is CheckStatus.FAIL


# --- Identidad -------------------------------------------------------------------------------


def test_si_cambia_la_identidad_inscrita_la_evidencia_anterior_del_agente_no_sirve():
    reenrolled = cfg()
    reenrolled.hosts[LAPTOP] = HostEnrollment(
        hardware_uuid="99999999-2222-3333-4444-555555555555", hostname=f"{LAPTOP}.test"
    )

    results = evaluate(
        agents=[agent(DARWIN_FULL), contact(AT)],
        admission=Admission(AT - timedelta(hours=30), GEN),
        config_=reenrolled,
    )

    assert none_pass(results, ("HOST-001", *HOST_CHECKS))
    assert results["HOST-002"].status is CheckStatus.FAIL


def test_el_uuid_inscrito_en_mayusculas_sigue_casando_con_resultados_y_contactos():
    upper = cfg()
    upper.hosts[LAPTOP] = HostEnrollment(
        hardware_uuid=LAPTOP_UUID.upper(), hostname=f"{LAPTOP}.test"
    )

    results = evaluate(
        agents=[agent(DARWIN_FULL), contact(AT - timedelta(hours=1))],
        admission=ADMITTED,
        config_=upper,
    )

    assert results["HOST-005"].status is CheckStatus.PASS
    assert results["HOST-002"].status is CheckStatus.PASS


def test_la_identidad_del_agente_es_el_sha256_completo_del_uuid_normalizado():
    assert identity_digest(LAPTOP_UUID.upper()) == identity_digest(LAPTOP_UUID)
    assert len(identity_digest(LAPTOP_UUID)) == 64


def test_la_evidencia_de_agente_anterior_a_la_identidad_no_da_pass_ni_contacto():
    legacy_contact = contact(AT - timedelta(hours=1))
    del legacy_contact.result.data["agent"]["identity_sha256"]

    results = evaluate(
        agents=[agent(DARWIN_FULL, identity="legacy"), legacy_contact],
        admission=Admission(AT - timedelta(hours=30), GEN),
    )

    assert none_pass(results, ("HOST-001", *HOST_CHECKS))
    assert results["HOST-002"].status is CheckStatus.FAIL


def test_un_contacto_con_identidad_basura_no_rompe_la_evaluacion():
    junk = contact(AT)
    junk.result.data["agent"]["identity_sha256"] = ["no", "es", "texto"]

    results = evaluate(agents=[junk], admission=Admission(AT - timedelta(hours=30), GEN))

    assert results["HOST-002"].status is not CheckStatus.PASS


# --- Vigencia --------------------------------------------------------------------------------


def test_sin_valid_until_en_el_manifiesto_caduca_por_edad_exactamente_a_los_30_dias():
    evidence = agent(DARWIN_FULL, valid_until=None)
    limit = COLLECTED + timedelta(days=30)

    before = evaluate(agents=[evidence], at=limit - timedelta(seconds=1))
    exact = evaluate(agents=[evidence], at=limit)

    assert before["HOST-001"].status is CheckStatus.PASS
    assert exact["HOST-001"].status is not CheckStatus.PASS
    assert exact["HOST-005"].reason_code == "evidence_expired"


def test_el_instante_exacto_de_valid_until_ya_esta_caducado():
    expiry = COLLECTED + timedelta(days=2)
    evidence = agent(DARWIN_FULL, valid_until=expiry)

    before = evaluate(agents=[evidence], at=expiry - timedelta(seconds=1))
    exact = evaluate(agents=[evidence], at=expiry)

    assert before["HOST-005"].status is CheckStatus.PASS
    assert exact["HOST-005"].reason_code == "evidence_expired"
    assert none_pass(exact, ("HOST-001", *HOST_CHECKS))


def test_un_snapshot_con_hora_futura_no_da_pass_ni_se_sustituye_por_uno_anterior():
    old = agent(DARWIN_FULL, evidence_id="ag-old")
    future = agent(DARWIN_FULL, at=AT + timedelta(hours=2), evidence_id="ag-future")

    results = evaluate(agents=[old, future])

    assert results["HOST-005"].reason_code == "evidence_in_future"
    assert none_pass(results, ("HOST-001", *HOST_CHECKS))


def test_una_consulta_con_hora_futura_dentro_de_un_lote_antiguo_no_da_pass():
    batch = agent(DARWIN_FULL, query_at={"alf": AT + timedelta(hours=2)})

    results = evaluate(agents=[batch])

    assert results["HOST-005"].status is not CheckStatus.PASS


# --- Lotes con fechas distintas --------------------------------------------------------------


def test_xprotect_reciente_con_inventario_antiguo_se_evalua_contra_su_propia_hora():
    inventory = agent(
        {k: v for k, v in DARWIN_FULL.items() if k != "xprotect_bundle"}, evidence_id="ag-inv"
    )
    xprotect = agent(
        {"xprotect_bundle": [{"bundle_short_version": "2200", "mtime": str(_epoch(AT, hours=1))}]},
        at=AT,
        evidence_id="ag-xp",
    )

    results = evaluate(agents=[inventory, xprotect], at=AT + timedelta(minutes=1))

    assert results["HOST-008"].status is CheckStatus.PASS
    assert results["HOST-008"].evidence_ids == ["ag-xp"]
    assert results["HOST-007"].evidence_ids == ["ag-inv"]


def test_xprotect_caducado_en_su_propio_lote_da_fail_aunque_el_inventario_sea_anterior():
    inventory = agent(
        {k: v for k, v in DARWIN_FULL.items() if k != "xprotect_bundle"}, evidence_id="ag-inv"
    )
    stale = [{"bundle_short_version": "2100", "mtime": str(_epoch(AT, days=40))}]
    xprotect = agent({"xprotect_bundle": stale}, at=AT, evidence_id="ag-xp")

    results = evaluate(agents=[inventory, xprotect], at=AT + timedelta(minutes=1))

    assert results["HOST-008"].status is CheckStatus.FAIL


def _epoch(moment, *, days=0, hours=0):
    return int((moment - timedelta(days=days, hours=hours)).timestamp())


# --- Consultas ausentes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check_id", "missing"),
    [
        ("HOST-003", "os_version"),
        ("HOST-004", "root_mount"),
        ("HOST-004", "disk_encryption_darwin"),
        ("HOST-005", "alf"),
        ("HOST-005", "osquery_info"),
        ("HOST-006", "software_update_policy"),
        ("HOST-007", "apps"),
        ("HOST-008", "xprotect_bundle"),
        ("HOST-009", "gatekeeper"),
    ],
)
def test_una_consulta_aplicable_que_falta_da_unknown_no_data(check_id, missing):
    rows = {k: v for k, v in DARWIN_FULL.items() if k != missing}

    results = evaluate(agents=[agent(rows)])

    assert results[check_id].status is CheckStatus.UNKNOWN
    assert results[check_id].reason_code == "no_data"


@pytest.mark.parametrize("missing", ["clamav_signatures", "clamav_units"])
def test_en_linux_falta_una_consulta_de_antivirus_da_unknown_no_data(missing):
    rows = {k: v for k, v in LINUX_FULL.items() if k != missing}

    results = evaluate(agents=[agent(rows, platform="linux", asset=SERVER)], asset=SERVER)

    assert results["HOST-008"].status is CheckStatus.UNKNOWN
    assert results["HOST-008"].reason_code == "no_data"


# --- Windows solo agente ---------------------------------------------------------------------


def test_windows_solo_agente_pasa_001_y_002_y_deja_003_a_009_en_unknown_not_supported():
    windows = agent({"os_version": [{"name": "Windows"}]}, platform="windows")

    results = evaluate(
        agents=[windows, contact(AT - timedelta(hours=2), platform="windows")],
        admission=ADMITTED,
    )

    assert results["HOST-001"].status is CheckStatus.PASS
    assert results["HOST-002"].status is CheckStatus.PASS
    for check_id in HOST_CHECKS:
        assert results[check_id].status is CheckStatus.UNKNOWN, check_id
        assert results[check_id].reason_code == "not_supported", check_id


def test_windows_solo_agente_desconectado_da_fail_en_002_y_sigue_sin_pass_en_el_resto():
    windows = agent({"os_version": [{"name": "Windows"}]}, platform="windows")

    results = evaluate(
        agents=[windows, contact(AT - timedelta(hours=30), platform="windows")],
        admission=ADMITTED,
    )

    assert results["HOST-002"].status is CheckStatus.FAIL
    assert none_pass(results, HOST_CHECKS)


# --- Solo script (comportamiento de v0.1 intacto) --------------------------------------------


def test_un_host_solo_script_conserva_sus_estados_y_host_002_no_pasa():
    results = evaluate(scripts=[script(rows=DARWIN_FULL | {"alf": [{"global_state": "0"}]})])

    assert results["HOST-001"].status is CheckStatus.PASS
    assert results["HOST-005"].status is CheckStatus.FAIL
    assert results["HOST-002"].status is not CheckStatus.PASS


def test_sin_ninguna_evidencia_nada_da_pass():
    results = evaluate()

    assert none_pass(results, ("HOST-001", "HOST-002", *HOST_CHECKS))
