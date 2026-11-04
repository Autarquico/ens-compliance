"""Validación semántica de la capa de juicio (plan pre-audit, «Cambios r2», punto 9).

Recibe una respuesta que ya cumple el esquema y decide, hallazgo a hallazgo, si se acepta. Un
hallazgo se descarta, y se cuenta, si:

- su criterio no está en el pack, o su propuesta no está en las `allowed_proposals` del criterio;
- su medida no está en el catálogo, no está en el payload o no es aplicable;
- no cita al menos un documento y al menos una evidencia;
- cita un documento o evidencia que no está en el payload enlazado a esa medida;
- un fragmento citado no aparece literalmente en el elemento citado;
- no da motivo, o repite un hallazgo ya aceptado en esta respuesta;
- su motivo o sus fragmentos contienen un secreto o un dato personal detectable.

La fuente normativa del hallazgo es siempre la del criterio: la respuesta no la elige.

Forma del payload que se lee (la construye `payload.py`; los campos que no se listan se
ignoran):

    {"dossier": {
        "measures":  [{"id": "org.1", "applicable": true,
                       "documents": ["<ref>", ...], "evidence": ["<ref>", ...]}],
        "documents": [{"ref": "<ref>", "text": "<texto redactado>"}],
        "evidence":  [{"ref": "<ref>", "text": "<texto redactado>"}]}}
"""

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ens_evidence.catalog.load import load_catalog
from ens_evidence.models.preaudit import Finding, Layer, Ref, RefKind, finding_id
from ens_evidence.preaudit.pack import AuditPack, Criterion
from ens_evidence.privacy_text import text_problems
from ens_evidence.scope.secrets import find_secrets

if TYPE_CHECKING:
    from ens_evidence.preaudit.adapter import CitedItem, JudgmentResponse, ResponseFinding


class DiscardReason(StrEnum):
    UNKNOWN_CRITERION = "UNKNOWN_CRITERION"
    PROPOSAL_NOT_ALLOWED = "PROPOSAL_NOT_ALLOWED"
    UNKNOWN_MEASURE = "UNKNOWN_MEASURE"
    MEASURE_NOT_APPLICABLE = "MEASURE_NOT_APPLICABLE"
    MISSING_CITATION = "MISSING_CITATION"
    UNLINKED_REF = "UNLINKED_REF"
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
    EMPTY_REASON = "EMPTY_REASON"
    SENSITIVE_TEXT = "SENSITIVE_TEXT"
    DUPLICATE = "DUPLICATE"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PayloadMeasure(_Lenient):
    id: str
    applicable: bool
    documents: list[str] = []
    evidence: list[str] = []


class PayloadItem(_Lenient):
    ref: str
    text: str


class PayloadDossier(_Lenient):
    measures: list[PayloadMeasure]
    documents: list[PayloadItem] = []
    evidence: list[PayloadItem] = []


class Payload(_Lenient):
    dossier: PayloadDossier


@dataclass(frozen=True)
class Validation:
    findings: list[Finding]
    discards: Counter[DiscardReason] = field(default_factory=Counter)

    @property
    def discarded(self) -> int:
        return sum(self.discards.values())


class PayloadShapeError(ValueError):
    """El payload no tiene la forma que se valida; es un fallo nuestro, no del proveedor."""


def validate_findings(
    pack: AuditPack, payload: dict[str, Any], response: "JudgmentResponse"
) -> Validation:
    try:
        dossier = Payload.model_validate(payload).dossier
    except ValidationError:
        raise PayloadShapeError("el payload no tiene la forma esperada") from None
    context = _Context(dossier)
    result = Validation(findings=[])
    for candidate in response.findings:
        verdict = _judge(pack, context, candidate)
        if isinstance(verdict, Finding) and verdict.id in {f.id for f in result.findings}:
            verdict = DiscardReason.DUPLICATE
        if isinstance(verdict, DiscardReason):
            result.discards[verdict] += 1
        else:
            result.findings.append(verdict)
    return result


class _Context:
    def __init__(self, dossier: PayloadDossier) -> None:
        self.measures = {m.id: m for m in dossier.measures}
        self.texts = {
            RefKind.DOCUMENT: {d.ref: d.text for d in dossier.documents},
            RefKind.EVIDENCE: {e.ref: e.text for e in dossier.evidence},
        }

    def linked(self, measure: PayloadMeasure, kind: RefKind) -> set[str]:
        return set(measure.documents if kind is RefKind.DOCUMENT else measure.evidence)


def _judge(
    pack: AuditPack, context: _Context, candidate: "ResponseFinding"
) -> Finding | DiscardReason:
    criterion = pack.criterion(candidate.criterion)
    if criterion is None:
        return DiscardReason.UNKNOWN_CRITERION
    if candidate.proposal not in criterion.allowed_proposals:
        return DiscardReason.PROPOSAL_NOT_ALLOWED
    measure = context.measures.get(candidate.measure)
    if measure is None or load_catalog().get(candidate.measure) is None:
        return DiscardReason.UNKNOWN_MEASURE
    if not measure.applicable:
        return DiscardReason.MEASURE_NOT_APPLICABLE
    if not candidate.documents or not candidate.evidence:
        return DiscardReason.MISSING_CITATION
    cited = [(RefKind.DOCUMENT, d) for d in candidate.documents] + [
        (RefKind.EVIDENCE, e) for e in candidate.evidence
    ]
    if problem := _citation_problem(context, measure, cited):
        return problem
    if not candidate.reason.strip():
        return DiscardReason.EMPTY_REASON
    if _sensitive(candidate.reason, *(item.quote for _, item in cited)):
        return DiscardReason.SENSITIVE_TEXT
    return _finding(criterion, candidate, cited)


def _citation_problem(
    context: _Context, measure: PayloadMeasure, cited: list[tuple[RefKind, "CitedItem"]]
) -> DiscardReason | None:
    for kind, item in cited:
        text = context.texts[kind].get(item.ref)
        if text is None or item.ref not in context.linked(measure, kind):
            return DiscardReason.UNLINKED_REF
        if not item.quote.strip() or item.quote not in text:
            return DiscardReason.QUOTE_NOT_FOUND
    return None


def _sensitive(*texts: str) -> bool:
    for text in texts:
        errors, warnings = text_problems(text)
        if errors or warnings or find_secrets(text):
            return True
    return False


def _finding(
    criterion: Criterion, candidate: "ResponseFinding", cited: list[tuple[RefKind, "CitedItem"]]
) -> Finding:
    refs = [Ref(kind=kind, id=item.ref) for kind, item in cited]
    return Finding(
        id=finding_id(criterion.id, candidate.measure, refs),
        layer=Layer.JUDGMENT,
        proposal=candidate.proposal,
        measure=candidate.measure,
        refs=refs,
        criterion=criterion.id,
        reason=candidate.reason,
        quote="\n".join(f"{item.ref}: «{item.quote}»" for _, item in cited),
        source=f"{criterion.source.reference}: «{criterion.source.quote}»",
    )
