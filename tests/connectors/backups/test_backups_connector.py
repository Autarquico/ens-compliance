"""El conector `backups`: N repositorios, solo lectura, una sección por repositorio."""

import json

import pytest
from pydantic import ValidationError

from connectors.restic.fake_restic import (
    NOW,
    OTHER_REPOSITORY_ID,
    PASSWORD,
    REPOSITORY_ID,
    FakeRestic,
    config_json,
    snapshot_json,
)
from ens_evidence.backup.environment import load_secrets
from ens_evidence.backup.errors import BackupError
from ens_evidence.connectors.backups import BackupsConfig, BackupsConnector, BackupsRun
from ens_evidence.connectors.backups import checks as backups_checks
from ens_evidence.connectors.restic.process import (
    ALLOWED,
    LIST_ALL_SNAPSHOTS,
    ResticProcess,
)
from ens_evidence.models.enums import CheckStatus, CollectionStatus

THRESHOLDS = {
    "max_backup_age_hours": 26,
    "max_check_age_days": 7,
    "restore_test_validity_days": 90,
}
ACCESS, SECRET = "AKIA-CANARIO-LECTURA", "secreto-canario-lectura"


def repo(repo_id: str, asset: str, location: str = "s3:s3.example.com/copias") -> dict:
    return {"id": repo_id, "asset_id": asset, "repository": location, **THRESHOLDS}


def config(*repositories: dict) -> BackupsRun:
    return BackupsRun(repositories=list(repositories))


def secrets_env(*ids: str) -> dict:
    env = {"PATH": "/usr/bin"}
    for repo_id in ids:
        env |= {
            f"ENS_BACKUPS_{repo_id.upper()}_RESTIC_PASSWORD": PASSWORD,
            f"ENS_BACKUPS_{repo_id.upper()}_AWS_ACCESS_KEY_ID": ACCESS,
            f"ENS_BACKUPS_{repo_id.upper()}_AWS_SECRET_ACCESS_KEY": SECRET,
        }
    return env


def untagged_snapshot(time: str = "2026-10-28T11:00:00+00:00") -> dict:
    return {**snapshot_json(time=time), "tags": ["plan:diario", "created-by:backrest"]}


def connector(fakes: dict[str, FakeRestic]) -> BackupsConnector:
    return BackupsConnector(executor=lambda r: fakes[r.id], clock=lambda: NOW)


def test_un_resultado_con_una_seccion_por_repositorio_y_su_asset_id():
    fakes = {
        "caja": FakeRestic().with_snapshots(untagged_snapshot()),
        "oficina": FakeRestic(cat=(0, config_json(OTHER_REPOSITORY_ID))).with_snapshots(),
    }

    result = connector(fakes).collect(config(repo("caja", "bk-caja"), repo("oficina", "bk-of")))

    assert result.source == "backups"
    entries = result.data["repositories"]
    assert [(e["id"], e["asset_id"], e["repository_id"]) for e in entries] == [
        ("caja", "bk-caja", REPOSITORY_ID),
        ("oficina", "bk-of", OTHER_REPOSITORY_ID),
    ]
    assert result.collection_status is CollectionStatus.SUCCESS


def test_lee_solo_con_no_lock_y_cuenta_snapshots_de_cualquier_etiqueta():
    fake = FakeRestic().with_snapshots(untagged_snapshot())

    result = connector({"caja": fake}).collect(config(repo("caja", "bk-caja")))

    assert LIST_ALL_SNAPSHOTS in fake.calls
    assert all(call[:2] == ("--no-lock", "--no-cache") for call in fake.calls)
    assert result.data["repositories"][0]["sections"]["snapshots"]["summary"]["count"] == 1


def test_el_proceso_real_admite_listar_todos_los_snapshots_y_nada_mas():
    allowed = frozenset({*ALLOWED, LIST_ALL_SNAPSHOTS})
    process = ResticProcess(
        "/r", load_secrets("/r", {"ENS_BACKUP_RESTIC_PASSWORD": "x"}), allowed=allowed
    )

    with pytest.raises(ValueError, match="no permitido"):
        process(("--no-lock", "--no-cache", "forget", "--prune"))


