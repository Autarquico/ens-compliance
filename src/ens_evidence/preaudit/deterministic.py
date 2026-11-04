"""Capa determinista: hallazgos a partir de la instantánea del expediente.

Reutiliza lo que el expediente ya calculó en la sección 7 (bloqueos de la matriz); no recalcula
la matriz. Sin red y sin escribir. Cada hallazgo cita su fuente normativa y es una propuesta.
"""

import hashlib
from collections.abc import Iterable, Sequence

from ens_evidence.assessment.dossier.model import RESULTS_SECTION, DossierSnapshot, snapshot_export
from ens_evidence.evidence.layout import is_evidence_id
from ens_evidence.models.preaudit import (
    PROPOSAL_NOTICE,
    DeterministicRule,
    Finding,
    Layer,
    Ref,
    RefKind,
    finding_id,
)
from ens_evidence.models.readiness import Blocker, BlockerKind
from ens_evidence.preaudit.classify import proposal_for, source_for

DOSSIER_SCOPE = "expediente"
"""«Medida» de los hallazgos que no cuelgan de una medida del catálogo (integridad)."""

_HASH_DETAILS = frozenset(
    {
        "check_evidence_changed",
        "check_evidence_unverified",
        "check_evidence_inconsistent",
        "evidence_unverified",
        "evidence_inconsistent",
    }
)

_REASONS = {
    DeterministicRule.CHECK_FAIL: "un check ha fallado en una medida aplicable",
    DeterministicRule.EVIDENCE_MISSING: "falta evidencia que sustente la medida",
    DeterministicRule.EVIDENCE_EXPIRED: "la evidencia que sustenta la medida está caducada",
    DeterministicRule.CHECK_UNKNOWN: "un check no pudo valorar la medida",
    DeterministicRule.HASH_MISMATCH: "la huella de la evidencia no cuadra con lo registrado",
    DeterministicRule.DOSSIER_INTEGRITY: "el expediente o su matriz no se pueden leer o verificar",
}


def deterministic_findings(
    snapshot: DossierSnapshot, *, integrity: Sequence[str] = ()
) -> list[Finding]:
    """Los hallazgos deterministas de `snapshot`.

    `integrity`: problemas de integridad que ya informa `dossier_status` (cadena, derivados).
    """
    try:
        export = snapshot_export(snapshot)
    except (ValueError, KeyError):
        problems = [*integrity, "la sección de resultados no se puede leer"]
        return _unique(_integrity_finding(p) for p in problems)
    found = [f for b in export.blockers if (f := _from_blocker(b)) is not None]
    found += [_integrity_finding(p) for p in (*integrity, *export.errors)]
    return _unique(found)


def _unique(findings: Iterable[Finding]) -> list[Finding]:
    return list({f.id: f for f in findings}.values())


def _rule_of(blocker: Blocker) -> DeterministicRule | None:
    tag = blocker.detail.split(":", 1)[0]
    match blocker.kind:
        case BlockerKind.NOT_COMPLIANT if tag == "check_failed":
            return DeterministicRule.CHECK_FAIL
        case BlockerKind.EXPIRED_EVIDENCE:
            return DeterministicRule.EVIDENCE_EXPIRED
        case BlockerKind.MISSING_EVIDENCE if tag in _HASH_DETAILS:
            return DeterministicRule.HASH_MISMATCH
        case BlockerKind.MISSING_EVIDENCE if tag == "check_unknown_subject":
            return DeterministicRule.CHECK_UNKNOWN
        case BlockerKind.MISSING_EVIDENCE:
            return DeterministicRule.EVIDENCE_MISSING
    return None  # fuera de la tabla de decisión: no se inventa una severidad


def _from_blocker(blocker: Blocker) -> Finding | None:
    rule = _rule_of(blocker)
    if rule is None:
        return None
    section = f"{RESULTS_SECTION}:{blocker.detail}" if blocker.detail else str(RESULTS_SECTION)
    refs = [Ref(kind=RefKind.SECTION, id=section)]
    refs += [
        Ref(kind=RefKind.EVIDENCE, id=part)
        for part in blocker.detail.split(":")
        if is_evidence_id(part)
    ]
    detail = f" ({blocker.detail})" if blocker.detail else ""
    return _finding(rule, blocker.measure_id or DOSSIER_SCOPE, refs, f"{_REASONS[rule]}{detail}")


def _integrity_finding(problem: str) -> Finding:
    digest = hashlib.sha256(problem.encode()).hexdigest()[:12]
    refs = [Ref(kind=RefKind.SECTION, id=f"integridad:{digest}")]
    rule = DeterministicRule.DOSSIER_INTEGRITY
    return _finding(rule, DOSSIER_SCOPE, refs, f"{_REASONS[rule]}: {problem}")


def _finding(rule: DeterministicRule, measure: str, refs: list[Ref], reason: str) -> Finding:
    return Finding(
        id=finding_id(rule, measure, refs),
        layer=Layer.DETERMINISTIC,
        proposal=proposal_for(rule),
        measure=measure,
        refs=refs,
        criterion=rule,
        reason=f"{reason}; {PROPOSAL_NOTICE}",
        source=source_for(rule),
    )
