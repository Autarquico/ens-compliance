"""Entradas hostiles del kit de claves: configuración, paquete, custodios y OpenBao."""

import io
import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml
from box.test_gate import shell_function
from pydantic import ValidationError

from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.connectors.keystore import KeystoreConfig, KeystoreConnector, KeystoreRun, checks
from ens_evidence.connectors.keystore.connector import TOKEN_ENV
from ens_evidence.kit.custody import CustodianRegister, record_custodians
from ens_evidence.kit.keystore import KeystoreInputs, render_bundle
from ens_evidence.models.enums import CheckStatus
from ens_evidence.workspace import LocalStore

from .fake_openbao import (
    ACCESSOR_APP,
    APP_LOOKUP,
    NOW,
    POLICY,
    TOKEN,
    FakeOpenBao,
    days_ago,
    epoch_ago,
)

EXAMPLE_SCOPE = Path(__file__).resolve().parents[3] / "examples" / "workspace" / "scope"
CANARY = "CANARIO-DE-VALOR-HOSTIL"
GOOD_REGISTER = {
    "custodians": [{"name": "Ana Pérez", "since": "2026-11-01T00:00:00+00:00"}],
    "key_shares": 3,
    "key_threshold": 2,
    "review": {
        "reviewer": "Marta Ruiz",
        "role": "security_officer",
        "reviewed_at": "2026-11-09T10:00:00+00:00",
        "valid_until": "2027-11-09T10:00:00+00:00",
    },
}


def check_of(api, check, **changes):
    run = KeystoreRun(asset_id="openbao", max_key_age_days=90, **changes)
    connector = KeystoreConnector(
        transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN}
    )
    (outcome,) = check(connector.collect(run), "ev-1", NOW)
    return outcome


# --- configuración del conector: sin credenciales ---------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        {"token": "hvs.CAESIJ-valor-del-token-de-la-app"},
        {"root_token": "hvs.CAESIJ-valor-del-root"},
        {"vault_token": "hvs.CAESIJ-valor-de-otro-token"},
        {"address": "http://root:hvs.CAESIJ-valor@openbao:8200"},
    ],
)
def test_la_carga_de_keystore_yaml_rechaza_un_token_sin_repetirlo(tmp_path, entry):
    store = LocalStore(tmp_path / "ws")
    base = {"asset_id": "openbao", "max_key_age_days": 90}
    store.write_text("connectors/keystore.yaml", yaml.safe_dump({**base, **entry}))

    with pytest.raises(ConnectorConfigError) as caught:
        load_connector_config(store, "keystore", KeystoreConfig)

    assert not any("CAESIJ" in str(part) for part in (caught.value, *caught.value.args))


# --- paquete del kit: ni ficheros de más ni enlaces -----------------------------------------


def package_members(tmp_path, *, extra=None, replace=None):
    """El paquete real de `render_bundle`, con miembros añadidos o sustituidos."""
    blob = render_bundle(
        KeystoreInputs(asset_id="openbao", key_shares=5, key_threshold=3, max_key_age_days=90),
        datetime(2026, 11, 10, tzinfo=UTC),
    )
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = [(m, tar.extractfile(m).read()) for m in tar.getmembers() if m.isfile()]
    path = tmp_path / "paquete.tar.gz"
    with tarfile.open(path, "w:gz") as out:
        for info, data in members:
            if replace and info.name in replace:
                info = replace[info.name]
                data = b""
            out.addfile(info, io.BytesIO(data) if info.isfile() else None)
        for info, data in extra or ():
            out.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def unpack(tmp_path, package):
    script = "\n".join(
        [
            shell_function("die"),
            shell_function("sha256_of"),
            shell_function("extract_claves_package"),
            shell_function("verify_claves_package"),
            f'extract_claves_package "{package}" "{tmp_path}/out" && '
            f'verify_claves_package "{tmp_path}/out/claves"',
        ]
    )
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True)


def member(name, data=b"x", **fields):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    for key, value in fields.items():
        setattr(info, key, value)
    return info, data


