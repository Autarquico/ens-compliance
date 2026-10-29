"""Ni la contraseña ni la salida de restic llegan al artefacto, a las excepciones ni a los logs."""

import json
import logging
import stat
import sys

import pytest

from ens_evidence.backup.environment import BackupSecrets
from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.connectors.restic.process import CAT_CONFIG, ResticProcess
from ens_evidence.evidence.importer import canonical_json
from ens_evidence.workspace.settings import BackupSettings

from .fake_restic import CANARY, NOW, PASSWORD, REPOSITORY, FakeRestic, snapshot_json
from .record_data import backup_run, restic_run

NOISE = f"{CANARY} {PASSWORD} password={PASSWORD}"
FORBIDDEN = (CANARY, PASSWORD)


def artifact(result) -> str:
    return canonical_json(result).decode()


def log_text(caplog) -> str:
    return "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)


def assert_clean(text: str) -> None:
    for secret in FORBIDDEN:
        assert secret not in text


CAT_FAILURES = [(12, NOISE), (1, NOISE), (0, NOISE), (0, f'{{"id": "{NOISE}"}}')]


@pytest.mark.parametrize("cat", CAT_FAILURES)
def test_la_salida_de_cat_config_no_llega_al_artefacto_ni_al_log(cat, caplog):
    caplog.set_level(logging.DEBUG)
    connector = ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY),
        executor=FakeRestic(cat=cat),
        clock=lambda: NOW,
    )

    result = connector.collect(restic_run(runs=[backup_run()]))

    assert_clean(artifact(result))
    assert_clean(log_text(caplog))


@pytest.mark.parametrize(
    "snapshots",
    [(1, NOISE), (0, NOISE), (0, f'[{{"id": "{NOISE}"}}]'), (0, f"[{snapshot_json()['id']!r}]")],
)
def test_la_salida_de_snapshots_no_llega_al_artefacto_ni_al_log(snapshots, caplog):
    caplog.set_level(logging.DEBUG)
    restic = FakeRestic(snapshots=snapshots)
    connector = ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY), executor=restic, clock=lambda: NOW
    )

    result = connector.collect(restic_run())

    assert_clean(artifact(result))
    assert_clean(log_text(caplog))


def test_el_error_de_una_ejecucion_registrada_no_llega_al_artefacto(collect):
    run = backup_run("r1", "failed").model_copy(update={"error": f"fallo {NOISE}"})

    assert_clean(artifact(collect(runs=[run])))


def test_el_rechazo_de_un_comando_no_permitido_no_incluye_secretos():
    process = ResticProcess("/repo", BackupSecrets(PASSWORD), base_env={"PATH": "/nonexistent"})

    with pytest.raises(ValueError) as error:
        process(("--no-lock", "--no-cache", "restore", PASSWORD))

    assert PASSWORD not in str(error.value)


@pytest.fixture
def restic_bin(tmp_path) -> str:
    """Un `restic` real de proceso que escribe canarios y la contraseña recibida por entorno."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "restic"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"print('{CANARY}', os.environ['RESTIC_PASSWORD'])\n"
        f"print('{CANARY}', os.environ['RESTIC_PASSWORD'], file=sys.stderr)\n"
        "sys.exit(12)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(bin_dir)


def test_con_un_proceso_real_que_imprime_secretos_nada_llega_al_artefacto_ni_al_log(
    restic_bin, caplog
):
    caplog.set_level(logging.DEBUG)
    connector = ResticConnector(
        backup=BackupSettings(restic_repository="/repos/copias"),
        clock=lambda: NOW,
        env={"ENS_BACKUP_RESTIC_PASSWORD": PASSWORD, "PATH": restic_bin},
    )

    result = connector.collect(restic_run())

    assert result.data["error"] and "12" in result.data["error"]
    assert_clean(artifact(result))
    assert_clean(log_text(caplog))


def test_el_proceso_recibe_la_contrasena_por_entorno_y_la_entrada_estandar_cerrada(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "restic"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "null = os.stat(os.devnull)\n"
        "print(json.dumps({'argv': sys.argv[1:], 'password': os.environ.get('RESTIC_PASSWORD'),\n"
        "  'stdin_is_devnull': os.fstat(0).st_rdev == null.st_rdev,\n"
        "  'env': sorted(os.environ)}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    process = ResticProcess(
        "/repo", BackupSecrets(PASSWORD), base_env={"PATH": str(bin_dir), "OTRA": "no-se-hereda"}
    )

    seen = json.loads(process(CAT_CONFIG).stdout)

    assert seen["argv"] == ["--repo", "/repo", *CAT_CONFIG]
    assert PASSWORD not in " ".join(seen["argv"])
    assert seen["password"] == PASSWORD
    assert seen["stdin_is_devnull"] is True
    assert "OTRA" not in seen["env"]
