"""Scenarios de extremo a extremo: script + importación reales, checks y regla de preparación."""

import time
from datetime import timedelta

from ens_evidence.connectors.config import load_connector_config
from ens_evidence.endpoint.checks import HOST_CHECKS, evaluate_host_checks
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.endpoint.evidence import evidence_refs, load_host_evidence
from ens_evidence.evidence import list_manifests
from ens_evidence.models import (
    Applicability,
    BlockerKind,
    Measure,
    MeasureAssessment,
    Readiness,
    ReadinessInput,
    Review,
    Role,
    evaluate_readiness,
)
from ens_evidence.models.enums import CheckStatus
from ens_evidence.scope.inventory import load_assets

from .support import Answer, collect, import_package, utc_now

MANAGED_UPDATES = Answer(
    out=[
        {
            "domain": "com.apple.SoftwareUpdate",
            "name": "AutomaticallyInstallMacOSUpdates",
            "value": "1",
            "user_scoped": "0",
            "manual": "0",
        }
    ]
)


XPROTECT_PLIST = "/Library/Apple/System/Library/CoreServices/XProtect.bundle/Contents/Info.plist"
PROTECTED_MAC = {
    "software_update_policy": MANAGED_UPDATES,
    "gatekeeper": Answer(out=[{"assessments_enabled": "1", "dev_id_enabled": "1"}]),
    "xprotect_bundle": Answer(
        out=[
            {
                "path": XPROTECT_PLIST,
                "bundle_short_version": "5300",
                "mtime": str(int(time.time()) - 86400),
            }
        ]
    ),
}


def endpoint_config(store) -> EndpointConfig:
    return load_connector_config(store, CONFIG_NAME, EndpointConfig)


def evaluate_at(store, moment):
    loaded, _ = load_assets(store)
    evidence = load_host_evidence(store)
    return {
        result.check_id: result
        for result in evaluate_host_checks(
            loaded.assets, evidence.items, endpoint_config(store), moment, evidence.unreadable
        )
        if result.asset_id == "lap-ana"
    }


def collect_and_import(tmp_path, store):
    run = collect(tmp_path / "run", answers=PROTECTED_MAC)
    import_package(store, run.data)
    return run


def test_los_nueve_checks_se_evaluan_sobre_la_evidencia_importada_de_un_portatil(tmp_path, store):
    collect_and_import(tmp_path, store)

    results = evaluate_at(store, utc_now())

    assert list(results) == list(HOST_CHECKS)
    assert {check_id: r.status for check_id, r in results.items()} == {
        "HOST-001": CheckStatus.PASS,
        "HOST-002": CheckStatus.UNKNOWN,
        "HOST-003": CheckStatus.PASS,
        "HOST-004": CheckStatus.PASS,
        "HOST-005": CheckStatus.PASS,
        "HOST-006": CheckStatus.PASS,
        "HOST-007": CheckStatus.PASS,
        "HOST-008": CheckStatus.PASS,
        "HOST-009": CheckStatus.PASS,
    }
    evidence_id = load_host_evidence(store).items[0].evidence_id
    assert all(
        r.evidence_ids == [evidence_id] for r in results.values() if r.status is CheckStatus.PASS
    )
    assert all(r.subject == "lap-ana" for r in results.values())


def test_el_servidor_linux_importado_solo_concluye_los_checks_con_consulta_en_v1(
    tmp_path, store, server_package
):
    import_package(store, server_package, asset="srv-app")

    loaded, _ = load_assets(store)
    results = {
        r.check_id: r
        for r in evaluate_host_checks(
            [a for a in loaded.assets if a.id == "srv-app"],
            load_host_evidence(store).items,
            endpoint_config(store),
            utc_now(),
        )
    }

    assert results["HOST-001"].status is CheckStatus.PASS
    assert results["HOST-007"].status is CheckStatus.PASS
    assert results["HOST-005"].status is CheckStatus.UNKNOWN
    assert results["HOST-006"].status is CheckStatus.UNKNOWN


