"""Pre-auditoría: hallazgos propuestos, ejecución y disposición humana (ADR 0002).

Todo hallazgo es una propuesta; la decide una persona. Nunca es un dictamen.
"""

import hashlib
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from ens_evidence.models.contracts import SHA256_PATTERN, Strict

PROPOSAL_NOTICE = "propuesta; la decide una persona"


class Layer(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    JUDGMENT = "JUDGMENT"


class Classification(StrEnum):
    NC_MAYOR = "NC_MAYOR"
    NC_MENOR = "NC_MENOR"
    OBSERVACION = "OBSERVACION"
    SIN_CLASIFICAR = "SIN_CLASIFICAR"


class LayerStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    DONE = "DONE"
    ERROR = "ERROR"


class DispositionState(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


CLI_PROVIDERS = frozenset({Provider.CLAUDE_CODE, Provider.CODEX})
"""Destinos que usan la CLI y la sesión de quien ejecuta: no admiten temperatura."""


class DiscardReason(StrEnum):
    """Por qué la validación descarta un hallazgo de juicio (`preaudit/validate.py`)."""

    UNKNOWN_CRITERION = "UNKNOWN_CRITERION"
    PROPOSAL_NOT_ALLOWED = "PROPOSAL_NOT_ALLOWED"
    UNKNOWN_MEASURE = "UNKNOWN_MEASURE"
    MEASURE_NOT_APPLICABLE = "MEASURE_NOT_APPLICABLE"
    MEASURE_NOT_JUSTIFIED = "MEASURE_NOT_JUSTIFIED"
    MISSING_CITATION = "MISSING_CITATION"
    UNLINKED_REF = "UNLINKED_REF"
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
    EMPTY_REASON = "EMPTY_REASON"
    SENSITIVE_TEXT = "SENSITIVE_TEXT"
    UNDISPLAYABLE_TEXT = "UNDISPLAYABLE_TEXT"
    DUPLICATE = "DUPLICATE"


class RefKind(StrEnum):
    DOCUMENT = "DOCUMENT"
    EVIDENCE = "EVIDENCE"
    SECTION = "SECTION"


class DeterministicRule(StrEnum):
    CHECK_FAIL = "CHECK_FAIL"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_EXPIRED = "EVIDENCE_EXPIRED"
    CHECK_UNKNOWN = "CHECK_UNKNOWN"
    HASH_MISMATCH = "HASH_MISMATCH"
    DOSSIER_INTEGRITY = "DOSSIER_INTEGRITY"


# Tabla de decisión de la capa determinista (plan, «Cambios r2», punto 4).
DETERMINISTIC_PROPOSAL = {
    DeterministicRule.CHECK_FAIL: Classification.NC_MENOR,
    DeterministicRule.EVIDENCE_MISSING: Classification.SIN_CLASIFICAR,
    DeterministicRule.EVIDENCE_EXPIRED: Classification.SIN_CLASIFICAR,
    DeterministicRule.CHECK_UNKNOWN: Classification.SIN_CLASIFICAR,
    DeterministicRule.HASH_MISMATCH: Classification.SIN_CLASIFICAR,
    DeterministicRule.DOSSIER_INTEGRITY: Classification.SIN_CLASIFICAR,
}


class Ref(Strict):
    kind: RefKind
    id: str = Field(min_length=1)


def group_of(measure: str) -> str:
    """Grupo del catálogo: `op.pl.1` → `op.pl`, `org.1` → `org`."""
    return measure.rsplit(".", 1)[0]


def finding_id(criterion: str, measure: str, refs: list[Ref]) -> str:
    """Id estable: mismo criterio, medida y refs (sin importar el orden) → mismo id."""
    parts = [criterion, measure, *sorted(f"{ref.kind}:{ref.id}" for ref in refs)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


class Finding(Strict):
    id: str = Field(pattern=SHA256_PATTERN)
    layer: Layer
    proposal: Classification
    measure: str = Field(min_length=1)
    refs: list[Ref] = Field(default_factory=list)
    criterion: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    quote: str | None = None
    source: str = Field(min_length=1)
    """En juicio, la fuente del criterio (qué se compara); en la capa determinista, la de su
    clasificación."""
    classification_source: str | None = Field(default=None, min_length=1)
    """En juicio, la fuente literal de la clase propuesta (ITS §VI.2, IC-01/19 §53)."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.id != finding_id(self.criterion, self.measure, self.refs):
            raise ValueError("id must derive from criterion, measure and refs")
        if self.layer is Layer.DETERMINISTIC:
            self._check_deterministic()
        else:
            self._check_judgment()
        return self

    def _check_deterministic(self) -> None:
        try:
            rule = DeterministicRule(self.criterion)
        except ValueError:
            raise ValueError(f"unknown deterministic rule: {self.criterion}") from None
        if self.proposal is not DETERMINISTIC_PROPOSAL[rule]:
            raise ValueError(f"{rule} must be proposed as {DETERMINISTIC_PROPOSAL[rule]}")

    def _check_judgment(self) -> None:
        if self.proposal is Classification.SIN_CLASIFICAR:
            raise ValueError("a judgment finding must propose NC_MAYOR, NC_MENOR or OBSERVACION")
        if not self.quote:
            raise ValueError("a judgment finding must quote its source")
        if not self.refs:
            raise ValueError("a judgment finding must cite at least one reference")
        if self.classification_source is None:
            raise ValueError("a judgment finding must cite the source of its classification")


class Disposition(Strict):
    """La decisión sobre un hallazgo. Cubre sus apariciones hasta la ejecución `covers_run`; si
    una ejecución posterior lo vuelve a proponer, esa aparición queda abierta."""

    finding_id: str = Field(pattern=SHA256_PATTERN)
    covers_run: int = Field(ge=1)
    state: DispositionState
    by: str = Field(min_length=1)
    at: AwareDatetime
    evidence_id: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _state_fields(self) -> Self:
        resolved = self.state is DispositionState.RESOLVED
        if resolved and not self.evidence_id:
            raise ValueError("RESOLVED requires evidence_id")
        if not resolved and self.evidence_id is not None:
            raise ValueError("evidence_id is only valid in RESOLVED")
        if self.state is DispositionState.DISMISSED and not self.reason:
            raise ValueError("DISMISSED requires a reason")
        return self


UNBLOCKING = frozenset({DispositionState.RESOLVED, DispositionState.DISMISSED})
"""Solo estas disposiciones dejan de bloquear; `ACCEPTED` reconoce el hallazgo y sigue abierto."""

NEXT_STATES = {
    DispositionState.OPEN: frozenset(
        {DispositionState.ACCEPTED, DispositionState.RESOLVED, DispositionState.DISMISSED}
    ),
    DispositionState.ACCEPTED: UNBLOCKING,
    DispositionState.RESOLVED: frozenset(),
    DispositionState.DISMISSED: frozenset(),
}

BLOCKING_PROPOSALS = frozenset({Classification.NC_MAYOR, Classification.NC_MENOR})
"""Las propuestas de juicio que, sin disponer, bloquean la preparación (`OPEN_FINDING`)."""

SEVERITY = (Classification.NC_MAYOR, Classification.NC_MENOR, Classification.OBSERVACION)
"""Las clases que propone la capa de juicio, de más a menos grave."""


class DispositionRecord(Strict):
    """Un registro de la cadena de disposiciones, de solo adición (`chain.py`)."""

    seq: int = Field(ge=1)
    previous_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    recorded_at: AwareDatetime
    disposition: Disposition

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.seq == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first record has no previous_sha256")
        if self.disposition.state is DispositionState.OPEN:
            raise ValueError("a disposition decides; OPEN is the state before any")
        if self.disposition.at != self.recorded_at:
            raise ValueError("disposition.at is recorded_at")
        return self


class GroupingProposal(Strict):
    """Agrupación derivada, IC-01/19 §54. Conserva los hallazgos originales."""

    proposal: Classification
    measures: list[str] = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=2)
    justification: str = Field(min_length=1)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _follows_section_54(self) -> Self:
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("finding_ids must be unique")
        if len(set(self.measures)) != len(self.measures):
            raise ValueError("measures must be unique")
        if self.proposal is Classification.NC_MENOR:
            if len(self.measures) != 1:
                raise ValueError("NC_MENOR grouping refers to a single measure")
        elif self.proposal is Classification.NC_MAYOR:
            if len(self.measures) < 2:
                raise ValueError("NC_MAYOR grouping refers to several measures")
            if len({group_of(m) for m in self.measures}) != 1:
                raise ValueError("measures from different groups cannot be grouped")
        else:
            raise ValueError("grouping proposes NC_MENOR or NC_MAYOR")
        return self


class Consent(Strict):
    by: str = Field(min_length=1)
    at: AwareDatetime
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    destination: str = Field(min_length=1)
    shell_risk_accepted: bool = False
    """El destino conserva una herramienta de shell y quien consiente lo aceptó expresamente."""


class PreAuditRun(Strict):
    dossier: str = Field(min_length=1)
    dossier_sha256: str = Field(pattern=SHA256_PATTERN)
    pack_version: str = Field(min_length=1)
    pack_sha256: str = Field(pattern=SHA256_PATTERN)
    at: AwareDatetime
    provider: Provider | None = None
    model: str | None = Field(default=None, min_length=1)
    consent: Consent | None = None
    deterministic_status: LayerStatus = LayerStatus.DONE
    judgment_status: LayerStatus = LayerStatus.NOT_RUN
    judgment_error: str | None = Field(default=None, min_length=1)
    discards: dict[DiscardReason, Annotated[int, Field(ge=1)]] = {}
    """Los hallazgos de juicio descartados por la validación, por motivo."""

    @property
    def discarded(self) -> int:
        return sum(self.discards.values())

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.deterministic_status is not LayerStatus.DONE:
            raise ValueError("the deterministic layer always completes")
        if self.judgment_status is LayerStatus.NOT_RUN:
            if any(v is not None for v in (self.provider, self.model, self.consent)):
                raise ValueError("NOT_RUN judgment carries no provider, model or consent")
            if self.discarded:
                raise ValueError("NOT_RUN judgment has nothing to discard")
        else:
            if self.provider is None or self.model is None:
                raise ValueError("judgment requires provider and model")
            if self.consent is None:
                raise ValueError("judgment requires consent")
        if (self.judgment_error is not None) != (self.judgment_status is LayerStatus.ERROR):
            raise ValueError("judgment_error goes with, and only with, an ERROR judgment")
        if self.judgment_status is LayerStatus.ERROR and self.discarded:
            raise ValueError("an ERROR judgment keeps nothing, so it discards nothing")
        return self


class PreAuditRecord(Strict):
    """Un registro de la cadena de ejecuciones, de solo adición (`chain.py`).

    Lleva la ejecución y sus hallazgos tal como se aceptaron. Una ejecución posterior nunca lo
    sustituye ni cierra lo que propuso; la disposición humana va aparte."""

    seq: int = Field(ge=1)
    previous_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    recorded_at: AwareDatetime
    prepared: str | None = Field(default=None, min_length=1)
    """Clave de `prepared.json` al que se ligó el consentimiento; None si no se envió nada."""
    run: PreAuditRun
    findings: list[Finding] = Field(default_factory=list)
    groupings: list[GroupingProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.prepared is None) != (self.run.consent is None):
            raise ValueError("a prepared payload goes with, and only with, a consent")
        judged = [f for f in self.findings if f.layer is Layer.JUDGMENT]
        if judged and self.run.judgment_status is not LayerStatus.DONE:
            raise ValueError("only a DONE judgment layer carries judgment findings")
        if len({f.id for f in self.findings}) != len(self.findings):
            raise ValueError("finding ids must be unique")
        known = {f.id for f in self.findings}
        if any(not set(g.finding_ids) <= known for g in self.groupings):
            raise ValueError("a grouping refers to findings of this run")
        if self.run.at > self.recorded_at:
            raise ValueError("run.at is after recorded_at")
        return self
