"""Export de JSON Schema de los contratos."""

import json
from pathlib import Path

from pydantic import BaseModel

from ens_evidence.models import (
    CheckResult,
    CollectionResult,
    Dossier,
    EvidenceManifest,
    ReadinessInput,
    ReadinessResult,
    Review,
)
from ens_evidence.scope import (
    Approval,
    AssetsFile,
    DependenciesFile,
    ObservedHosts,
    ServicesFile,
    SystemScope,
)

CONTRACTS: dict[str, type[BaseModel]] = {
    "check-result": CheckResult,
    "collection-result": CollectionResult,
    "dossier": Dossier,
    "evidence-manifest": EvidenceManifest,
    "readiness-input": ReadinessInput,
    "readiness-result": ReadinessResult,
    "review": Review,
    "scope-approval": Approval,
    "scope-assets": AssetsFile,
    "scope-dependencies": DependenciesFile,
    "scope-services": ServicesFile,
    "scope-system": SystemScope,
    "observed-hosts": ObservedHosts,
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
