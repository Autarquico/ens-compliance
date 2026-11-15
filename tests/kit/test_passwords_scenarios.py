"""Escenarios de docs/specs/kit-passwords.md: de la base de Vaultwarden a lo que dicen los checks.

Solo se simula la frontera (una base SQLite ficticia con el DDL verificado); exportador, conector,
checks y evidencia son los reales. La tarjeta en «Con problemas» está en tests/ui/test_scenarios.py.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.connectors.passwords import PasswordsConfig, PasswordsConnector, reader
from ens_evidence.connectors.passwords import checks as passwords_checks
from ens_evidence.evidence.importer import import_collected
from ens_evidence.kit.passwords_export import PasswordsExport, export
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.workspace import LocalStore

from .test_passwords_export import Db, uid

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
LIMIT_DAYS = 90
SIN_FACTOR, CON_FACTOR = uid(1), uid(2)
EMAIL = "persona01@example.test"
NOMBRE = "Persona 01"
CANARIO = "VALOR-DE-COFRE-CANARIO"
CHECKS = (
    passwords_checks.second_factor,
    passwords_checks.no_inactive_accounts,
    passwords_checks.snapshot_recent,
)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def export_dict(users: list[dict], **over) -> dict:
    base = {
        "format": 1,
        "exported_at": (NOW - timedelta(minutes=5)).isoformat(),
        "vaultwarden_migration": "20261007120000",
        "snapshot_ok": True,
        "last_good_snapshot_at": (NOW - timedelta(minutes=5)).isoformat(),
        "users": users,
    }
    return {**base, **over}


def user_dict(n: int = 3, **over) -> dict:
    base = {
        "id": uid(n),
        "enabled": True,
        "invited": False,
        "created_at": days_ago(200).isoformat(),
        "second_factor": True,
        "last_active": days_ago(2).isoformat(),
    }
    return {**base, **over}


def collect(path: Path, **changes):
    config = PasswordsConfig(
        asset_id="vaultwarden",
        export_path=str(path),
        inactive_days=LIMIT_DAYS,
        max_export_age_minutes=60,
    ).model_copy(update=changes)
    return PasswordsConnector(clock=lambda: NOW).collect(config)


def outcomes(result) -> dict:
    found = {}
    for check in CHECKS:
        (outcome,) = check(result, "ev-1", NOW)
        found[outcome.check_id] = outcome
    return found


def written(tmp_path: Path, doc=None, raw: bytes | None = None) -> Path:
    path = tmp_path / "usuarios.json"
    path.write_bytes(raw if raw is not None else json.dumps(doc).encode())
    return path


def checked(tmp_path: Path, users: list[dict]) -> dict:
    return outcomes(collect(written(tmp_path, export_dict(users))))


def second_factor_of(found):
    return found[passwords_checks.SECOND_FACTOR]


def inactivity_of(found):
    return found[passwords_checks.NO_INACTIVE]


def workspace_bytes(root: Path) -> bytes:
    return b"\n".join((Path(d) / f).read_bytes() for d, _, files in os.walk(root) for f in files)


# --- «doble factor obligatorio» -----------------------------------------------------------------


@pytest.fixture
def vaultwarden_db(tmp_path):
    """Dos cuentas reales con email y nombre: una solo con «recordar dispositivo», otra con TOTP."""
    data = tmp_path / "data"
    data.mkdir()
    db = Db(data / "db.sqlite3")
    db.user(SIN_FACTOR)
    db.factor(SIN_FACTOR, 5)  # recordar dispositivo no es un segundo factor
    db.device(SIN_FACTOR, stamp(days_ago(1)))
    db.user(CON_FACTOR)
    db.factor(CON_FACTOR, 0)
    db.device(CON_FACTOR, stamp(days_ago(1)))
    db.done()
    return data / "db.sqlite3"


def exported_and_collected(tmp_path, vaultwarden_db):
    out = tmp_path / "out"
    export(vaultwarden_db, out, tmp_path / "snap", NOW - timedelta(minutes=1))
    return collect(out / "usuarios.json")


def test_un_usuario_sin_doble_factor_da_fail_con_su_id_interno(tmp_path, vaultwarden_db):
    found = outcomes(exported_and_collected(tmp_path, vaultwarden_db))

    outcome = second_factor_of(found)
    assert outcome.status is CheckStatus.FAIL
    assert SIN_FACTOR in outcome.reason
    assert CON_FACTOR not in outcome.reason


def test_el_texto_del_fallo_no_lleva_email_ni_nombre(tmp_path, vaultwarden_db):
    found = outcomes(exported_and_collected(tmp_path, vaultwarden_db))

    texts = " ".join(o.reason for o in found.values())
    assert EMAIL not in texts and NOMBRE not in texts and "persona" not in texts.lower()


def test_ni_el_export_ni_la_evidencia_guardada_llevan_datos_personales(tmp_path, vaultwarden_db):
    result = exported_and_collected(tmp_path, vaultwarden_db)
    store = LocalStore(tmp_path / "ws")
    import_collected(store, result, collector_version="1", now=NOW)

    kept = workspace_bytes(tmp_path / "ws") + (tmp_path / "out" / "usuarios.json").read_bytes()
    assert SIN_FACTOR.encode() in kept
    assert EMAIL.encode() not in kept and NOMBRE.encode() not in kept
    assert b"persona" not in kept.lower()


def test_el_check_es_estricto_con_recordar_dispositivo_y_el_codigo_de_recuperacion(
    tmp_path, vaultwarden_db
):
    result = exported_and_collected(tmp_path, vaultwarden_db)

    by_id = {u["id"]: u["second_factor"] for u in result.data["users"]}
    assert by_id == {SIN_FACTOR: False, CON_FACTOR: True}


def test_con_todos_con_doble_factor_pasa(tmp_path):
    found = checked(tmp_path, [user_dict(3), user_dict(4)])

    assert second_factor_of(found).status is CheckStatus.PASS


# --- «la app no ve cofres» ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((REPO / "compose.yaml").read_text(encoding="utf-8"))


def mounts_of(service: dict) -> list[str]:
    return [str(v) for v in service.get("volumes", [])]


def test_la_app_solo_monta_el_export_en_solo_lectura(compose):
    app = compose["services"]["app"]

    exported = [m for m in mounts_of(app) if m.startswith("contrasenas-")]
    assert exported == ["contrasenas-export:/run/kit/contrasenas:ro"]
    assert not any("vaultwarden" in m or "contrasenas-snapshot" in m for m in mounts_of(app))


def test_la_app_no_esta_en_la_red_de_vaultwarden(compose):
    services = compose["services"]

    assert "contrasenas-net" not in services["app"]["networks"]
    members = {n for n, s in services.items() if "contrasenas-net" in s.get("networks", [])}
    assert members == {"caddy", "vaultwarden"}


def test_la_base_de_vaultwarden_solo_la_ve_el_exportador_y_en_solo_lectura(compose):
    users = {
        n: [m for m in mounts_of(s) if m.startswith("vaultwarden-data:")]
        for n, s in compose["services"].items()
    }

    mounted = {n: m for n, m in users.items() if m}
    assert mounted == {
        "vaultwarden": ["vaultwarden-data:/data"],
        "contrasenas-export": ["vaultwarden-data:/source:ro"],
    }


def test_el_exportador_no_tiene_red_ni_las_credenciales_ni_el_volumen_de_la_app(compose):
    exporter = compose["services"]["contrasenas-export"]

    assert exporter["network_mode"] == "none" and "networks" not in exporter
    assert "env_file" not in exporter
    assert not any(m.startswith("data:") for m in mounts_of(exporter))


VAULT_FIELDS = [
    ("cifrados en la raiz", lambda d: d.update(ciphers=[{"login": {"password": CANARIO}}])),
    ("carpetas en la raiz", lambda d: d.update(folders=[{"name": CANARIO}])),
    ("login en un usuario", lambda d: d["users"][0].update(login={"password": CANARIO})),
    ("email en un usuario", lambda d: d["users"][0].update(email=CANARIO)),
    ("clave del usuario", lambda d: d["users"][0].update(akey=CANARIO)),
]


@pytest.mark.parametrize("mutate", [m for _, m in VAULT_FIELDS], ids=[n for n, _ in VAULT_FIELDS])
def test_un_export_con_campos_de_cofre_se_rechaza_y_no_se_guarda_nada(tmp_path, mutate):
    doc = export_dict([user_dict()])
    mutate(doc)

    result = collect(written(tmp_path, doc))
    found = outcomes(result)
    store = LocalStore(tmp_path / "ws")
    import_collected(store, result, collector_version="1", now=NOW)

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"] == reader.INVALID and "users" not in result.data
    kept = result.model_dump_json() + " ".join(o.reason for o in found.values())
    assert CANARIO not in kept
    assert CANARIO.encode() not in workspace_bytes(tmp_path / "ws")
    assert all(o.status is not CheckStatus.PASS for o in found.values())


def test_el_modelo_del_export_rechaza_cualquier_campo_de_cofre():
    doc = export_dict([user_dict(login={"password": CANARIO})])

    with pytest.raises(ValidationError):
        PasswordsExport.model_validate(doc)


# --- C13: inactividad ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("idle_days", "status"),
    [
        (LIMIT_DAYS - 1, CheckStatus.PASS),
        (LIMIT_DAYS, CheckStatus.PASS),
        (LIMIT_DAYS + 1, CheckStatus.FAIL),
    ],
)
def test_la_inactividad_se_mide_contra_el_umbral_antes_en_y_despues(tmp_path, idle_days, status):
    found = checked(tmp_path, [user_dict(last_active=days_ago(idle_days).isoformat())])

    outcome = inactivity_of(found)
    assert outcome.status is status
    if status is CheckStatus.FAIL:
        assert uid(3) in outcome.reason and "no la corrige" in outcome.reason


def test_sin_actividad_registrada_se_mide_desde_created_at_y_lo_dice(tmp_path):
    old = user_dict(last_active=None, created_at=days_ago(LIMIT_DAYS + 30).isoformat())

    outcome = inactivity_of(checked(tmp_path, [old]))

    assert outcome.status is CheckStatus.FAIL and "created_at" in outcome.reason


def test_el_respaldo_created_at_tambien_pasa_si_el_alta_es_reciente_y_se_nombra(tmp_path):
    recent = user_dict(last_active=None, created_at=days_ago(10).isoformat())

    outcome = inactivity_of(checked(tmp_path, [recent]))

    assert outcome.status is CheckStatus.PASS and "created_at" in outcome.reason


def test_la_inactividad_solo_cuenta_a_los_usuarios_activos_y_no_invitados(tmp_path):
    stale = days_ago(LIMIT_DAYS + 50).isoformat()
    users = [
        user_dict(3),
        user_dict(4, enabled=False, last_active=stale),
        user_dict(5, invited=True, last_active=None, created_at=stale),
    ]

    assert inactivity_of(checked(tmp_path, users)).status is CheckStatus.PASS


# --- C13: sin export o con error, nunca favorable ----


def missing(tmp_path):
    return tmp_path / "no-existe.json"


BAD_EXPORTS = {
    "ausente": lambda p: missing(p),
    "viejo": lambda p: written(
        p, export_dict([user_dict()], exported_at=(NOW - timedelta(hours=2)).isoformat())
    ),
    "con fecha futura": lambda p: written(
        p, export_dict([user_dict()], exported_at=(NOW + timedelta(hours=1)).isoformat())
    ),
    "ilegible": lambda p: written(p, raw=b"\xff\xfe no es json"),
    "de otro formato": lambda p: written(p, export_dict([user_dict()], format=2)),
    "vacio": lambda p: written(p, raw=b""),
}


@pytest.mark.parametrize("make", list(BAD_EXPORTS.values()), ids=list(BAD_EXPORTS))
def test_un_export_ausente_o_erroneo_nunca_da_pass(tmp_path, make):
    result = collect(make(tmp_path))
    found = outcomes(result)

    assert result.collection_status is CollectionStatus.ERROR
    assert "users" not in result.data
    assert {o.status for o in found.values()} <= {CheckStatus.ERROR, CheckStatus.UNKNOWN}


# --- C4: población vacía ----

EMPTY_POPULATIONS = {
    "sin usuarios": [],
    "todos deshabilitados": [user_dict(3, enabled=False), user_dict(4, enabled=False)],
    "todos invitados": [user_dict(3, invited=True), user_dict(4, invited=True)],
}


@pytest.mark.parametrize("users", list(EMPTY_POPULATIONS.values()), ids=list(EMPTY_POPULATIONS))
def test_sin_usuarios_evaluables_el_resultado_es_unknown(tmp_path, users):
    found = checked(tmp_path, users)

    assert second_factor_of(found).status is CheckStatus.UNKNOWN
    assert inactivity_of(found).status is CheckStatus.UNKNOWN
