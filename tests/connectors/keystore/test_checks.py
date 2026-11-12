"""KEY-001..006: PASS solo con evidencia que lo demuestra."""

from datetime import timedelta

import httpx
import pytest

from ens_evidence.connectors.keystore import checks
from ens_evidence.models.enums import CheckStatus

from .fake_openbao import APP_LOOKUP, NOW, POLICY, SEAL_OPEN, days_ago, epoch_ago, kv_entry

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN, CheckStatus.ERROR
)  # fmt: skip
EVIDENCE = "ev-1"


@pytest.fixture
def run(api, collect):
    def evaluate(check, at=NOW, **collect_changes):
        result = collect(**collect_changes)
        (outcome,) = check(result, EVIDENCE, at)
        assert outcome.evidence_ids == [EVIDENCE] and outcome.asset_id == "openbao"
        return outcome

    return evaluate


def test_todo_en_orden_da_pass_en_los_seis(run):
    for check in (
        checks.unsealed, checks.audit_enabled, checks.keys_rotated,
        checks.no_root_token, checks.app_token_scoped, checks.custodians_registered,
    ):  # fmt: skip
        assert run(check).status is PASS


def test_los_ids_son_los_del_catalogo():
    from ens_evidence.checks.catalog import CHECKS

    ids = {c for c, s in CHECKS.items() if s.connector == "keystore"}
    assert ids == {
        checks.UNSEALED, checks.AUDIT_ENABLED, checks.KEYS_ROTATED,
        checks.NO_ROOT_TOKEN, checks.APP_TOKEN_SCOPED, checks.CUSTODIANS_REGISTERED,
    }  # fmt: skip


def seal(api, **changes):
    api.set("GET", "sys/seal-status", (200, {**SEAL_OPEN, **changes}))


def test_sellado_es_fail_en_key_001_con_m_de_n_y_error_en_el_resto(api, run):
    seal(api, sealed=True)

    unsealed = run(checks.unsealed)
    assert unsealed.status is FAIL and "hacen falta 2 de 3 custodios" in unsealed.reason
    assert unsealed.reason_code == checks.SEALED_CODE
    for check in (
        checks.audit_enabled, checks.keys_rotated, checks.no_root_token, checks.app_token_scoped,
    ):  # fmt: skip
        assert run(check).status is ERROR
    assert run(checks.custodians_registered).status is PASS


def test_sellado_sin_n_ni_m_dice_m_de_n(api, run):
    seal(api, sealed=True, t=None, n=None)

    assert "hacen falta M de N custodios" in run(checks.unsealed).reason


def test_sin_inicializar_es_fail_y_sin_respuesta_es_error(api, run):
    seal(api, initialized=False)
    assert run(checks.unsealed).status is FAIL

    api.set("GET", "sys/seal-status", (503, {"errors": ["x"]}))
    assert run(checks.unsealed).status is ERROR
    assert run(checks.custodians_registered).status is ERROR


def test_audit_sin_dispositivos_file_es_fail(api, run):
    api.set("GET", "sys/audit", (200, {"data": {}}))
    assert run(checks.audit_enabled).status is FAIL

    api.set("GET", "sys/audit", (200, {"data": {"syslog/": {"type": "syslog"}}}))
    assert run(checks.audit_enabled).status is FAIL


def test_audit_403_es_unknown_con_motivo(api, run):
    api.set("GET", "sys/audit", (403, {"errors": ["permission denied"]}))

    outcome = run(checks.audit_enabled)

    assert outcome.status is UNKNOWN and "no tiene permiso" in outcome.reason
    assert outcome.reason_code == "permission_denied"


def empty_keys(api):
    api.drop("LIST", "kv/metadata/")
    api.drop("LIST", "transit/keys/")


def test_cero_claves_es_fail_no_hay_claves_gestionadas(api, run):
    empty_keys(api)

    outcome = run(checks.keys_rotated)

    assert outcome.status is FAIL and "no hay claves gestionadas" in outcome.reason


def only_kv(api, **entries):
    api.drop("LIST", "transit/keys/")
    api.set("LIST", "kv/metadata/", (200, {"data": {"keys": list(entries)}}))
    for name, entry in entries.items():
        api.set("GET", f"kv/metadata/{name}", (200, entry))