def test_el_paquete_integro_se_acepta(tmp_path):
    result = unpack(tmp_path, package_members(tmp_path))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["claves/extra.sh", "claves/../evil", "/etc/cron.d/x", "otro/x"])
def test_un_paquete_con_ficheros_de_mas_se_rechaza_sin_extraer_nada(tmp_path, name):
    package = package_members(tmp_path, extra=[member(name)])

    result = unpack(tmp_path, package)

    assert result.returncode != 0 and "no son del contrato" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_un_paquete_con_enlaces_se_rechaza_sin_extraer_nada(tmp_path, kind):
    link, _ = member("claves/openbao.hcl", b"", type=kind, linkname="/etc/passwd", size=0)
    package = package_members(tmp_path, replace={"claves/openbao.hcl": link})

    result = unpack(tmp_path, package)

    assert result.returncode != 0 and "enlaces" in result.stderr
    assert not (tmp_path / "out").exists()


def test_un_paquete_con_un_fichero_repetido_se_rechaza(tmp_path):
    package = package_members(tmp_path, extra=[member("claves/init.json", b"{}")])

    result = unpack(tmp_path, package)

    assert result.returncode != 0 and "repite" in result.stderr
    assert not (tmp_path / "out").exists()


# --- M > N y nombres de custodio ---------------------------------------------------------------


@pytest.mark.parametrize("shares, threshold", [(3, 4), (5, 6), (10, 11)])
def test_m_mayor_que_n_no_genera_paquete(shares, threshold):
    with pytest.raises(ValidationError, match="no puede ser mayor"):
        KeystoreInputs(
            asset_id="openbao", key_shares=shares, key_threshold=threshold, max_key_age_days=90
        )


def test_m_mayor_que_n_no_vale_como_registro_de_custodios():
    with pytest.raises(ValidationError, match="key_threshold no puede superar"):
        CustodianRegister.model_validate({**GOOD_REGISTER, "key_shares": 2, "key_threshold": 3})


@pytest.mark.parametrize("name", ["ana@example.com", "12345678Z", "+34 600 123 456"])
def test_un_custodio_con_email_dni_o_telefono_no_se_registra_y_el_error_no_lo_repite(
    tmp_path, name
):
    store = LocalStore(tmp_path / "ws")
    data = yaml.safe_dump(
        {**GOOD_REGISTER, "custodians": [{"name": name, "since": "2026-11-01T00:00:00+00:00"}]}
    )

    with pytest.raises(ValidationError) as caught:
        record_custodians(store, data.encode(), NOW, asset_id="openbao")

    assert name not in str(caught.value.errors(include_input=False, include_url=False))
    assert store.list("evidence") == []


# --- respuestas de OpenBao con valores inyectados -------------------------------------------------