def readiness_at(store, moment):
    evidence = {
        r.id: r
        for refs in evidence_refs(
            list_manifests(store).manifests, endpoint_config(store).max_evidence_age_days
        ).values()
        for r in refs
    }
    measure = Measure(
        id="op.exp.3",
        applicability=Applicability.APPLICABLE,
        assessment=MeasureAssessment.COMPLIANT,
        evidence=[evidence[item.evidence_id] for item in load_host_evidence(store).items],
        review=Review(
            reviewer="ana",
            role=Role.SECURITY_OFFICER,
            reviewed_at=moment - timedelta(days=1),
            valid_until=moment + timedelta(days=365),
            conclusion="ok",
        ),
    )
    return evaluate_readiness(
        ReadinessInput(measures=[measure], judgment_executed=True, evaluated_at=moment)
    )


def test_la_evidencia_caducada_deja_todos_los_checks_unknown_y_bloquea_la_preparacion(
    tmp_path, store
):
    collect_and_import(tmp_path, store)
    valid_until = load_host_evidence(store).items[0].valid_until

    results = evaluate_at(store, valid_until)
    readiness = readiness_at(store, valid_until)

    assert {r.status for r in results.values()} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in results.values()} == {"evidence_expired"}
    assert readiness.readiness is Readiness.NOT_READY
    assert [b.kind for b in readiness.blockers] == [BlockerKind.EXPIRED_EVIDENCE]


def test_un_instante_antes_de_caducar_la_evidencia_vale_y_la_preparacion_no_se_bloquea(
    tmp_path, store
):
    collect_and_import(tmp_path, store)
    just_before = load_host_evidence(store).items[0].valid_until - timedelta(microseconds=1)

    results = evaluate_at(store, just_before)
    readiness = readiness_at(store, just_before)

    assert results["HOST-001"].status is CheckStatus.PASS
    assert readiness.readiness is Readiness.READY


def test_tras_recolectar_de_nuevo_la_ejecucion_vieja_caducada_no_bloquea_la_preparacion(
    tmp_path, store
):
    collect_and_import(tmp_path, store)
    current = next(m for m in list_manifests(store).manifests if m.source == "endpoint-script")
    max_age = endpoint_config(store).max_evidence_age_days
    old = current.model_copy(
        update={
            "id": "EV-19990101-0ld00000",
            "collected_at": current.collected_at - timedelta(days=90),
            "valid_until": current.valid_until - timedelta(days=90),
        }
    )

    refs = evidence_refs([old, current], max_age)["lap-ana"]

    assert [r.id for r in refs] == [current.id]
    assert evidence_refs([old], max_age)["lap-ana"][0].valid_until < current.collected_at


def test_los_ids_de_evidencia_de_los_checks_y_de_las_referencias_coinciden(tmp_path, store):
    collect_and_import(tmp_path, store)

    refs = evidence_refs(
        list_manifests(store).manifests, endpoint_config(store).max_evidence_age_days
    )["lap-ana"]

    assert [r.id for r in refs] == [item.evidence_id for item in load_host_evidence(store).items]


def test_la_preparacion_caduca_con_el_umbral_vigente_igual_que_los_checks(tmp_path, store):
    collect_and_import(tmp_path, store)
    evidence = load_host_evidence(store).items[0]
    strict = 7
    refs = evidence_refs(list_manifests(store).manifests, strict)["lap-ana"]

    assert [r.valid_until for r in refs] == [evidence.expires_at(strict)]
    assert refs[0].valid_until < evidence.valid_until


def test_un_manifiesto_ilegible_no_hace_volver_a_la_ejecucion_anterior_que_pasaba(tmp_path, store):
    collect_and_import(tmp_path, store)
    assert evaluate_at(store, utc_now())["HOST-005"].status is CheckStatus.PASS
    store.write_text("evidence/manifests/EV-29991231-deadbeef.yaml", "{ no es: [yaml")

    results = evaluate_at(store, utc_now())

    assert {r.status for r in results.values()} == {CheckStatus.UNKNOWN}
    assert {r.reason_code for r in results.values()} == {"unreadable_evidence"}
    assert all("evidencia ilegible" in r.reason and not r.evidence_ids for r in results.values())


def test_un_artefacto_alterado_tras_importar_no_se_evalua_y_deja_todo_unknown(tmp_path, store):
    collect_and_import(tmp_path, store)
    manifest = next(m for m in list_manifests(store).manifests if m.source == "endpoint-script")
    store.write_bytes(manifest.artifact.key, b"{}")

    loaded = load_host_evidence(store)
    results = evaluate_at(store, utc_now())

    assert loaded.items == [] and len(loaded.unreadable) == 1
    assert {r.reason_code for r in results.values()} == {"unreadable_evidence"}
