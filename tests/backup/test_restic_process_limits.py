import os
import stat

import pytest

from ens_evidence.backup import BackupError, BackupSecrets, Restic

from .conftest import PASSWORD


def fake(directory, monkeypatch, body: str) -> None:
    script = directory / "restic"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


def test_restic_no_hereda_la_entrada_estandar(tmp_path, monkeypatch):
    seen = tmp_path / "stdin.txt"
    fake(tmp_path, monkeypatch, f'cat > "{seen}"\necho \'{{"id": "{"ab" * 32}"}}\'')
    typed = tmp_path / "typed"
    typed.write_text("TECLADO")
    saved = os.dup(0)
    os.dup2(os.open(typed, os.O_RDONLY), 0)
    try:
        Restic(str(tmp_path / "repo"), BackupSecrets(PASSWORD)).repository_id()
    finally:
        os.dup2(saved, 0)

    assert seen.read_text() == ""


def test_una_lectura_que_no_responde_acaba_en_error_estructurado(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "sleep 5")
    restic = Restic(str(tmp_path / "repo"), BackupSecrets(PASSWORD), read_timeout=0.2)

    with pytest.raises(BackupError, match="no respondió"):
        restic.repository_id()
