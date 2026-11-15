"""Lector del agente osquery, selección de fuente por check y HOST-002 (solo fixtures)."""

from datetime import timedelta

import pytest

from ens_evidence.endpoint.agent_view import Admission, identity_digest
from ens_evidence.endpoint.checks import AGENT_SAYS, ATTESTED, evaluate_host_checks
from ens_evidence.endpoint.config import HostEnrollment
from ens_evidence.endpoint.evidence import AGENT_SOURCE, CONTACT_SOURCE, HostEvidence
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import (
    AT,
    COLLECTED,
    DARWIN_ROWS,
    LAPTOP,
    LINUX_ROWS,
    SERVER,
    assets,
    config,
    host_evidence,
)

LAPTOP_UUID = "11111111-2222-3333-4444-555555555555"
SERVER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UUIDS = {LAPTOP: LAPTOP_UUID, SERVER: SERVER_UUID}
EPOCH_DAILY = int((AT - timedelta(days=1)).timestamp())
XPROTECT = [{"bundle_short_version": "2200", "mtime": str(EPOCH_DAILY)}]
GATEKEEPER = [{"assessments_enabled": "1"}]
CLAMAV = [{"path": "/var/lib/clamav/daily.cvd", "mtime": str(EPOCH_DAILY), "size": "100"}]
FRESHCLAM = [{"id": "clamav-freshclam.service", "active_state": "active"}]
DARWIN_FULL = DARWIN_ROWS | {"xprotect_bundle": XPROTECT, "gatekeeper": GATEKEEPER}
LINUX_FULL = LINUX_ROWS | {"clamav_signatures": CLAMAV, "clamav_units": FRESHCLAM}
GEN = 1


def alf_rows(state):
    """Las consultas que lee HOST-005 en macOS, con `alf.global_state` a `state`."""
    keep = {key: DARWIN_ROWS[key] for key in ("os_version", "osquery_info")}
    return keep | {"alf": [{"global_state": state}]}


def cfg(**changes):
    base = config()
    hosts = {
        asset_id: HostEnrollment(
            hardware_uuid=UUIDS.get(asset_id, "x"), hostname=f"{asset_id}.test"
        )
        for asset_id in base.hosts
        if asset_id in UUIDS
    }
    return base.model_copy(update={"hosts": hosts, **changes})


def script(platform="darwin", *, rows=None, at=COLLECTED, evidence_id="sc-1", **kwargs):
    asset = LAPTOP if platform == "darwin" else SERVER
    return host_evidence(
        platform,
        rows=rows or {},
        collected_at=at,
        evidence_id=evidence_id,
        identity=(UUIDS[asset], f"{asset}.test"),
        **kwargs,
    )


def agent(
    rows,
    *,
    asset=LAPTOP,
    at=COLLECTED,
    platform="darwin",
    evidence_id="ag-1",
    valid_until=None,
    identity="ok",
    live=False,
    statuses=None,
    query_at=None,
):
    block = {"platform": platform, "host_identifier": "abcd1234"}
    if identity == "ok":
        block["identity_sha256"] = identity_digest(UUIDS[asset])
    elif identity == "other":
        block["identity_sha256"] = identity_digest("99999999-2222-3333-4444-555555555555")
    if live:
        block["query_run_id"] = "run-1"
    queries = {
        query_id: {
            "status": (statuses or {}).get(query_id, "SUCCESS"),
            "rows": query_rows,
            "collected_at": (query_at or {}).get(query_id, at).isoformat(),
        }
        for query_id, query_rows in rows.items()
    }
    result = CollectionResult(
        source=AGENT_SOURCE,
        collector=AGENT_SOURCE,
        collected_at=at,
        asset_id=asset,
        collection_status=CollectionStatus.SUCCESS,
        data={"queries": queries, "agent": block},
    )
    return HostEvidence(evidence_id, valid_until, result)


def contact(at, *, asset=LAPTOP, generation=GEN, evidence_id="ct-1", platform="darwin"):
    result = CollectionResult(
        source=CONTACT_SOURCE,
        collector=CONTACT_SOURCE,
        collected_at=at,
        asset_id=asset,
        collection_status=CollectionStatus.SUCCESS,
        data={
            "agent": {
                "identity_sha256": identity_digest(UUIDS[asset]),
                "platform": platform,
                "generation": generation,
            }
        },
    )
    return HostEvidence(evidence_id, None, result)