def poisoned_api() -> FakeOpenBao:
    """Cada campo que el conector no necesita lleva un valor marcado."""
    api = FakeOpenBao()
    api.set(
        "GET",
        "sys/seal-status",
        (
            200,
            {
                "initialized": True,
                "sealed": False,
                "t": 2,
                "n": 3,
                "progress": 0,
                "version": "2.7.2",
                "cluster_name": CANARY,
                "recovery_seal": CANARY,
                "nonce": CANARY,
            },
        ),
    )
    api.set(
        "GET",
        "sys/audit",
        (200, {"data": {"archivo/": {"type": "file", "options": {"file_path": CANARY}}}}),
    )
    api.set(
        "GET",
        "sys/mounts",
        (
            200,
            {
                "data": {
                    "kv/": {"type": "kv", "options": {"version": "2"}, "description": CANARY},
                    "transit/": {"type": "transit", "options": {}, "accessor": CANARY},
                    "sys/": {"type": "system", "options": None},
                    "cubbyhole/": {"type": "cubbyhole", "options": None},
                    "identity/": {"type": "identity", "options": None},
                }
            },
        ),
    )
    entry = {
        "data": {
            "current_version": 1,
            "custom_metadata": {"nota": CANARY, "password": CANARY},
            "versions": {
                "1": {"created_time": days_ago(5), "destroyed": False, "deletion_time": CANARY}
            },
        }
    }
    api.set("GET", "kv/metadata/app", (200, entry))
    api.set(
        "GET",
        "transit/keys/firma",
        (
            200,
            {
                "data": {
                    "type": "ed25519",
                    "latest_version": 1,
                    "keys": {
                        "1": {
                            "creation_time": days_ago(3),
                            "public_key": CANARY,
                            "name": CANARY,
                        }
                    },
                    "auto_rotate_period": 0,
                    "derived": CANARY,
                }
            },
        ),
    )
    api.set(
        "GET",
        "transit/keys/sim",
        (
            200,
            {
                "data": {
                    "type": "aes256-gcm96",
                    "latest_version": 1,
                    "keys": {"1": epoch_ago(5)},
                    "backup": CANARY,
                    "plaintext": CANARY,
                }
            },
        ),
    )
    api.set(
        "GET",
        "auth/token/lookup-self",
        (
            200,
            {"data": {**APP_LOOKUP, "id": CANARY, "display_name": CANARY, "meta": {"x": CANARY}}},
        ),
    )
    api.set(
        "POST",
        "auth/token/lookup-accessor",
        (
            200,
            {
                "data": {
                    "policies": [POLICY],
                    "orphan": True,
                    "ttl": 100,
                    "id": CANARY,
                    "accessor": ACCESSOR_APP,
                    "display_name": CANARY,
                    "meta": {"k": CANARY},
                }
            },
        ),
    )
    return api


def test_los_valores_inyectados_en_respuestas_de_openbao_no_llegan_a_la_evidencia():
    api = poisoned_api()
    connector = KeystoreConnector(
        transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN}
    )
    run = KeystoreRun(asset_id="openbao", max_key_age_days=90)

    result = connector.collect(run)

    assert CANARY not in json.dumps(result.model_dump(mode="json"))
    assert TOKEN not in json.dumps(result.model_dump(mode="json"))


def test_con_valores_inyectados_los_checks_siguen_diciendo_lo_mismo_y_sus_motivos_no_los_repiten():
    api = poisoned_api()
    connector = KeystoreConnector(
        transport=api.transport, clock=lambda: NOW, env={TOKEN_ENV: TOKEN}
    )
    result = connector.collect(KeystoreRun(asset_id="openbao", max_key_age_days=90))

    verdicts = (
        checks.unsealed, checks.audit_enabled, checks.keys_rotated,
        checks.no_root_token, checks.app_token_scoped,
    )  # fmt: skip
    outcomes = [o for check in verdicts for o in check(result, "ev", NOW)]

    assert all(o.status is CheckStatus.PASS for o in outcomes), [o.reason for o in outcomes]
    assert not any(CANARY in o.reason for o in outcomes)


def test_un_nombre_de_clave_hostil_se_nombra_en_el_motivo_sin_romper_ni_inyectar_rutas():
    api = FakeOpenBao()
    hostile = "../../sys/raw/x"
    api.set("LIST", "transit/keys/", (200, {"data": {"keys": [hostile, "firma"]}}))

    outcome = check_of(api, checks.keys_rotated)

    assert outcome.status in {CheckStatus.UNKNOWN, CheckStatus.ERROR, CheckStatus.FAIL}
    assert not any(path.startswith("sys/raw") for _, path in api.requests)


# --- política del token de la app ---------------------------------------------------------------


@pytest.mark.parametrize("policies", [["root"], ["root", POLICY], [POLICY, "default"], []])
def test_un_token_de_la_app_con_root_u_otra_politica_da_fail_en_key_005(policies):
    api = FakeOpenBao()
    api.set("GET", "auth/token/lookup-self", (200, {"data": {**APP_LOOKUP, "policies": policies}}))

    outcome = check_of(api, checks.app_token_scoped)

    assert outcome.status is CheckStatus.FAIL
    assert "las políticas del token no son exactamente" in outcome.reason


