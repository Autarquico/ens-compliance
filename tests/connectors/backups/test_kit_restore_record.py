"""Recorrido de una restauración hecha con Backrest hasta BACKUP-005 (kit de copias).

restic real sobre un repositorio local temporal; la restauración la hace `restic restore`, que es
lo que Backrest ejecuta por debajo. `backup restore-test record-kit` compara la carpeta restaurada
con la original y registra la prueba que declara la persona.
"""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from connectors.restic.cli_support import ens, write_workspace
from ens_evidence.backup import list_restores
from ens_evidence.connectors.backups import BackupsConnector
from ens_evidence.connectors.backups import checks as backups_checks
from ens_evidence.connectors.backups.config import BackupsConfig
from ens_evidence.connectors.config import load_connector_config
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.models.enums import CheckStatus

PASSWORD = "contrasena-de-prueba-0123"
NOW = datetime.now(UTC).replace(microsecond=0)
FILES = {"a.txt": b"uno", "sub/b.bin": bytes(range(200)), "sub/hondo/c.txt": b"tres"}

pytestmark = pytest.mark.usefixtures("real_restic")


def restic(repository: Path, *args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(
        ["restic", "-r", str(repository), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={
            "RESTIC_PASSWORD": PASSWORD,
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
            "HOME": os.environ["HOME"],
        },
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def original(tmp_path) -> Path:
    root = tmp_path / "original"
    for name, content in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    return root


@pytest.fixture
def box(tmp_path, original, monkeypatch):
    """(settings, store, snapshot_id, carpeta restaurada): copia hecha y restaurada de verdad."""
    repository = tmp_path / "repo"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ENS_BACKUPS_CAJA_RESTIC_PASSWORD", PASSWORD)
    restic(repository, "init")
    restic(repository, "backup", ".", cwd=original)
    snapshot = json.loads(restic(repository, "snapshots", "--json"))[0]["id"]
    target = tmp_path / "restaurado"
    restic(repository, "restore", snapshot, "--target", str(target))
    # restic restaura la ruta completa del origen bajo el destino
    restored = next(target.rglob("a.txt")).parent
    settings, store = write_workspace(tmp_path / "base", str(repository))
    store.write_text(
        "connectors/backups.yaml",
        yaml.safe_dump(
            {
                "repositories": [
                    {
                        "id": "caja",
                        "asset_id": "bk-diario",
                        "repository": str(repository),
                        "max_backup_age_hours": 26,
                        "max_check_age_days": 7,
                        "restore_test_validity_days": 90,
                    }
                ]
            }
        ),
    )
    return settings, store, snapshot, restored


def declaration(snapshot: str, **review_overrides) -> dict:
    review = {
        "approved": True,
        "reviewer": "Ana Gil",
        "role": "security_officer",
        "reviewed_at": (NOW - timedelta(minutes=30)).isoformat(),
        "valid_until": (NOW + timedelta(days=90)).isoformat(),
        **review_overrides,
    }
    return {
        "test_id": "RT-BACKREST-1",
        "system": "bk-diario",
        "performed_at": (NOW - timedelta(hours=1)).isoformat(),
        "isolated_target": True,
        "backup": {"snapshot_id": snapshot},
        "result": {"restored": True, "integrity_verified": True, "application_started": True},
        "review": review,
    }


def record(box, tmp_path, original, restored=None, **fields):
    settings, _store, snapshot, restored_dir = box
    document = {**declaration(snapshot), **fields}
    path = tmp_path / "prueba.yaml"
    path.write_text(yaml.safe_dump(document))
    return ens(
        settings,
        *("backup", "restore-test", "record-kit", str(path)),
        *("--repository", "caja"),
        *("--restored", str(restored or restored_dir)),
        *("--original", str(original)),
    )


def backup_005(store) -> CheckStatus:
    connector = BackupsConnector()
    config = connector.prepare(load_connector_config(store, "backups", BackupsConfig), store)
    result = connector.collect(config)
    (outcome,) = backups_checks.backup_005_restore_test(result, "ev-1", datetime.now(UTC))
    return outcome.status


def test_restaurar_con_backrest_y_registrarlo_con_la_persona_lleva_backup_005_a_pass(
    box, tmp_path, original
):
    _settings, store, snapshot, _restored = box

    result = record(box, tmp_path, original)

    assert result.exit_code == 0, result.output
    (restore,), _ = list_restores(store)
    assert (restore.inventory_verified, restore.mismatches) == (True, 0)
    assert restore.snapshot_id == snapshot
    saved = yaml.safe_load(store.read_bytes("evidence/restore-tests/RT-BACKREST-1.yaml"))
    assert saved["review"]["reviewer"] == "Ana Gil"
    assert saved["restore_id"] == restore.restore_id
    (manifest,) = list_manifests(store).manifests
    assert manifest.provenance.submitted_by == "Ana Gil"
    assert backup_005(store) is CheckStatus.PASS


def test_sin_registrar_ninguna_prueba_backup_005_no_es_pass(box):
    assert backup_005(box[1]) is not CheckStatus.PASS


def test_si_lo_restaurado_difiere_la_restauracion_queda_no_verificada_y_no_hay_pass(
    box, tmp_path, original
):
    _settings, store, _snapshot, restored = box
    (restored / "sub" / "b.bin").write_bytes(b"corrupto")
    (restored / "sobra.txt").write_text("x")

    result = record(box, tmp_path, original)

    assert result.exit_code == 1
    assert "sub/b.bin" in result.output and "sobra.txt" in result.output
    (restore,), _ = list_restores(store)
    assert (restore.inventory_verified, restore.mismatches) == (False, 2)
    assert store.list("evidence/restore-tests") == []
    assert backup_005(store) is not CheckStatus.PASS


def test_comparar_una_carpeta_consigo_misma_no_verifica_nada(box, tmp_path, original):
    result = record(box, tmp_path, original, restored=original)

    assert result.exit_code == 1
    assert "distintas" in result.output
    assert list_restores(box[1]) == ([], [])
    assert backup_005(box[1]) is not CheckStatus.PASS


def test_sin_revisor_no_se_registra_nada_y_la_herramienta_no_lo_rellena(box, tmp_path, original):
    settings, store, snapshot, _ = box
    document = declaration(snapshot)
    del document["review"]["reviewer"]
    path = tmp_path / "sin-revisor.yaml"
    path.write_text(yaml.safe_dump(document))

    result = ens(
        settings,
        *("backup", "restore-test", "record-kit", str(path), "--repository", "caja"),
        *("--restored", str(box[3]), "--original", str(original)),
    )

    assert result.exit_code == 1
    assert "reviewer" in result.output
    assert list_restores(store) == ([], [])
    assert store.list("evidence/restore-tests") == []
    assert backup_005(store) is not CheckStatus.PASS


def test_la_declaracion_no_puede_citar_un_restore_id_propio(box, tmp_path, original):
    result = record(box, tmp_path, original, restore_id="RS-INVENTADO")

    assert result.exit_code == 1
    assert "restore_id" in result.output
    assert list_restores(box[1]) == ([], [])


def test_una_carpeta_original_vacia_no_verifica_nada(box, tmp_path):
    empty = tmp_path / "vacia"
    empty.mkdir()

    result = record(box, tmp_path, empty)

    assert result.exit_code == 1
    assert "nada que comparar" in result.output
    assert list_restores(box[1]) == ([], [])


def test_un_repositorio_desconocido_se_rechaza(box, tmp_path, original):
    settings, _, snapshot, restored = box
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(declaration(snapshot)))

    result = ens(
        settings,
        *("backup", "restore-test", "record-kit", str(path), "--repository", "otra"),
        *("--restored", str(restored), "--original", str(original)),
    )

    assert result.exit_code == 2
    assert "otra" in result.output


def test_un_enlace_simbolico_en_lo_restaurado_no_cuenta_como_fichero_igual(box, tmp_path, original):
    restored = box[3]
    (restored / "a.txt").unlink()
    (restored / "a.txt").symlink_to(original / "a.txt")

    result = record(box, tmp_path, original)

    assert result.exit_code == 1
    assert "a.txt" in result.output
    assert backup_005(box[1]) is not CheckStatus.PASS