def test_sin_credenciales_el_repositorio_queda_en_error_y_sus_checks_no_pasan():
    result = BackupsConnector(env=secrets_env(), clock=lambda: NOW).collect(
        config(repo("caja", "bk-caja"))
    )

    entry = result.data["repositories"][0]
    assert entry["collection_status"] == "ERROR"
    assert "ENS_BACKUPS_CAJA_RESTIC_PASSWORD" in entry["error"]
    checks = [r for check in backups_checks.ALL_CHECKS for r in check(result, "ev-1", NOW)]
    assert len(checks) == 6
    assert CheckStatus.PASS not in {c.status for c in checks}
    assert not json.dumps(result.model_dump(mode="json")).count(PASSWORD)


def test_las_credenciales_se_leen_por_repositorio():
    env = secrets_env("caja")

    assert load_secrets("s3:h/b", env, "ENS_BACKUPS_CAJA_").password == PASSWORD
    with pytest.raises(BackupError, match="ENS_BACKUPS_OFICINA_RESTIC_PASSWORD"):
        load_secrets("s3:h/b", env, "ENS_BACKUPS_OFICINA_")


def test_un_repositorio_sin_credenciales_no_impide_leer_el_otro():
    fakes = {"caja": FakeRestic().with_snapshots(untagged_snapshot())}
    unit = BackupsConnector(
        env=secrets_env(),
        executor=lambda r: fakes[r.id] if r.id == "caja" else _raise(),
        clock=lambda: NOW,
    )

    result = unit.collect(config(repo("caja", "bk-caja"), repo("oficina", "bk-of")))

    statuses = {e["id"]: e["collection_status"] for e in result.data["repositories"]}
    assert statuses == {"caja": "SUCCESS", "oficina": "ERROR"}
    assert result.collection_status is CollectionStatus.ERROR


def _raise():
    raise BackupError("faltan credenciales")


def test_los_checks_se_evaluan_por_repositorio_con_su_asset_id():
    fakes = {
        "caja": FakeRestic().with_snapshots(untagged_snapshot()),
        "oficina": FakeRestic().with_snapshots(untagged_snapshot("2026-10-01T11:00:00+00:00")),
    }
    result = connector(fakes).collect(config(repo("caja", "bk-caja"), repo("oficina", "bk-of")))

    recent = backups_checks.backup_001_recent(result, "ev-1", NOW)

    assert [(c.check_id, c.asset_id, c.status) for c in recent] == [
        ("backups.backup_001_recent", "bk-caja", CheckStatus.PASS),
        ("backups.backup_001_recent", "bk-of", CheckStatus.FAIL),
    ]
    every = [r for check in backups_checks.ALL_CHECKS for r in check(result, "ev-1", NOW)]
    assert len(every) == 12
    assert {
        c.status for c in every if c.check_id.endswith(("004_integrity", "005_restore_test"))
    } == {CheckStatus.UNKNOWN}
    assert all(c.evidence_ids == ["ev-1"] for c in every)


def test_sin_repositorios_interpretables_no_hay_pass():
    from ens_evidence.models.contracts import CollectionResult

    empty = CollectionResult(
        source="backups",
        collector="backups",
        collected_at=NOW,
        asset_id="backups",
        data={},
        collection_status=CollectionStatus.SUCCESS,
    )

    results = backups_checks.backup_001_recent(empty, "ev-1", NOW)

    assert [r.status for r in results] == [CheckStatus.UNKNOWN]


@pytest.mark.parametrize(
    "repositories",
    [
        [repo("a", "x"), repo("A", "y")],
        [repo("a", "x"), repo("b", "x")],
        [repo("mi-copia", "x")],
        [],
        [{**repo("a", "x"), "password": "s"}],
    ],
)
def test_la_configuracion_rechaza_ids_repetidos_o_invalidos_y_claves_ajenas(repositories):
    with pytest.raises(ValidationError):
        BackupsConfig(repositories=repositories)
