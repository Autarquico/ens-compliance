"""Modelo del incidente (docs/ens-compliance-guide.md:867-890) y de sus registros de solo adición.

Un `IncidentRecord` es una fotografía completa del incidente (`IncidentState`) en un instante.
`IncidentStatus` es ciclo de vida; nunca es un eje de evaluación (ADR 0002).
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, model_validator

from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.orgdocs.answers import is_filler

SCHEMA_VERSION = "1.0"
INCIDENT_ID_PATTERN = r"^(INC|SIM)-\d{4}-\d{3}$"
OPAQUE_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
EVIDENCE_ID_PATTERN = r"^EV-\d{8}-[0-9a-f]{8}$"
APPROVAL_KEY_PATTERN = r"^[A-Za-z0-9._/:-]{1,200}$"

TITLE_MAX = 120
LONG_TEXT_MAX = 2000
ACTION_MAX = 500
LIST_MAX = 100


class IncidentStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class RecordKind(StrEnum):
    """Reporte inicial, intermedio y final (op.exp.9.1)."""

    OPENED = "opened"
    UPDATED = "updated"
    CLOSED = "closed"


class ActionKind(StrEnum):
    """Vocabulario del producto, no del RD 311/2022: el real no clasifica las actuaciones."""

    EMERGENCY = "emergency"
    SYSTEM_CHANGE = "system_change"
    NOTIFICATION = "notification"
    OTHER = "other"


class TriState(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ReviewResult(StrEnum):
    CHANGED = "changed"
    NO_CHANGE = "no_change"


def _concrete(text: str) -> str:
    """Un texto obligatorio no puede ser vacío ni de relleno (criterio de orgdocs/answers.py)."""
    if is_filler(text):
        raise ValueError("debe ser concreto, no vacío ni de relleno")
    return text


Opaque = Annotated[str, Field(pattern=OPAQUE_ID_PATTERN)]
EvidenceId = Annotated[str, Field(pattern=EVIDENCE_ID_PATTERN)]
Concrete = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=ACTION_MAX), AfterValidator(_concrete)
]


class IncidentSource(Strict):
    system: Annotated[str, Field(min_length=1, max_length=64)]
    alert_ids: list[Opaque] = Field(default_factory=list, max_length=LIST_MAX)


class Analysis(Strict):
    summary: Annotated[str, Field(max_length=LONG_TEXT_MAX)] | None = None
    recommendations: list[Annotated[str, Field(max_length=ACTION_MAX)]] = Field(
        default_factory=list, max_length=LIST_MAX
    )


class Action(Strict):
    kind: ActionKind
    at: AwareDatetime
    description: Annotated[str, Field(min_length=1, max_length=ACTION_MAX)]


class Procedure(Strict):
    """Procedimiento vigente al dar de alta el incidente, verificable contra org-docs."""

    name: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)]
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    approved_at: AwareDatetime
    approval_key: str = Field(pattern=APPROVAL_KEY_PATTERN)


class AuditableEventsReview(Strict):
    """op.exp.9.3: revisión de los eventos auditables tras el análisis."""

    result: ReviewResult
    detail: Concrete  # qué cambió (`changed`) o por qué no cambia nada (`no_change`)


class Closure(Strict):
    closed_at: AwareDatetime
    conclusion: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=LONG_TEXT_MAX),
        AfterValidator(_concrete),
    ]
    legal_relevance: TriState
    legal_advice: Concrete | None = None  # quién da el asesoramiento legal (op.exp.9.2)
    preserved_evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=LIST_MAX)
    auditable_events_review: AuditableEventsReview

    @model_validator(mode="after")
    def _legal_evidence(self) -> Self:
        if self.legal_relevance is TriState.YES:
            if self.legal_advice is None:
                raise ValueError(
                    "con relevancia jurisdiccional hay que indicar el asesoramiento legal"
                )
            if not self.preserved_evidence_ids:
                raise ValueError(
                    "con relevancia jurisdiccional hay que citar las evidencias preservadas"
                )
        return self


class IncidentState(Strict):
    id: str = Field(pattern=INCIDENT_ID_PATTERN)
    simulated: bool = False
    title: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)]
    status: IncidentStatus = IncidentStatus.OPEN
    severity: str | None = Field(default=None, min_length=1, max_length=64)
    detected_at: AwareDatetime
    assets: list[Opaque] = Field(default_factory=list, max_length=LIST_MAX)
    source: IncidentSource | None = None
    personal_data: TriState = TriState.UNKNOWN
    analysis: Analysis = Field(default_factory=Analysis)
    actions: list[Action] = Field(default_factory=list, max_length=LIST_MAX)
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=LIST_MAX)
    procedure: Procedure | None = None
    closure: Closure | None = None

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.id.startswith("SIM-") != self.simulated:
            raise ValueError("el prefijo del id (SIM-/INC-) debe coincidir con `simulated`")
        if self.simulated and self.procedure is None:
            raise ValueError("un simulacro prueba el procedimiento: exige `procedure`")
        if (self.status is IncidentStatus.CLOSED) != (self.closure is not None):
            raise ValueError("`closure` existe si y solo si el estado es closed")
        if self.closure is not None:
            if self.personal_data is TriState.UNKNOWN:
                raise ValueError(
                    "al cerrar, `personal_data` debe ser yes o no (¿afecta a datos personales?). "
                    f"Indícalo antes: incident update {self.id} --personal-data yes|no"
                )
            if self.closure.closed_at < self.detected_at:
                raise ValueError("closed_at no puede ser anterior a detected_at")
        return self


class IncidentRecord(Strict):
    schema_version: str = SCHEMA_VERSION
    seq: int = Field(ge=1)
    kind: RecordKind
    recorded_at: AwareDatetime
    previous_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    state: IncidentState

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        state = self.state
        if (self.seq == 1) != (self.kind is RecordKind.OPENED):
            raise ValueError("solo el primer registro es `opened`")
        if (self.seq == 1) != (self.previous_sha256 is None):
            raise ValueError("`previous_sha256` falta solo en el primer registro")
        if (self.kind is RecordKind.CLOSED) != (state.status is IncidentStatus.CLOSED):
            raise ValueError("el registro `closed` y el estado closed van juntos")
        if state.detected_at > self.recorded_at:
            raise ValueError("detected_at no puede ser posterior a recorded_at")
        if any(action.at > self.recorded_at for action in state.actions):
            raise ValueError("una actuación no puede ser posterior a recorded_at")
        if state.closure is not None and state.closure.closed_at > self.recorded_at:
            raise ValueError("closed_at no puede ser posterior a recorded_at")
        return self


def check_successor(previous: IncidentRecord, record: IncidentRecord) -> list[str]:
    """Qué no puede cambiar entre dos registros consecutivos del mismo incidente."""
    errors = []
    before, after = previous.state, record.state
    if record.seq != previous.seq + 1:
        errors.append("la secuencia debe ser contigua")
    if record.recorded_at < previous.recorded_at:
        errors.append("recorded_at no puede decrecer")
    if previous.kind is RecordKind.CLOSED:
        errors.append("un incidente cerrado no admite más registros")
    for name in ("id", "simulated", "detected_at", "procedure"):
        if getattr(before, name) != getattr(after, name):
            errors.append(f"{name} es inmutable")
    return errors