def run(*, scripts=(), agents=(), admission=None, at=AT, asset=LAPTOP, config_=None):
    admissions = {asset: admission} if admission else {}
    results = evaluate_host_checks(
        assets(asset),
        list(scripts),
        config_ or cfg(),
        at,
        agent_evidence=list(agents),
        admissions=admissions,
    )
    return {r.check_id: r for r in results}


# --- selección de fuente por check -------------------------------------------------------


def test_solo_agente_evalua_con_su_prefijo_y_cita_sus_snapshots():
    results = run(agents=[agent(DARWIN_FULL)])

    assert results["HOST-001"].status is CheckStatus.PASS
    assert results["HOST-001"].reason.startswith(AGENT_SAYS)
    assert results["HOST-005"].status is CheckStatus.PASS
    assert results["HOST-005"].reason.startswith(AGENT_SAYS)
    assert results["HOST-005"].evidence_ids == ["ag-1"]


def test_un_script_solo_conserva_su_prefijo():
    results = run(scripts=[script(rows=DARWIN_FULL)])

    assert results["HOST-005"].reason.startswith(ATTESTED)
    assert results["HOST-005"].evidence_ids == ["sc-1"]


def test_un_fail_mas_reciente_del_agente_manda_sobre_un_script_anterior_que_pasaba():
    results = run(
        scripts=[script(rows=DARWIN_FULL)],
        agents=[agent(alf_rows("0"), at=COLLECTED + timedelta(days=1))],
    )

    assert results["HOST-005"].status is CheckStatus.FAIL
    assert results["HOST-005"].reason.startswith(AGENT_SAYS)
    assert results["HOST-006"].reason.startswith(ATTESTED)  # el agente no vio software_update


def test_un_fail_mas_reciente_del_script_manda_sobre_un_agente_anterior_que_pasaba():
    results = run(
        scripts=[script(rows=alf_rows("0"), at=COLLECTED + timedelta(days=1))],
        agents=[agent(DARWIN_FULL)],
    )

    assert results["HOST-005"].status is CheckStatus.FAIL


def test_un_error_mas_reciente_del_agente_no_deja_pasar_el_script_anterior():
    results = run(
        scripts=[script(rows=DARWIN_FULL)],
        agents=[
            agent(
                alf_rows("1") | {"alf": []},
                at=COLLECTED + timedelta(days=1),
                statuses={"alf": "ERROR"},
            )
        ],
    )

    assert results["HOST-005"].status is not CheckStatus.PASS
    assert results["HOST-005"].collection_status is CollectionStatus.ERROR


def test_la_fuente_ganadora_caducada_no_recupera_la_otra():
    newer_but_short = agent(
        DARWIN_FULL, at=COLLECTED + timedelta(days=1), valid_until=COLLECTED + timedelta(days=2)
    )

    results = run(scripts=[script(rows=DARWIN_FULL)], agents=[newer_but_short])

    assert results["HOST-005"].status is CheckStatus.UNKNOWN
    assert results["HOST-005"].reason_code == "evidence_expired"
    assert results["HOST-001"].status is CheckStatus.PASS  # el script sigue vigente


def test_sin_vencimiento_de_manifiesto_caduca_por_edad_en_el_instante_exacto():
    evidence = agent(DARWIN_FULL, valid_until=None)
    limit = COLLECTED + timedelta(days=30)

    just_before = run(agents=[evidence], at=limit - timedelta(microseconds=1))
    exact = run(agents=[evidence], at=limit)

    assert just_before["HOST-005"].status is CheckStatus.PASS
    assert exact["HOST-005"].reason_code == "evidence_expired"


def test_un_vencimiento_anticipado_del_manifiesto_prevalece():
    evidence = agent(DARWIN_FULL, valid_until=COLLECTED + timedelta(days=2))

    assert run(agents=[evidence])["HOST-005"].reason_code == "evidence_expired"


def test_snapshot_con_fecha_futura_es_unknown():
    evidence = agent(DARWIN_FULL, at=AT + timedelta(hours=1))

    results = run(agents=[evidence])

    assert results["HOST-005"].reason_code == "evidence_in_future"
    assert results["HOST-001"].reason_code == "evidence_in_future"


