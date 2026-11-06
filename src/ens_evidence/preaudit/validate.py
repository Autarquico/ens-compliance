"""Validación semántica de la capa de juicio (plan pre-audit, «Cambios r2», punto 9).

Recibe una respuesta que ya cumple el esquema y decide, hallazgo a hallazgo, si se acepta. Un
hallazgo se descarta, y se cuenta, si:

- su criterio no está en el pack, o su propuesta no está en las `allowed_proposals` del criterio;
- su medida no está en el catálogo o no está en el payload;
- su medida no es de las que trata el criterio (`measure_scope`): aplicable, o bien no aplicable o
  compensada (con una justificación en el payload);
- no cita lo que pide el criterio: al menos un documento y una evidencia (`applicable`), o al
  menos una justificación (`not_applicable_or_compensated`);
- cita un documento, evidencia o justificación que no está en el payload enlazado a esa medida;
- un fragmento citado no aparece literalmente en el elemento citado, o es demasiado corto para
  apoyar nada: sin contar las marcas de redacción (`[PERSONA]`, `[REDACTADO:…]`…), menos de
  `MIN_QUOTE_WORDS` palabras o de `MIN_QUOTE_CHARS` caracteres no blancos;
- no da motivo;
- su motivo o sus fragmentos contienen un secreto o un dato personal detectable, o texto que no
  se puede mostrar ni guardar tal cual (caracteres de control, o más de `MAX_REASON_CHARS` /
  `MAX_QUOTE_CHARS` caracteres);
- repite un hallazgo de esta respuesta (mismo id). Entre los dos se queda la propuesta más grave
  y el otro cuenta como duplicado.

Cada descarte se cuenta por motivo (`DiscardReason`).

Las fuentes normativas no las elige la respuesta: el hallazgo cita la del criterio (qué se
compara) y la de la clase que propone (`classify.JUDGMENT_CLASSIFICATIONS`).

Forma del payload que se lee (la construye `payload.py`; los campos que no se listan se
ignoran):

    {"dossier": {
        "measures":  [{"id": "org.1", "applicable": true,
                       "documents": ["<ref>", ...], "evidence": ["<ref>", ...]}],
        "documents": [{"ref": "<ref>", "text": "<texto redactado>"}],
        "evidence":  [{"ref": "<ref>", "text": "<texto redactado>"}],
        "justifications": [{"id": "justification:<tipo>:<medida>[:…]", "measure": "<medida>",
                            "justification": "<texto redactado>"}]}}

Una justificación citada es una ref `SECTION` con el id de la justificación.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ens_evidence.catalog.load import load_catalog
from ens_evidence.models.preaudit import (
    SEVERITY,
    DiscardReason,
    Finding,
    Layer,
    Ref,
    RefKind,
    finding_id,
)
from ens_evidence.preaudit.classify import judgment_classification_source
from ens_evidence.preaudit.pack import AuditPack, Criterion, MeasureScope
from ens_evidence.privacy_text import sensitive_problems


# Equivalente pydantic del `response.schema.json` del pack vigente (un test vigila que no derive).
class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CitedItem(_Response):
    ref: str
    quote: str


class ResponseFinding(_Response):
    criterion: str
    measure: str
    proposal: Literal["NC_MAYOR", "NC_MENOR", "OBSERVACION"]
    documents: list[CitedItem]
    evidence: list[CitedItem]
    justifications: list[CitedItem]
    reason: str


class JudgmentResponse(_Response):
    findings: list[ResponseFinding]


MIN_QUOTE_WORDS = 4
MIN_QUOTE_CHARS = 20
_PLACEHOLDER = re.compile(r"\[[^\[\]]*\]")
_WORD = re.compile(r"[^\W_]+")
MAX_REASON_CHARS = 2000
MAX_QUOTE_CHARS = 1000
# C0 salvo tabulador y salto de línea, DEL y C1: secuencias que manipularían el terminal de quien
# revisa (`preaudit show`).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


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


class PayloadJustification(_Lenient):
    id: str
    measure: str
    justification: str


class PayloadDossier(_Lenient):
    measures: list[PayloadMeasure]
    documents: list[PayloadItem] = []
    evidence: list[PayloadItem] = []
    justifications: list[PayloadJustification] = []


class PayloadView(_Lenient):
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
    pack: AuditPack, payload: dict[str, Any], response: JudgmentResponse
) -> Validation:
    try:
        dossier = PayloadView.model_validate(payload).dossier
    except ValidationError:
        raise PayloadShapeError("el payload no tiene la forma esperada") from None
    context = _Context(dossier)
    accepted: dict[str, Finding] = {}
    discards: Counter[DiscardReason] = Counter()
    for candidate in response.findings:
        verdict = _judge(pack, context, candidate)
        if isinstance(verdict, DiscardReason):
            discards[verdict] += 1
            continue
        if kept := accepted.get(verdict.id):
            discards[DiscardReason.DUPLICATE] += 1
            verdict = _graver(kept, verdict)
        accepted[verdict.id] = verdict
    return Validation(list(accepted.values()), discards)


def _graver(kept: Finding, repeated: Finding) -> Finding:
    """Entre dos propuestas del mismo hallazgo, la más grave (a igual gravedad, la primera)."""
    if SEVERITY.index(repeated.proposal) < SEVERITY.index(kept.proposal):
        return repeated
    return kept


class _Context:
    def __init__(self, dossier: PayloadDossier) -> None:
        self.measures = {m.id: m for m in dossier.measures}
        self.texts = {
            RefKind.DOCUMENT: {d.ref: d.text for d in dossier.documents},
            RefKind.EVIDENCE: {e.ref: e.text for e in dossier.evidence},
            RefKind.SECTION: {j.id: j.justification for j in dossier.justifications},
        }
        self.justified: dict[str, set[str]] = {}
        for item in dossier.justifications:
            self.justified.setdefault(item.measure, set()).add(item.id)

    def linked(self, measure: PayloadMeasure, kind: RefKind) -> set[str]:
        match kind:
            case RefKind.DOCUMENT:
                return set(measure.documents)
            case RefKind.EVIDENCE:
                return set(measure.evidence)
            case RefKind.SECTION:
                return self.justified.get(measure.id, set())


def _judge(
    pack: AuditPack, context: _Context, candidate: ResponseFinding
) -> Finding | DiscardReason:
    criterion = pack.criterion(candidate.criterion)
    if criterion is None:
        return DiscardReason.UNKNOWN_CRITERION
    if candidate.proposal not in criterion.allowed_proposals:
        return DiscardReason.PROPOSAL_NOT_ALLOWED
    measure = context.measures.get(candidate.measure)
    if measure is None or load_catalog().get(candidate.measure) is None:
        return DiscardReason.UNKNOWN_MEASURE
    if problem := _scope_problem(criterion.measure_scope, context, measure, candidate):
        return problem
    cited = (
        [(RefKind.DOCUMENT, d) for d in candidate.documents]
        + [(RefKind.EVIDENCE, e) for e in candidate.evidence]
        + [(RefKind.SECTION, j) for j in candidate.justifications]
    )
    if problem := _citation_problem(context, measure, cited):
        return problem
    if not candidate.reason.strip():
        return DiscardReason.EMPTY_REASON
    quotes = [item.quote for _, item in cited]
    if not _displayable(candidate.reason, quotes):
        return DiscardReason.UNDISPLAYABLE_TEXT
    if _sensitive(candidate.reason, *quotes):
        return DiscardReason.SENSITIVE_TEXT
    return _finding(criterion, candidate, cited)


def _scope_problem(
    scope: MeasureScope, context: _Context, measure: PayloadMeasure, candidate: ResponseFinding
) -> DiscardReason | None:
    """La medida es de las que trata el criterio y se cita lo que el criterio pide."""
    if scope is MeasureScope.APPLICABLE:
        if not measure.applicable:
            return DiscardReason.MEASURE_NOT_APPLICABLE
        if not candidate.documents or not candidate.evidence:
            return DiscardReason.MISSING_CITATION
        if candidate.justifications:
            return DiscardReason.UNLINKED_REF
        return None
    if measure.id not in context.justified:
        return DiscardReason.MEASURE_NOT_JUSTIFIED
    if not candidate.justifications:
        return DiscardReason.MISSING_CITATION
    return None


def _citation_problem(
    context: _Context, measure: PayloadMeasure, cited: list[tuple[RefKind, CitedItem]]
) -> DiscardReason | None:
    for kind, item in cited:
        text = context.texts[kind].get(item.ref)
        if text is None or item.ref not in context.linked(measure, kind):
            return DiscardReason.UNLINKED_REF
        if not _substantive(item.quote) or item.quote not in text:
            return DiscardReason.QUOTE_NOT_FOUND
    return None


def _substantive(quote: str) -> bool:
    """Lo bastante largo para apoyar un hallazgo una vez quitadas las marcas de redacción."""
    content = _PLACEHOLDER.sub(" ", quote)
    words = _WORD.findall(content)
    return len(words) >= MIN_QUOTE_WORDS and len("".join(content.split())) >= MIN_QUOTE_CHARS


def _displayable(reason: str, quotes: list[str]) -> bool:
    return (
        len(reason) <= MAX_REASON_CHARS
        and all(len(quote) <= MAX_QUOTE_CHARS for quote in quotes)
        and not any(_CONTROL.search(text) for text in (reason, *quotes))
    )


def _sensitive(*texts: str) -> bool:
    return any(sensitive_problems(text) for text in texts)


def _finding(
    criterion: Criterion, candidate: ResponseFinding, cited: list[tuple[RefKind, CitedItem]]
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
        classification_source=judgment_classification_source(candidate.proposal),
    )