def test_version_destruida_reciente_sin_mas_claves_no_es_pass(api, run):
    only_kv(api, app=kv_entry(days_ago(1), destroyed=True))

    outcome = run(checks.keys_rotated)

    assert outcome.status is FAIL
    assert (
        "no hay claves gestionadas" in outcome.reason
        and "1 con la versión actual" in outcome.reason
    )


def test_version_borrada_no_cuenta_como_clave_ni_como_rotacion(api, run):
    only_kv(api, app=kv_entry(days_ago(1), deletion_time=days_ago(0)))

    assert run(checks.keys_rotated).status is FAIL


def test_borrado_programado_a_futuro_sigue_siendo_clave_viva(api, run):
    only_kv(api, app=kv_entry(days_ago(1), deletion_time=days_ago(-30)))

    assert run(checks.keys_rotated).status is PASS


def test_deletion_time_ilegible_no_cuenta_como_clave(api, run):
    only_kv(api, app=kv_entry(days_ago(1), deletion_time="mañana"))

    assert run(checks.keys_rotated).status is FAIL


def test_mezcla_de_destruida_y_viva_evalua_solo_la_viva(api, run):
    only_kv(
        api,
        muerta=kv_entry(days_ago(1), destroyed=True),
        viva=kv_entry(days_ago(10)),
    )

    outcome = run(checks.keys_rotated)

    assert outcome.status is PASS and outcome.reason.startswith("1 claves")


def test_version_destruida_antigua_no_cuenta_como_excedida(api, run):
    only_kv(api, muerta=kv_entry(days_ago(200), destroyed=True), viva=kv_entry(days_ago(10)))

    assert run(checks.keys_rotated).status is PASS


def test_claves_que_exceden_el_umbral_son_fail_y_se_listan_sin_valores(api, run):
    api.set("GET", "kv/metadata/app", (200, kv_entry(days_ago(91))))

    outcome = run(checks.keys_rotated)

    assert outcome.status is FAIL
    assert "1 de 4" in outcome.reason and "kv:app" in outcome.reason


def test_el_limite_exacto_cuenta_como_dentro(api, run):
    api.set("GET", "kv/metadata/app", (200, kv_entry(days_ago(90))))

    assert run(checks.keys_rotated).status is PASS


def test_la_edad_se_mide_en_la_fecha_de_evaluacion(run):
    assert run(checks.keys_rotated, at=NOW + timedelta(days=200)).status is FAIL


def test_un_montaje_no_cubierto_es_unknown(api, run):
    api.set(
        "GET",
        "sys/mounts",
        (
            200,
            {"data": {"kv/": {"type": "kv", "options": {"version": "2"}}, "pki/": {"type": "pki"}}},
        ),
    )

    outcome = run(checks.keys_rotated)

    assert outcome.status is UNKNOWN and "pki/ sin cobertura; revísalo a mano" in outcome.reason


def test_un_montaje_no_cubierto_con_cero_claves_sigue_siendo_unknown(api, run):
    empty_keys(api)
    api.set("GET", "sys/mounts", (200, {"data": {"pki/": {"type": "pki"}}}))

    assert run(checks.keys_rotated).status is UNKNOWN


def test_fecha_ilegible_o_futura_es_unknown(api, run):
    api.set("GET", "kv/metadata/app", (200, kv_entry("ayer")))
    assert run(checks.keys_rotated).status is UNKNOWN

    api.set("GET", "kv/metadata/app", (200, kv_entry(days_ago(-3))))
    assert run(checks.keys_rotated).status is UNKNOWN


def test_auto_rotate_period_no_sustituye_a_la_fecha_real(api, run):
    api.set(
        "GET",
        "transit/keys/sim",
        (
            200,
            {
                "data": {
                    "latest_version": 1,
                    "keys": {"1": epoch_ago(400)},
                    "auto_rotate_period": 86400,
                }
            },
        ),
    )

    assert run(checks.keys_rotated).status is FAIL


def test_sin_umbral_en_la_evidencia_es_unknown(api, collect):
    result = collect()
    result.data["thresholds"] = {}

    (outcome,) = checks.keys_rotated(result, EVIDENCE, NOW)

    assert outcome.status is UNKNOWN


def test_un_token_root_vivo_es_fail(api, run):
    from .fake_openbao import ACCESSOR_ROOT

    def lookup(request):
        accessor = __import__("json").loads(request.content)["accessor"]
        policies = ["root"] if accessor == "acc-otro" else [POLICY]
        return httpx.Response(200, json={"data": {"policies": policies}})

    api.set("POST", "auth/token/lookup-accessor", lookup)

    outcome = run(checks.no_root_token)

    assert outcome.status is FAIL and "1 token" in outcome.reason
    assert ACCESSOR_ROOT not in outcome.reason