def test_se_toma_el_ultimo_snapshot_y_no_se_recupera_uno_anterior_valido():
    old = agent(alf_rows("1"), evidence_id="ag-old")
    newer_expired = agent(
        alf_rows("0"),
        at=COLLECTED + timedelta(days=1),
        evidence_id="ag-new",
        valid_until=COLLECTED + timedelta(days=2),
    )

    result = run(agents=[old, newer_expired])["HOST-005"]

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code != "no_data"


def test_la_respuesta_en_vivo_no_alimenta_la_vista():
    live = agent(alf_rows("0"), at=AT - timedelta(hours=1), live=True)

    results = run(agents=[agent(DARWIN_FULL), live])

    assert results["HOST-005"].status is CheckStatus.PASS
    assert results["HOST-005"].evidence_ids == ["ag-1"]


def test_solo_respuestas_en_vivo_no_cubren_al_equipo():
    results = run(agents=[agent(DARWIN_FULL, live=True)])

    assert results["HOST-001"].status is CheckStatus.FAIL
    assert results["HOST-001"].reason_code == "no_evidence"


@pytest.mark.parametrize("identity", ["legacy", "other"])
def test_evidencia_sin_identidad_o_de_otro_equipo_se_ignora(identity):
    results = run(agents=[agent(DARWIN_FULL, identity=identity)])

    assert results["HOST-001"].reason_code == "no_evidence"
    assert results["HOST-005"].status is CheckStatus.UNKNOWN


def test_el_uuid_inscrito_en_mayusculas_casa_con_la_identidad_normalizada():
    upper = cfg()
    upper.hosts[LAPTOP] = HostEnrollment(
        hardware_uuid=LAPTOP_UUID.upper(), hostname=f"{LAPTOP}.test"
    )

    results = run(agents=[agent(DARWIN_FULL)], config_=upper)

    assert results["HOST-005"].status is CheckStatus.PASS


def test_la_fecha_de_cada_consulta_gobierna_las_firmas_no_la_mas_antigua_del_lote():
    batch = agent(
        DARWIN_FULL,
        at=COLLECTED,
        query_at={"xprotect_bundle": AT, "gatekeeper": AT},
    )

    results = run(agents=[batch], at=AT + timedelta(minutes=1))

    assert results["HOST-008"].status is CheckStatus.PASS
    assert results["HOST-009"].status is CheckStatus.PASS


def test_consulta_necesaria_ausente_es_unknown_no_data_no_error():
    rows = {k: v for k, v in DARWIN_FULL.items() if k != "alf"}

    result = run(agents=[agent(rows)])["HOST-005"]

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code == "no_data"
    assert result.collection_status is CollectionStatus.SUCCESS


def test_consulta_vacia_observada_no_es_ausente():
    result = run(agents=[agent(DARWIN_FULL | {"alf": []})])["HOST-005"]

    assert "aún no ha enviado" not in result.reason
    assert result.status is not CheckStatus.PASS


def test_windows_solo_agente_es_not_supported_salvo_hosts_001_y_002():
    evidence = agent({"os_version": [{"name": "Windows"}]}, platform="windows")

    results = run(
        agents=[evidence, contact(AT, platform="windows")],
        admission=Admission(COLLECTED, GEN),
    )

    for check_id in (
        "HOST-003",
        "HOST-004",
        "HOST-005",
        "HOST-006",
        "HOST-007",
        "HOST-008",
        "HOST-009",
    ):
        assert results[check_id].status is CheckStatus.UNKNOWN
        assert results[check_id].reason_code == "not_supported"
        assert "Windows" in results[check_id].reason
    assert results["HOST-001"].status is CheckStatus.PASS
    assert results["HOST-002"].status is CheckStatus.PASS


def test_plataforma_desconocida_del_agente_es_unknown():
    evidence = agent(DARWIN_FULL, platform=None)

    assert run(agents=[evidence])["HOST-003"].reason_code == "no_data"


