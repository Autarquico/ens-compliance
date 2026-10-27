"""Los cinco scenarios de docs/specs/repo-foundation.md, con aserciones sobre el Then."""

import re
from pathlib import Path

import pytest
import yaml
from fake_connector import FakeConfig, FakeConnector
from pydantic import ValidationError

from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.models import (
    Applicability,
    CheckResult,
    CheckStatus,
    CollectionStatus,
    Measure,
    MeasureAssessment,
    Readiness,
    ReadinessInput,
    Role,
    enums,
    evaluate_readiness,
)
from ens_evidence.models.contracts import Review
from ens_evidence.models.readiness import BlockerKind, EvidenceRef

ROOT = Path(__file__).resolve().parent.parent
ADR = ROOT / "docs/decisions/0002-modelo-de-estados.md"


def test_existe_un_unico_enum_por_eje_documentado_en_un_adr():
    axes = {
        "CollectionStatus": {"SUCCESS", "ERROR", "PERMISSION_DENIED", "NOT_SUPPORTED"},
        "CheckStatus": {"PASS", "FAIL", "ERROR", "UNKNOWN"},
        "Applicability": {"APPLICABLE", "NOT_APPLICABLE", "UNDETERMINED"},
        "MeasureAssessment": {"COMPLIANT", "NON_COMPLIANT", "REQUIRES_REVIEW", "NOT_ASSESSED"},
        "Readiness": {"READY", "NOT_READY"},
        "DossierState": {"GENERATED", "SELF_ASSESSED", "ANALYZED", "DECLARED"},
    }
    adr = ADR.read_text(encoding="utf-8")
    for name, values in axes.items():
        assert {member.value for member in getattr(enums, name)} == values
        assert name in adr
        for value in values:
            assert value in adr
    definitions = [
        cls
        for cls in vars(enums).values()
        if isinstance(cls, type) and cls.__module__ == enums.__name__
    ]
    assert {cls.__name__ for cls in definitions} >= set(axes)
    assert len({cls.__name__ for cls in definitions}) == len(definitions)


def test_la_validacion_del_modelo_impide_pass_sin_evidence_ids(now):
    with pytest.raises(ValidationError, match="evidence_id"):
        CheckResult(
            check_id="c1", asset_id="a", status=CheckStatus.PASS, evaluated_at=now, reason="ok"
        )
    ok = CheckResult(
        check_id="c1",
        asset_id="a",
        status=CheckStatus.PASS,
        evaluated_at=now,
        reason="ok",
        evidence_ids=["ev-1"],
    )
    assert ok.status is CheckStatus.PASS


def test_pass_tecnico_sin_revision_no_cuenta_como_satisfecha_para_la_preparacion(now, future):
    measure = Measure(
        id="op.acc.1",
        applicability=Applicability.APPLICABLE,
        assessment=MeasureAssessment.COMPLIANT,
        evidence=[EvidenceRef(id="ev-1", valid_until=future)],
        review=None,
    )
    result = evaluate_readiness(
        ReadinessInput(measures=[measure], judgment_executed=True, evaluated_at=now)
    )
    assert result.readiness is Readiness.NOT_READY
    assert [b.kind for b in result.blockers] == [BlockerKind.MISSING_REVIEW]

    reviewed = measure.model_copy(
        update={
            "review": Review(
                reviewer="ana",
                role=Role.SECURITY_OFFICER,
                reviewed_at=now,
                valid_until=future,
                conclusion="ok",
            )
        }
    )
    ready = evaluate_readiness(
        ReadinessInput(measures=[reviewed], judgment_executed=True, evaluated_at=now)
    )
    assert ready.readiness is Readiness.READY


def test_el_nucleo_descubre_y_procesa_un_conector_ficticio_sin_cambios():
    registry = ConnectorRegistry()
    registry.register(FakeConnector)
    connector = registry.get("fake")()
    result = connector.collect(connector.config_model())
    assert registry.names() == ["fake"]
    assert result.collection_status is CollectionStatus.SUCCESS
    assert result.collector == "fake"
    assert FakeConfig().asset_id == result.asset_id


def test_pytest_y_ruff_pasan_sin_credenciales_en_local_y_en_ci():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = " ".join(
        str(step.get("run", ""))
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    assert "ruff check" in steps
    assert "pytest" in steps
    assert not re.search(r"secrets\.", (ROOT / ".github/workflows/ci.yml").read_text())
