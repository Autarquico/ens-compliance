import dataclasses
import json
import re
from datetime import timedelta

import pytest
from checks.acceptance_helpers import GITHUB_CONFIG
from checks.test_engine import write_config
from matrix.evaluate_helpers import NOW

from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.matrix import assess
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, Readiness
from ens_evidence.models.readiness import ReadinessResult
from ens_evidence.scope import scope_status
from ens_evidence.workspace import WorkspaceError

AT = ["--at", NOW.isoformat()]
PENDING = "Revisión humana pendiente: la capa de juicio de la pre-auditoría no se ha ejecutado"
NO_PRE_AUDIT = "Pre-auditoría no ejecutada sobre el expediente vigente."
SECRET = "github.secret_scanning_enabled"


def record_failed_secret_scanning(store, finished):
    result = CheckResult(
        check_id=SECRET,
        asset_id="gh-taller",
        subject="infra",
        status=CheckStatus.FAIL,
        evaluated_at=finished,
        reason="secret scanning desactivado",
    )
    run = StoredCheckRun(
        seq=1,
        connector="github",
        engine_version="1",
        started_at=finished,
        finished_at=finished,
        complete=True,
        scope_fingerprint=scope_status(store).fingerprint,
        config_sha256="b" * 64,
        results=[result],
    )
    record_run(store, run, finished)


@pytest.fixture
def ready(monkeypatch):
    """Una evaluación READY (la regla de la matriz es ajena a este comando)."""

    def fake(store, at, *, checks=None, **_):
        real = assess(store, at, checks=checks)
        result = ReadinessResult(readiness=Readiness.READY, blockers=[], pending_human_review=True)
        return dataclasses.replace(real, readiness=result, blockers=())

    monkeypatch.setattr("ens_evidence.assessment.status.assess", fake)


def test_texto_no_preparada_sale_con_1_y_avisa_de_la_revision_humana(cli):
    result = cli("assessment", "status", *AT)

    assert result.exit_code == 1
    assert "Preparación: no preparada" in result.output
    assert PENDING in result.output and NO_PRE_AUDIT in result.output
    assert "La herramienta no declara conformidad." in result.output


def test_cada_bloqueo_indica_su_medida_y_el_recuento_cuadra_con_la_lista(cli):
    lines = cli("assessment", "status", *AT).output.splitlines()

    header = next(line for line in lines if line.startswith("Preparación:"))
    blockers = [line for line in lines if re.match(r"^  [a-z]+(\.[a-z]+)*\.\d+: ", line)]
    measures = {line.split(":")[0].strip() for line in blockers}
    assert header.endswith(f"{len(blockers)} bloqueos en {len(measures)} medidas.")
    assert len(blockers) > len(measures)


def test_texto_preparada_sale_con_0_y_sigue_avisando_de_la_revision_humana(cli, ready):
    result = cli("assessment", "status", *AT)

    assert result.exit_code == 0
    assert "Preparación: preparada" in result.output
    assert PENDING in result.output and NO_PRE_AUDIT in result.output


def test_json_no_preparada(cli):
    result = cli("assessment", "status", *AT, "--format", "json")

    document = json.loads(result.output)
    assert result.exit_code == 1
    assert document["readiness"] == "NOT_READY"
    assert document["pending_human_review"] is True
    assert document["pre_audit"] == NO_PRE_AUDIT
    assert document["blockers"] and all(b["text"] for b in document["blockers"])
    assert document["evaluated_at"] == NOW.isoformat()


def test_json_preparada_mantiene_la_revision_humana_pendiente(cli, ready):
    result = cli("assessment", "status", *AT, "--format", "json")

    document = json.loads(result.output)
    assert result.exit_code == 0
    assert document["readiness"] == "READY"
    assert document["blockers"] == []
    assert document["pending_human_review"] is True


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_la_salida_es_identica_byte_a_byte_con_el_mismo_at(cli, fmt):
    first = cli("assessment", "status", *AT, "--format", fmt)
    second = cli("assessment", "status", *AT, "--format", fmt)

    assert first.output == second.output
    assert first.stdout_bytes == second.stdout_bytes


def test_es_una_lectura_pura(cli, signed):
    record_failed_secret_scanning(signed, NOW - timedelta(hours=3))
    before = {k: signed.read_bytes(k) for k in signed.list("")}

    cli("assessment", "status", *AT)
    cli("assessment", "status", *AT, "--format", "json")

    assert {k: signed.read_bytes(k) for k in signed.list("")} == before


def test_resume_las_fuentes_tecnicas_por_conector(cli, signed):
    finished = NOW - timedelta(hours=3)
    record_failed_secret_scanning(signed, finished)

    result = cli("assessment", "status", *AT)

    github = f"github: ejecución de {finished.isoformat()}  PASS 0  FAIL 1  UNKNOWN 0"
    assert github in result.output
    for connector in ("restic", "cloudflare", "endpoint", "google-workspace"):
        assert f"{connector}: no configurado" in result.output
    assert "sin ejecución" not in result.output


def test_un_conector_configurado_sin_ejecucion_manda_a_ejecutar_check_all(cli, signed):
    write_config(signed, "github", GITHUB_CONFIG)

    result = cli("assessment", "status", *AT)
    document = json.loads(cli("assessment", "status", *AT, "--format", "json").output)

    assert "github: sin ejecución; ejecuta `ens-evidence check --all`" in result.output
    configured = {c["connector"]: c["configured"] for c in document["checks"]}
    assert configured["github"] is True and configured["restic"] is False


def test_json_resume_las_fuentes_tecnicas(cli, signed):
    record_failed_secret_scanning(signed, NOW - timedelta(hours=3))

    document = json.loads(cli("assessment", "status", *AT, "--format", "json").output)

    github = next(c for c in document["checks"] if c["connector"] == "github")
    assert github["counts"]["FAIL"] == 1 and github["complete"] is True
    restic = next(c for c in document["checks"] if c["connector"] == "restic")
    assert restic["finished_at"] is None


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_nunca_dice_conforme_ni_cumple(cli, ready, fmt):
    output = cli("assessment", "status", *AT, "--format", fmt).output.lower()

    assert "conforme" not in output and "cumple" not in output


def test_una_fecha_invalida_sale_con_2(cli):
    result = cli("assessment", "status", "--at", "ayer")

    assert result.exit_code == 2


def test_un_error_de_workspace_sale_con_2(cli, monkeypatch):
    def broken(store, at, checks):
        raise WorkspaceError("sin acceso")

    monkeypatch.setattr("ens_evidence.cli.assessment_status", broken)

    result = cli("assessment", "status")

    assert result.exit_code == 2
    assert "sin acceso" in result.output


def test_un_historial_ilegible_impide_el_exito(cli, signed, ready):
    record_failed_secret_scanning(signed, NOW - timedelta(hours=3))
    key = next(k for k in signed.list("evidence/checks"))
    signed.write_text(key, "no es json")

    result = cli("assessment", "status", *AT)

    assert "check_history_unreadable" in result.output
    assert result.exit_code == 1
