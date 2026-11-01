"""Instrumentos de los tests de aceptación: reloj fijo, CLI sobre un workspace y org-docs reales."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from orgdocs.orgdocs_helpers import SIGNATORY, fixture_answers, write_answers
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import approve_document, build_documents

from .evaluate_helpers import NOW

EVIDENCE_ID = re.compile(r"EV-\d{8}-[0-9a-f]{8}")
ASSET = "svc-facturacion"
SELF = ["--by", "Ana", "--role", "system_administrator"]
APPROVER = {
    "politica-seguridad": Role.MANAGEMENT,
    "normativa-seguridad": Role.MANAGEMENT,
    "designacion-responsables": Role.MANAGEMENT,
    "procedimientos-seguridad": Role.SECURITY_OFFICER,
    "proceso-autorizacion": Role.SECURITY_OFFICER,
    "procedimiento-incidentes": Role.SECURITY_OFFICER,
}


class Clock:
    """El reloj de la CLI: `matrix …` y `evidence import` leen la misma hora."""

    def __init__(self, monkeypatch) -> None:
        self.now = NOW
        clock = self

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now

        monkeypatch.setattr("ens_evidence.cli.datetime", Frozen)
        monkeypatch.setattr("ens_evidence.cli.clock", lambda: clock.now)


def settings_file(tmp_path: Path, workspace: dict) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"workspace": workspace}))
    return path


def runner(settings: Path):
    def run(*args: str):
        return CliRunner().invoke(app, ["--settings", str(settings), *args])

    return run


def generate_and_approve_documents(store, approved_at: datetime = NOW - timedelta(days=1)) -> None:
    """Los seis documentos reales de org-docs, generados con las respuestas y aprobados."""
    write_answers(store, fixture_answers())
    build_documents(store)
    for name, role in APPROVER.items():
        approve_document(store, name, SIGNATORY[role], role, approved_at)


def import_manual_evidence(run, tmp_path: Path, *, content: str = "acta de revisión") -> str:
    """`evidence import --source manual` por la CLI; devuelve el id de la evidencia."""
    artifact = tmp_path / "evidencia.txt"
    artifact.write_text(content, encoding="utf-8")
    result = run(
        *("evidence", "import", str(artifact), "--source", "manual"),
        *("--asset", ASSET, "--by", "Ana"),
        *("--valid-until", (NOW + timedelta(days=365)).isoformat()),
    )
    assert result.exit_code == 0, result.output
    match = EVIDENCE_ID.search(result.output)
    assert match, result.output
    return match.group()


def configure_measure(run, measure: str, every: str = "30") -> None:
    owner = ("--owner", "Ana", "--owner-role", "system_administrator")
    assert run("matrix", "set", measure, *owner).exit_code == 0
    assert run("matrix", "set", measure, "--every", every).exit_code == 0


def link(run, measure: str, kind: str, target: str, covers: str) -> None:
    result = run("matrix", "link", measure, f"--{kind}", target, "--covers", covers)
    assert result.exit_code == 0, result.output


def review(run, measure: str, assessment: str = "COMPLIANT"):
    return run(
        *("matrix", "review", measure, *SELF),
        *("--assessment", assessment, "--conclusion", "Revisada"),
    )


def line_of(output: str, measure: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(f"{measure}  "))