def test_un_token_root_vivo_entre_los_accessors_da_fail_en_key_004():
    api = FakeOpenBao()
    api.set("LIST", "auth/token/accessors", (200, {"data": {"keys": [ACCESSOR_APP, "acc-root"]}}))
    api.set(
        "POST",
        "auth/token/lookup-accessor",
        lambda request: httpx.Response(
            200,
            json={
                "data": {
                    "policies": ["root"]
                    if json.loads(request.content)["accessor"] == "acc-root"
                    else [POLICY],
                    "orphan": True,
                    "ttl": 0,
                }
            },
        ),
    )

    assert check_of(api, checks.no_root_token).status is CheckStatus.FAIL


# --- token sin periodo en lookup-self ---------------------------------------------------------


def lookup_without_period(**fields):
    api = FakeOpenBao()
    data = {k: v for k, v in APP_LOOKUP.items() if k != "period"} | fields
    api.set("GET", "auth/token/lookup-self", (200, {"data": data}))
    return api


def test_un_token_sin_periodo_ni_creation_ttl_da_fail():
    outcome = check_of(lookup_without_period(), checks.app_token_scoped)

    assert outcome.status is CheckStatus.FAIL
    assert "no tiene periodo ni TTL de creación" in outcome.reason


def test_un_token_sin_periodo_con_creation_ttl_mayor_que_el_limite_da_fail():
    api = lookup_without_period(creation_ttl=73 * 3600)

    outcome = check_of(api, checks.app_token_scoped)

    assert outcome.status is CheckStatus.FAIL
    assert "TTL de creación del token supera 72 h" in outcome.reason


def test_un_token_no_periodico_con_creation_ttl_dentro_del_limite_da_pass_hoy():
    """Documenta el comportamiento: `lookup-self` de OpenBao 2.7.2 no distingue un token
    periódico (el del rol `ens-app`) de uno renovable con `explicit_max_ttl`: ambos traen
    solo `creation_ttl`. KEY-005 comprueba política, TTL acotado y deny en valores; que el
    token sea periódico no se puede probar con esa respuesta."""
    api = lookup_without_period(creation_ttl=72 * 3600, explicit_max_ttl=72 * 3600, renewable=True)

    outcome = check_of(api, checks.app_token_scoped)

    assert outcome.status is CheckStatus.PASS
    assert "TTL de creación 72 h; OpenBao no expone el periodo" in outcome.reason
    assert "periodo válido" not in outcome.reason and "periodo 72" not in outcome.reason


def test_con_period_el_motivo_nombra_el_periodo():
    outcome = check_of(FakeOpenBao(), checks.app_token_scoped)

    assert outcome.status is CheckStatus.PASS
    assert "periodo 72 h" in outcome.reason and "TTL de creación" not in outcome.reason


# --- el registro de custodios es de un activo ------------------------------------------------


OTHER_APP = """
  - id: app-otra
    type: application
    deployment: deployed
    service: Facturación
    domain: otra.taller-norte.example
    repo: taller-norte/otra
    database: db-facturas
"""


@pytest.mark.xfail(
    reason="`latest_register` no filtra por activo: el registro de custodios de un almacén "
    "se lee también para otro activo application (el registro no guarda a qué activo "
    "pertenece; solo su evidencia manual lo sabe)",
    strict=True,
)
def test_el_registro_de_custodios_de_un_activo_no_se_usa_para_otro(tmp_path):
    store = LocalStore(tmp_path / "ws")
    for name in ("assets", "services", "dependencies", "system"):
        text = (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        store.write_text(f"scope/{name}.yaml", text + (OTHER_APP if name == "assets" else ""))
    record_custodians(store, yaml.safe_dump(GOOD_REGISTER).encode(), NOW, asset_id="app-facturas")
    connector = KeystoreConnector(clock=lambda: NOW, env={})

    run = connector.prepare(KeystoreConfig(asset_id="app-otra", max_key_age_days=90), store)

    assert run.custody is None