@pytest.mark.parametrize(
    ("platform", "rows", "asset"),
    [("darwin", DARWIN_FULL, LAPTOP), ("linux", LINUX_FULL, SERVER)],
)
def test_paridad_script_y_agente_dan_los_mismos_estados(platform, rows, asset):
    from_script = run(scripts=[script(platform, rows=rows)], asset=asset)
    from_agent = run(agents=[agent(rows, platform=platform, asset=asset)], asset=asset)

    for check_id in [f"HOST-00{n}" for n in range(1, 10) if n != 2]:
        if check_id in from_script:
            assert from_agent[check_id].status is from_script[check_id].status, check_id
    assert from_script["HOST-001"].status is CheckStatus.PASS


# --- HOST-002 ------------------------------------------------------------------------------

ADMITTED = Admission(COLLECTED, GEN)


def test_host_002_pasa_con_contacto_reciente_y_lo_cita():
    results = run(agents=[contact(AT - timedelta(hours=3))], admission=ADMITTED)

    assert results["HOST-002"].status is CheckStatus.PASS
    assert results["HOST-002"].evidence_ids == ["ct-1"]


def test_host_002_umbral_exacto_pasa_y_un_instante_mas_falla():
    last = AT - timedelta(hours=24)

    exact = run(agents=[contact(last)], admission=ADMITTED)["HOST-002"]
    over = run(agents=[contact(last - timedelta(microseconds=1))], admission=ADMITTED)["HOST-002"]

    assert exact.status is CheckStatus.PASS
    assert over.status is CheckStatus.FAIL
    assert over.reason_code == "agent_silent"
    assert over.evidence_ids == ["ct-1"]


def test_host_002_usa_el_umbral_configurado():
    custom = cfg(max_agent_silence_hours=2)

    result = run(agents=[contact(AT - timedelta(hours=3))], admission=ADMITTED, config_=custom)

    assert result["HOST-002"].status is CheckStatus.FAIL


def test_host_002_contacto_sin_snapshots_pasa_aunque_el_host_no_este_cubierto():
    results = run(agents=[contact(AT)], admission=ADMITTED)

    assert results["HOST-002"].status is CheckStatus.PASS
    assert results["HOST-001"].status is CheckStatus.FAIL


def test_host_002_sin_contacto_y_alta_antigua_falla():
    result = run(admission=Admission(AT - timedelta(hours=25), GEN))["HOST-002"]

    assert result.status is CheckStatus.FAIL
    assert result.evidence_ids == []


def test_host_002_sin_contacto_y_alta_reciente_es_unknown():
    result = run(admission=Admission(AT - timedelta(hours=1), GEN))["HOST-002"]

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code == "no_contact"


def test_host_002_sin_alta_o_revocada_es_unknown_aunque_haya_contactos():
    result = run(agents=[contact(AT)])["HOST-002"]

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code == "no_agent"


def test_host_002_ignora_contactos_de_una_generacion_revocada():
    result = run(
        agents=[contact(AT, generation=1)], admission=Admission(AT - timedelta(hours=30), 2)
    )["HOST-002"]

    assert result.status is CheckStatus.FAIL
    assert result.evidence_ids == []


def test_host_002_contacto_con_fecha_futura_es_unknown():
    result = run(agents=[contact(AT + timedelta(hours=1))], admission=ADMITTED)["HOST-002"]

    assert result.status is CheckStatus.UNKNOWN
    assert result.reason_code == "evidence_in_future"


def test_host_002_ignora_contactos_de_otra_identidad():
    foreign = contact(AT)
    foreign.result.data["agent"]["identity_sha256"] = identity_digest(
        "99999999-2222-3333-4444-555555555555"
    )

    result = run(agents=[foreign], admission=Admission(AT - timedelta(hours=30), GEN))["HOST-002"]

    assert result.status is CheckStatus.FAIL


def test_host_002_es_independiente_de_la_vigencia_de_los_demas_checks():
    late = COLLECTED + timedelta(days=90)
    silent = contact(COLLECTED + timedelta(days=1))

    results = run(
        scripts=[script(rows=DARWIN_FULL)],
        agents=[silent],
        admission=ADMITTED,
        at=late,
    )

    assert results["HOST-002"].status is CheckStatus.FAIL
    assert results["HOST-005"].reason_code == "evidence_expired"


def test_solo_script_sigue_igual_salvo_host_002():
    results = run(scripts=[script(rows=DARWIN_FULL)])

    assert results["HOST-001"].status is CheckStatus.PASS
    assert results["HOST-002"].reason_code == "no_agent"
