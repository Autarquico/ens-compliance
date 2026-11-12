"""Escenarios de docs/specs/kit-secrets.md: de lo que responde OpenBao a lo que dice la matriz.

Solo se simula OpenBao (`FakeOpenBao`, frontera de red); conector, checks, evidencia y matriz
son los reales. La tarjeta de Claves en rojo y su enlace al runbook están en tests/ui.
"""

import fnmatch
import json
from datetime import timedelta

import pytest
from connectors.keystore.fake_openbao import NOW, TOKEN, FakeOpenBao
from matrix.source_helpers import SHA, check_link, run

from ens_evidence.connectors.keystore import KeystoreConnector, KeystoreRun
from ens_evidence.connectors.keystore import checks as keystore_checks
from ens_evidence.connectors.keystore.client import KeystoreClient, KeystoreError
from ens_evidence.connectors.keystore.connector import TOKEN_ENV
from ens_evidence.connectors.keystore.sections import VALUE_PATHS
from ens_evidence.evidence.importer import import_collected
from ens_evidence.evidence.manifests import load_manifest_by_id
from ens_evidence.kit.custody import CustodianRegister
from ens_evidence.matrix import ClauseRef, MatrixRowSpec, Obligation, RowKind
from ens_evidence.matrix.evidence import manifest_sha256
from ens_evidence.matrix.technical import technical_support
from ens_evidence.models.enums import CheckStatus
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.workspace import LocalStore

from .test_openbao_policies import rules

AT = NOW + timedelta(hours=1)
ASSET = "openbao"
CHECK_IDS = (
    keystore_checks.UNSEALED,
    keystore_checks.AUDIT_ENABLED,
    keystore_checks.KEYS_ROTATED,
    keystore_checks.NO_ROOT_TOKEN,
    keystore_checks.APP_TOKEN_SCOPED,
    keystore_checks.CUSTODIANS_REGISTERED,
)
ALL_CHECKS = (
    keystore_checks.unsealed,
    keystore_checks.audit_enabled,
    keystore_checks.keys_rotated,
    keystore_checks.no_root_token,
    keystore_checks.app_token_scoped,
    keystore_checks.custodians_registered,
)
# op.exp.10 tal como lo da el anexo II (op.yaml); sin texto, solo los ids de cláusula
OP_EXP_10 = MatrixRowSpec(
    "op.exp.10",
    RowKind.SELECTED,
    (
        Obligation(
            "base",
            tuple(ClauseRef(f"op.exp.10.{n}", None, "texto") for n in (1, 2, 3)),
        ),
    ),
    SHA,
)
REGISTER = CustodianRegister.model_validate(
    {
        "asset_id": ASSET,
        "custodians": [
            {"name": name, "since": "2026-11-01T00:00:00+00:00"}
            for name in ("Ana Pérez", "Luis Gómez", "Marta Ruiz")
        ],
        "key_shares": 3,
        "key_threshold": 2,
        "review": {
            "reviewer": "Marta Ruiz",
            "role": "security_officer",
            "reviewed_at": "2026-11-09T10:00:00+00:00",
            "valid_until": "2027-11-09T10:00:00+00:00",
        },
    }
)
SECRET_VALUE = "VALOR-DEL-SECRETO-CANARIO"  # noqa: S105 - valor de prueba


def collect(api: FakeOpenBao, **changes):
    connector = KeystoreConnector(
        transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN}
    )
    config = KeystoreRun(asset_id=ASSET, max_key_age_days=90, custody=REGISTER)
    return connector.collect(config.model_copy(update=changes))


def evaluated(store, result) -> dict:
    """La evidencia importada de verdad y los seis checks sobre ella, por id."""
    evidence = import_collected(store, result, collector_version="1", now=NOW)
    found = {}
    for check in ALL_CHECKS:
        (outcome,) = check(result, evidence.id, AT - timedelta(minutes=30))
        found[outcome.check_id] = outcome
    return found


def support_of(store, outcomes: dict, check_id: str):
    """Lo que la matriz saca de esos resultados al evaluar op.exp.10 con un CheckLink a la 10.1."""
    results = list(outcomes.values())
    fingerprints = {  # la huella que registra el motor al construir la ejecución
        i: manifest_sha256(load_manifest_by_id(store, i)) for r in results for i in r.evidence_ids
    }
    runs = [
        run(
            results,
            connector="keystore",
            expected=(ASSET,),
            finished_at=AT,
            evidence_sha256=fingerprints,
        )
    ]
    return technical_support(
        store, OP_EXP_10, check_link(check_id, covers=("op.exp.10.1",)), runs, AT
    )


def seal(api: FakeOpenBao, **changes) -> None:
    from connectors.keystore.fake_openbao import SEAL_OPEN

    api.set("GET", "sys/seal-status", (200, {**SEAL_OPEN, **changes}))


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


# --- la app nunca lee un secreto ---------------------------------------------------------------


def policy_for(path: str) -> set[str]:
    """Capacidades de la política de la app sobre `path`: manda la regla más específica."""
    found = [
        (len(pattern), caps)
        for pattern, caps in rules("ens-app-metadata.hcl").items()
        if fnmatch.fnmatchcase(path, pattern)
    ]
    return max(found, key=lambda f: f[0])[1] if found else set()


@pytest.mark.parametrize(
    "path", [*VALUE_PATHS, "kv/data/otro/anidado", "transit/export/signing-key/x/1"]
)
def test_la_politica_de_la_app_deniega_cada_ruta_de_valores(path):
    assert policy_for(path) == {"deny"}


