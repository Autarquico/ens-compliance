"""Export de JSON Schema de los contratos."""

import json
from pathlib import Path

from pydantic import BaseModel

from ens_evidence.applicability import Declaration
from ens_evidence.backup.records import BackupRun, ImmutabilityCheck, IntegrityCheck, RestoreRecord
from ens_evidence.backup.restore_test import RestoreTest, RestoreTestDeclaration
from ens_evidence.catalog import MeasureCard
from ens_evidence.catalog.controls import MeasuresFile
from ens_evidence.categorization import CategorizationApproval, CategorizationFile
from ens_evidence.matrix import ApplicabilityReview, MatrixFile, MeasureReviewRecord
from ens_evidence.models import (
    CheckResult,
    CollectionResult,
    Dossier,
    EvidenceManifest,
    ReadinessInput,
    ReadinessResult,
    Review,
)
from ens_evidence.orgdocs.approval import DocumentApproval
from ens_evidence.orgdocs.meta import DocumentMeta
from ens_evidence.risk import Methodology, RegisterFile, RiskApproval, TreatmentPlanFile
from ens_evidence.scope import (
    Approval,
    AssetsFile,
    DependenciesFile,
    ObservedHosts,
    ServicesFile,
    SystemScope,
)

CONTRACTS: dict[str, type[BaseModel]] = {
    "applicability-declaration": Declaration,
    "applicability-review": ApplicabilityReview,
    "backup-run": BackupRun,
    "backup-check": IntegrityCheck,
    "backup-restore": RestoreRecord,
    "backup-restore-test": RestoreTest,
    "backup-restore-test-declaration": RestoreTestDeclaration,
    "immutability-check": ImmutabilityCheck,
    "catalog-measure": MeasureCard,
    "categorization": CategorizationFile,
    "categorization-approval": CategorizationApproval,
    "check-result": CheckResult,
    "collection-result": CollectionResult,
    "compliance-matrix": MatrixFile,
    "controls-measures": MeasuresFile,
    "dossier": Dossier,
    "evidence-manifest": EvidenceManifest,
    "measure-review": MeasureReviewRecord,
    "readiness-input": ReadinessInput,
    "readiness-result": ReadinessResult,
    "review": Review,
    "risk-approval": RiskApproval,
    "risk-methodology": Methodology,
    "risk-register": RegisterFile,
    "risk-treatment-plan": TreatmentPlanFile,
    "scope-approval": Approval,
    "scope-assets": AssetsFile,
    "scope-dependencies": DependenciesFile,
    "scope-services": ServicesFile,
    "scope-system": SystemScope,
    "observed-hosts": ObservedHosts,
    "org-document-meta": DocumentMeta,
    "org-document-approval": DocumentApproval,
}


def render() -> dict[str, str]:
    return {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), indent=2, sort_keys=True)
        + "\n"
        for name, model in CONTRACTS.items()
    }


def export(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for filename, content in render().items():
        (directory / filename).write_text(content, encoding="utf-8")


def drift(directory: Path) -> list[str]:
    """Ficheros ausentes, distintos o sobrantes respecto a los modelos."""
    expected = render()
    found = {path.name for path in directory.glob("*.schema.json")} if directory.is_dir() else set()
    problems = [
        f"{filename}: missing or outdated"
        for filename, content in expected.items()
        if filename not in found or (directory / filename).read_text(encoding="utf-8") != content
    ]
    problems += [f"{filename}: unexpected" for filename in sorted(found - expected.keys())]
    return problems