def test_accessors_403_listado_truncado_o_ilegible_o_vacio_son_unknown(api, run, monkeypatch):
    api.set("LIST", "auth/token/accessors", (403, {"errors": ["permission denied"]}))
    assert run(checks.no_root_token).status is UNKNOWN

    api.set("LIST", "auth/token/accessors", (200, {"data": {"keys": ["a", "b", "c"]}}))
    monkeypatch.setattr("ens_evidence.connectors.keystore.sections.MAX_ACCESSORS", 2)
    assert run(checks.no_root_token).status is UNKNOWN

    monkeypatch.undo()
    api.set("POST", "auth/token/lookup-accessor", (400, {"errors": ["invalid"]}))
    assert run(checks.no_root_token).status is UNKNOWN

    api.set("LIST", "auth/token/accessors", (200, {"data": {"keys": []}}))
    assert run(checks.no_root_token).status is UNKNOWN


def lookup(**changes):
    return (200, {"data": {**APP_LOOKUP, **changes}})


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"policies": [POLICY, "default"]}, "políticas del token"),
        ({"policies": ["root"]}, "políticas del token"),
        ({"policies": []}, "políticas del token"),
        ({"period": 73 * 3600, "ttl": 72 * 3600}, "supera 72 h"),
        ({"period": 0, "ttl": 5}, "no tiene periodo"),
        ({"ttl": 0}, "TTL"),
    ],
)
def test_token_de_la_app_mal_acotado_es_fail(api, run, changes, fragment):
    api.set("GET", "auth/token/lookup-self", lookup(**changes))
    api.set("POST", "auth/token/renew-self", (200, {}))

    outcome = run(checks.app_token_scoped)

    assert outcome.status is FAIL and fragment in outcome.reason


def test_token_sin_deny_en_las_rutas_de_valores_es_fail(api, run):
    api.set("POST", "sys/capabilities-self", (200, {"data": {"capabilities": ["read", "list"]}}))

    outcome = run(checks.app_token_scoped)

    assert outcome.status is FAIL and "no tiene deny" in outcome.reason


def test_politica_distinta_a_la_de_la_release_o_inexistente_es_fail(api, run):
    api.set("GET", f"sys/policies/acl/{POLICY}", (200, {"data": {"policy": 'path "*" {}'}}))
    assert "no coincide con la de la release" in run(checks.app_token_scoped).reason

    api.drop("GET", f"sys/policies/acl/{POLICY}")
    outcome = run(checks.app_token_scoped)
    assert outcome.status is FAIL and "no existe" in outcome.reason


def test_token_403_en_lookup_es_unknown(api, run):
    api.set("GET", "auth/token/lookup-self", (403, {"errors": ["permission denied"]}))

    assert run(checks.app_token_scoped).status is UNKNOWN


def test_token_con_deny_en_capabilities_y_politica_de_release_es_pass(run):
    assert "release" in run(checks.app_token_scoped).reason


def test_custodios_sin_registro_es_fail_no_consta(run):
    outcome = run(checks.custodians_registered, custody=None)

    assert outcome.status is FAIL and "no consta quién custodia" in outcome.reason


def test_custodios_con_registros_ilegibles_y_ninguno_valido_es_unknown(run):
    outcome = run(checks.custodians_registered, custody=None, unreadable_registers=["x"])

    assert outcome.status is UNKNOWN


def test_custodios_caducados_es_fail(run):
    outcome = run(checks.custodians_registered, at=NOW.replace(year=2028))

    assert outcome.status is FAIL and "caducado" in outcome.reason


@pytest.mark.parametrize("changes", [{"n": 5}, {"t": 3}, {"n": 2, "t": 2}])
def test_custodios_que_no_casan_con_seal_status_es_fail(api, run, changes):
    seal(api, **changes)

    outcome = run(checks.custodians_registered)

    assert outcome.status is FAIL and "no casa con OpenBao" in outcome.reason


def test_custodios_sin_n_y_m_en_seal_status_es_unknown(api, run):
    seal(api, n=None, t=None)

    assert run(checks.custodians_registered).status is UNKNOWN