@pytest.mark.parametrize(
    "path",
    ["kv/data/app", "kv/data/dir/db", "transit/export/encryption-key/firma", "transit/decrypt/sim"],
)
def test_el_cliente_rechaza_leer_un_valor_sin_enviar_nada(path):
    api = FakeOpenBao()
    api.set("GET", path, (200, {"data": {"data": {"password": SECRET_VALUE}}}))
    with KeystoreClient("http://openbao:8200", TOKEN, transport=api.transport) as client:
        with pytest.raises(KeystoreError, match="ruta no permitida"):
            client.get(path)

    assert api.requests == []


def test_la_recogida_completa_solo_pide_metadatos_y_el_valor_no_llega_a_la_evidencia():
    api = FakeOpenBao()
    for path in ("kv/data/app", "kv/data/dir/db", "transit/export/encryption-key/sim"):
        api.set("GET", path, (200, {"data": {"data": {"password": SECRET_VALUE}}}))

    result = collect(api)

    paths = [path for _, path in api.requests]
    assert paths and not [p for p in paths if p.startswith(("kv/data", "transit/export"))]
    assert all(
        p.startswith(("kv/metadata", "transit/keys", "sys/", "auth/token/")) for p in paths
    ), paths
    assert SECRET_VALUE not in json.dumps(result.model_dump(mode="json"))


def test_si_el_token_pudiera_leer_valores_el_check_de_la_app_da_fail(store):
    api = FakeOpenBao()
    api.set("POST", "sys/capabilities-self", (200, {"data": {"capabilities": ["read"]}}))

    outcomes = evaluated(store, collect(api))

    scoped = outcomes[keystore_checks.APP_TOKEN_SCOPED]
    assert scoped.status is CheckStatus.FAIL and "no tiene deny" in scoped.reason
    assert not support_of(store, outcomes, keystore_checks.APP_TOKEN_SCOPED).counts


# --- OpenBao sellado se ve ---------------------------------------------------------------------


def test_sano_op_exp_10_1_tiene_evidencia_vigente_con_el_check_de_desbloqueo(store):
    outcomes = evaluated(store, collect(FakeOpenBao()))

    support = support_of(store, outcomes, keystore_checks.UNSEALED)

    assert outcomes[keystore_checks.UNSEALED].status is CheckStatus.PASS
    assert support.clauses == frozenset({"op.exp.10.1"}) and support.evidence


def test_sellado_key_001_da_fail_y_dice_cuantos_custodios_hacen_falta(store):
    api = FakeOpenBao()
    seal(api, sealed=True)

    outcomes = evaluated(store, collect(api))

    unsealed = outcomes[keystore_checks.UNSEALED]
    assert unsealed.status is CheckStatus.FAIL
    assert "hacen falta 2 de 3 custodios" in unsealed.reason


def test_sellado_op_exp_10_pierde_la_evidencia_vigente_y_queda_como_hallazgo(store):
    api = FakeOpenBao()
    seal(api, sealed=True)
    outcomes = evaluated(store, collect(api))

    support = support_of(store, outcomes, keystore_checks.UNSEALED)

    assert not support.counts and not support.evidence
    assert [(b.kind, b.detail) for b in support.blockers] == [
        (BlockerKind.NOT_COMPLIANT, f"check_failed:{keystore_checks.UNSEALED}:{ASSET}")
    ]


@pytest.mark.parametrize("check_id", CHECK_IDS[1:5])
def test_sellado_los_demas_checks_no_dan_pass_porque_no_pudieron_leer(store, check_id):
    api = FakeOpenBao()
    seal(api, sealed=True)
    outcomes = evaluated(store, collect(api))

    assert outcomes[check_id].status is CheckStatus.ERROR
    assert not support_of(store, outcomes, check_id).counts


def test_desbloquear_devuelve_la_evidencia_vigente(store):
    api = FakeOpenBao()
    seal(api, sealed=True)
    assert not support_of(store, evaluated(store, collect(api)), keystore_checks.UNSEALED).counts

    seal(api, sealed=False)

    support = support_of(store, evaluated(store, collect(api)), keystore_checks.UNSEALED)
    assert support.clauses == frozenset({"op.exp.10.1"})


# --- instalar no es cumplir --------------------------------------------------------------------


def installed_only() -> FakeOpenBao:
    """OpenBao desplegado y desbloqueado, sin dispositivo de auditoría ni claves."""
    api = FakeOpenBao()
    api.set("GET", "sys/audit", (200, {"data": {}}))
    api.drop("LIST", "kv/metadata/")
    api.drop("LIST", "transit/keys/")
    return api


@pytest.mark.parametrize(
    ("check_id", "missing"),
    [
        (keystore_checks.AUDIT_ENABLED, "no hay ningún dispositivo de auditoría"),
        (keystore_checks.KEYS_ROTATED, "no hay claves gestionadas"),
        (keystore_checks.CUSTODIANS_REGISTERED, "no consta quién custodia"),
    ],
)
def test_sin_claves_ni_auditoria_ni_custodios_no_hay_evidencia_vigente_y_el_check_dice_que_falta(
    store, check_id, missing
):
    outcomes = evaluated(store, collect(installed_only(), custody=None))

    assert outcomes[check_id].status is CheckStatus.FAIL
    assert missing in outcomes[check_id].reason
    support = support_of(store, outcomes, check_id)
    assert not support.counts and not support.evidence
    assert [b.kind for b in support.blockers] == [BlockerKind.NOT_COMPLIANT]


def test_ningun_check_de_un_openbao_vacio_da_pass_en_claves_ni_en_auditoria(store):
    outcomes = evaluated(store, collect(installed_only(), custody=None))

    passing = {c for c, o in outcomes.items() if o.status is CheckStatus.PASS}

    assert keystore_checks.AUDIT_ENABLED not in passing
    assert keystore_checks.KEYS_ROTATED not in passing
    assert keystore_checks.CUSTODIANS_REGISTERED not in passing
