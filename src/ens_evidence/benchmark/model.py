"""Contratos del banco de defectos sembrados: perfiles, casos, grabaciones, resultados y línea
base. El banco mide la pre-auditoría; nunca declara conformidad."""

import re
from collections import Counter
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from ens_evidence.catalog.model import Dimension, SecurityLevel
from ens_evidence.matrix.model import COVERS_PATTERN, MAX_REVIEW_EVERY_DAYS, MeasureId, Sha256
from ens_evidence.models import MeasureAssessment, Role
from ens_evidence.models.contracts import Strict
from ens_evidence.models.preaudit import DeterministicRule, Provider
from ens_evidence.preaudit.adapter import ProviderErrorKind

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
EXCLUDED_RULES = frozenset({DeterministicRule.DOSSIER_INTEGRITY})
"""Reglas que el banco no siembra: la integridad del expediente no tiene texto del anexo II que
citar y `prepare` rechaza el expediente (plan, D3)."""
TEMPERATURE_NOTE = (
    "no aplica: JudgmentConfig no expone temperatura y los clientes no la envían; "
    "rige la del proveedor"
)
Notice = Literal[
    "Cobertura del banco, no garantía universal. Superar el banco no aprueba un expediente real."
]
NOT_A_VERDICT: Notice = (
    "Cobertura del banco, no garantía universal. Superar el banco no aprueba un expediente real."
)

Slug = Annotated[str, Field(pattern=SLUG_PATTERN)]
Covers = Annotated[list[Annotated[str, Field(pattern=COVERS_PATTERN)]], Field(min_length=1)]
_ABSOLUTE_TOKEN = re.compile(r"(?:^|\s)(?:/|~|[A-Za-z]:[\\/]|\\\\)")


def _relative(path: str) -> str:
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or "\\" in path or ".." in parts:
        raise ValueError(f"ruta no relativa o con «..»: {path}")
    return path


RelativePath = Annotated[str, AfterValidator(_relative)]


def _repeated(values: list[str]) -> list[str]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


# --- perfil ----------------------------------------------------------------------------------


class Clock(Strict):
    start: AwareDatetime


class Person(Strict):
    id: Slug
    name: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)


class ApplicabilityDecision(Strict):
    """Decisión que difiere de la propuesta de la herramienta, con su justificación."""

    measure: MeasureId
    applicable: bool
    justification: str = Field(min_length=1)


class OrgDocument(Strict):
    ref: Slug
    approved_by: Slug


class EvidenceLink(Strict):
    measure: MeasureId
    covers: Covers
    check: str | None = Field(
        default=None,
        min_length=1,
        description="En una recogida, el check que la valora: la recogida cuenta por su check.",
    )


class EvidenceSource(StrEnum):
    MANUAL = "manual"
    COLLECTED = "collected"


class ProfileEvidence(Strict):
    alias: Slug
    file: RelativePath
    source: EvidenceSource
    asset: Slug | None = Field(
        default=None,
        description="Activo del alcance de una evidencia manual; la recogida lo trae en su JSON.",
    )
    valid_until: AwareDatetime | None = None
    links: list[EvidenceLink] = Field(min_length=1)


class IncidentDrill(Strict):
    """Un simulacro de incidente (`incident new --simulated`, una actuación de emergencia y el
    cierre). Su historial es la evidencia que admite `op.exp.9`."""

    alias: Slug
    by: Slug
    title: str = Field(min_length=1)
    action: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    auditable_events_detail: str = Field(min_length=1)
    links: list[EvidenceLink] = Field(min_length=1)


class DocumentLink(Strict):
    measure: MeasureId
    document: Slug
    covers: Covers


class MatrixRow(Strict):
    measure: MeasureId
    owner: Slug
    review_every_days: int = Field(ge=1, le=MAX_REVIEW_EVERY_DAYS)
    reviewed_by: Slug
    assessment: MeasureAssessment
    conclusion: str | None = Field(default=None, min_length=1)


class MatrixDefaults(Strict):
    """Responsable, periodicidad y revisión de las medidas sin fila propia en `matrix`. Una
    medida aplicable sin fila se revisa como `COMPLIANT`; una no aplicable confirma la decisión."""

    owner: Slug
    review_every_days: int = Field(ge=1, le=MAX_REVIEW_EVERY_DAYS)
    reviewed_by: Slug
    conclusion: str = Field(min_length=1)


class Profile(Strict):
    """Pyme ficticia completa y correcta: con la capa determinista, cero hallazgos."""

    format: Literal["ens-benchmark-profile/1"]
    fictional: Literal[True]
    id: Slug
    title: str = Field(min_length=1)
    clock: Clock
    categorization: dict[Dimension, SecurityLevel] = Field(min_length=1)
    people: list[Person] = Field(min_length=1)
    applicability: list[ApplicabilityDecision] = Field(default_factory=list)
    reinforcements: dict[MeasureId, Annotated[list[str], Field(min_length=1)]] = Field(
        default_factory=dict,
        description="Refuerzo elegido de cada grupo alternativo de las medidas aplicables.",
    )
    not_required_justification: str | None = Field(
        default=None,
        min_length=1,
        description="Justificación de no aplicar lo no exigido por el anexo II y no decidido aquí.",
    )
    documents: list[OrgDocument] = Field(default_factory=list)
    evidence: list[ProfileEvidence] = Field(default_factory=list)
    incident_drill: IncidentDrill | None = None
    document_links: list[DocumentLink] = Field(default_factory=list)
    matrix: list[MatrixRow] = Field(default_factory=list)
    matrix_defaults: MatrixDefaults | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for kind, ids in (
            ("persona", [p.id for p in self.people]),
            ("documento", [d.ref for d in self.documents]),
            ("evidencia", [e.alias for e in self.evidence] + self._drill_alias()),
            ("decisión de aplicabilidad", [a.measure for a in self.applicability]),
            ("fila de la matriz", [m.measure for m in self.matrix]),
        ):
            if repeated := _repeated(ids):
                raise ValueError(f"{kind} repetida: {', '.join(repeated)}")
        people = {p.id for p in self.people}
        named = [d.approved_by for d in self.documents]
        named += [self.incident_drill.by] if self.incident_drill else []
        rows = [*self.matrix, *([self.matrix_defaults] if self.matrix_defaults else [])]
        named += [n for m in rows for n in (m.owner, m.reviewed_by)]
        if unknown := sorted(set(named) - people):
            raise ValueError(f"personas no declaradas: {', '.join(unknown)}")
        documents = {d.ref for d in self.documents}
        if unknown := sorted({link.document for link in self.document_links} - documents):
            raise ValueError(f"documentos no declarados: {', '.join(unknown)}")
        return self

    def _drill_alias(self) -> list[str]:
        return [self.incident_drill.alias] if self.incident_drill else []

    def measures(self) -> set[str]:
        drill = self.incident_drill.links if self.incident_drill else []
        return {
            *(link.measure for link in drill),
            *(a.measure for a in self.applicability),
            *self.reinforcements,
            *(link.measure for e in self.evidence for link in e.links),
            *(link.measure for link in self.document_links),
            *(m.measure for m in self.matrix),
        }


# --- caso ------------------------------------------------------------------------------------


class Partition(StrEnum):
    DEV = "dev"
    HOLDOUT = "holdout"


class CaseKind(StrEnum):
    DEFECTIVE = "defective"
    CORRECT = "correct"


class DefectLayer(StrEnum):
    DETERMINISTIC = "deterministic"
    JUDGMENT = "judgment"


class TargetKind(StrEnum):
    EVIDENCE = "evidence"
    DOCUMENT = "document"
    JUSTIFICATION = "justification"
    NONE = "none"


class Target(Strict):
    """Dónde está el defecto: `alias` de una evidencia del perfil o `ref` de un documento. La
    justificación es la de la medida del defecto; `none`, una ausencia sin objeto que citar."""

    kind: TargetKind
    alias: Slug | None = None
    ref: Slug | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        wants = {TargetKind.EVIDENCE: "alias", TargetKind.DOCUMENT: "ref"}.get(self.kind)
        given = [name for name in ("alias", "ref") if getattr(self, name) is not None]
        if given != ([wants] if wants else []):
            expected = f"solo `{wants}`" if wants else "ni `alias` ni `ref`"
            raise ValueError(f"un objetivo `{self.kind}` lleva {expected}")
        return self

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.alias or self.ref or ''}"


class Requirement(Strict):
    """Requisito del anexo II que el defecto incumple: `quote` es literal del texto de `clause`
    en el catálogo empaquetado (solo se normalizan los espacios)."""

    measure: MeasureId
    clause: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Defect(Strict):
    id: Slug
    layer: DefectLayer
    measure: MeasureId
    target: Target
    expected_rule: DeterministicRule | None = None
    expected_criterion: Slug | None = None
    requirement: Requirement
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.layer is DefectLayer.DETERMINISTIC:
            if self.expected_rule is None or self.expected_criterion is not None:
                raise ValueError("un defecto determinista lleva `expected_rule` y no criterio")
            if self.expected_rule in EXCLUDED_RULES:
                raise ValueError(f"{self.expected_rule} queda fuera del banco")
        elif self.expected_criterion is None or self.expected_rule is not None:
            raise ValueError("un defecto de juicio lleva `expected_criterion` y no regla")
        if self.requirement.measure != self.measure:
            raise ValueError("el requisito citado es de otra medida")
        return self


class _Mutation(Strict):
    pass


class ReplaceEvidenceText(_Mutation):
    op: Literal["replace_evidence_text"]
    alias: Slug
    file: RelativePath


class ExpireEvidence(_Mutation):
    op: Literal["expire_evidence"]
    alias: Slug
    valid_until: AwareDatetime


class UnlinkEvidence(_Mutation):
    op: Literal["unlink_evidence"]
    alias: Slug
    measure: MeasureId


class UnlinkDocument(_Mutation):
    op: Literal["unlink_document"]
    ref: Slug
    measure: MeasureId


AnswerValue = str | int | bool | list[str] | None


class SetAnswer(_Mutation):
    op: Literal["set_answer"]
    key: str = Field(min_length=1, description="Ruta con puntos dentro de `org/answers.yaml`.")
    value: AnswerValue


class DeclareNotApplicable(_Mutation):
    op: Literal["declare_not_applicable"]
    measure: MeasureId
    justification: str = Field(min_length=1)


class Compensate(_Mutation):
    op: Literal["compensate"]
    measure: MeasureId
    measures: list[str] = Field(min_length=1)
    replaces: list[str] = Field(min_length=1)


class CollectedOverride(_Mutation):
    op: Literal["collected_override"]
    alias: Slug
    file: RelativePath


class TamperArtifact(_Mutation):
    op: Literal["tamper_artifact"]
    alias: Slug


Mutation = Annotated[
    ReplaceEvidenceText
    | ExpireEvidence
    | UnlinkEvidence
    | UnlinkDocument
    | SetAnswer
    | DeclareNotApplicable
    | Compensate
    | CollectedOverride
    | TamperArtifact,
    Field(discriminator="op"),
]
"""Lista cerrada. Todas se aplican antes de generar el expediente: sin fase configurable."""


class Case(Strict):
    format: Literal["ens-benchmark-case/1"]
    id: Slug
    partition: Partition
    profile: Slug | None = None
    workspace: str | None = Field(
        default=None, min_length=1, description="Copia de un workspace real; solo fuera del repo."
    )
    kind: CaseKind
    judgment: bool = Field(
        default=True,
        description="False: caso solo determinista; se ejecuta sin consentimiento ni proveedor.",
    )
    mutations: list[Mutation] = Field(default_factory=list)
    defects: list[Defect] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.profile is None) == (self.workspace is None):
            raise ValueError("un caso parte de un `profile` o de un `workspace`, y solo de uno")
        if self.kind is CaseKind.CORRECT and self.defects:
            raise ValueError("un caso correcto no lleva defectos")
        if self.kind is CaseKind.DEFECTIVE and not self.defects:
            raise ValueError("un caso defectuoso lleva al menos un defecto")
        if repeated := _repeated([d.id for d in self.defects]):
            raise ValueError(f"defecto repetido: {', '.join(repeated)}")
        tampered = any(isinstance(m, TamperArtifact) for m in self.mutations)
        if tampered and self.judgment:
            raise ValueError("un caso con `tamper_artifact` es solo determinista (judgment: false)")
        judged = [d for d in self.defects if d.layer is DefectLayer.JUDGMENT]
        if judged and not self.judgment:
            raise ValueError("un caso solo determinista no lleva defectos de juicio")
        keys = [f"{d.measure} {d.target.key} {d.expected_criterion}" for d in judged]
        if repeated := _repeated(keys):
            raise ValueError(
                "dos defectos de juicio con la misma medida, objetivo y criterio: "
                + ", ".join(repeated)
            )
        return self

    @property
    def key(self) -> str:
        return f"{self.partition}/{self.id}"


# --- grabación -------------------------------------------------------------------------------


class RepetitionOutcome(StrEnum):
    RESPONSE = "response"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_ERROR = "schema_error"


class Repetition(Strict):
    n: int = Field(ge=1)
    at: AwareDatetime
    outcome: RepetitionOutcome
    response: str | None = Field(
        default=None, description="Texto devuelto por `judge()`, solo si pasó el esquema."
    )
    kind: ProviderErrorKind | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.response is not None) != (self.outcome is RepetitionOutcome.RESPONSE):
            raise ValueError("`response` va con, y solo con, `outcome: response`")
        if (self.kind is not None) != (self.outcome is RepetitionOutcome.PROVIDER_ERROR):
            raise ValueError("`kind` va con, y solo con, `outcome: provider_error`")
        return self


class Recording(Strict):
    """Respuestas grabadas de un proveedor para un caso, ligadas al pack y al payload completo."""

    format: Literal["ens-benchmark-recording/1"]
    case: str = Field(pattern=r"^(dev|holdout)/[a-z0-9][a-z0-9-]*$")
    case_sha256: Sha256
    provider: Provider
    model: str = Field(min_length=1)
    destination: str = Field(min_length=1, description="Sin la ruta absoluta del ejecutable.")
    temperature: None = None
    temperature_note: str = TEMPERATURE_NOTE
    pack_version: str = Field(min_length=1)
    pack_sha256: Sha256
    payload_sha256: Sha256
    tool_version: str = Field(min_length=1)
    catalog_sha256: Sha256
    recorded_at: AwareDatetime
    recorded_by: str = Field(min_length=1)
    repetitions: list[Repetition] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if [r.n for r in self.repetitions] != list(range(1, len(self.repetitions) + 1)):
            raise ValueError("las repeticiones se numeran 1, 2, 3… en orden")
        if _ABSOLUTE_TOKEN.search(self.destination):
            raise ValueError("el destino no puede llevar una ruta absoluta")
        return self


# --- resultados ------------------------------------------------------------------------------


class DefectResult(Strict):
    defect: str = Field(min_length=1, description="`<partición>/<caso>#<defecto>`.")
    layer: DefectLayer
    detected_by_repetition: list[bool] = Field(min_length=1)
    detected: bool = Field(description="Detectado en la mayoría de las repeticiones.")


class RepetitionResult(Strict):
    n: int = Field(ge=1)
    outcome: RepetitionOutcome | None = Field(
        default=None, description="None en un caso solo determinista."
    )
    judgment_emitted: int = Field(ge=0, description="Tras agrupar duplicados.")
    judgment_false_alarms: int = Field(ge=0)
    deterministic_unmatched: int = Field(ge=0)


class CaseResult(Strict):
    case: str = Field(min_length=1)
    kind: CaseKind
    judgment: bool
    case_sha256: Sha256
    payload_sha256: Sha256 | None = None
    repetitions: list[RepetitionResult] = Field(min_length=1)
    defects: list[DefectResult] = Field(default_factory=list)


class Rate(Strict):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float = Field(ge=0, le=1, description="0 si el denominador es 0; se informa aparte.")


class Totals(Strict):
    deterministic: Rate
    judgment: Rate
    false_alarms: Rate = Field(description="Tasa A: falsas alarmas de juicio / emitidos.")
    correct_with_false_alarms: Rate = Field(
        description="Tasa B: expedientes correctos con alguna falsa alarma / correctos."
    )
    false_alarms_by_repetition: list[Rate] = Field(
        default_factory=list, description="Tasa A de cada repetición; `false_alarms` es su mediana."
    )
    correct_with_false_alarms_by_repetition: list[Rate] = Field(
        default_factory=list, description="Tasa B de cada repetición; la de arriba es su mediana."
    )
    zero_emitted_repetitions: list[int] = Field(
        default_factory=list,
        description="Repeticiones sin hallazgos de juicio emitidos: su tasa A es 0.",
    )
    deterministic_unmatched: int = Field(
        default=0, ge=0, description="Hallazgos deterministas sin casar: fallo de puerta."
    )


class ProviderResult(Strict):
    provider: Provider
    model: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    temperature: None = None
    temperature_note: str = TEMPERATURE_NOTE
    recordings_sha256: Sha256
    recorded_at: list[AwareDatetime] = Field(default_factory=list)
    recorded_by: list[str] = Field(default_factory=list)
    cases: list[CaseResult]
    totals: Totals
    gate_passed: bool | None = Field(default=None, description="None si no se aplicó puerta.")
    gate_problems: list[str] = Field(default_factory=list)


class BenchmarkResult(Strict):
    format: Literal["ens-benchmark-result/1"]
    mode: Literal["replay"]
    partition: Partition
    gate: Literal["baseline", "threshold"] | None = None
    run_at: AwareDatetime
    tool_version: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    pack_sha256: Sha256
    catalog_sha256: Sha256
    cases_sha256: Sha256
    notice: Notice = NOT_A_VERDICT
    results: list[ProviderResult]


# --- línea base ------------------------------------------------------------------------------


class BaselineEntry(Strict):
    provider: Provider
    model: str = Field(min_length=1)
    detected: list[str] = Field(description="Defectos detectados, `<partición>/<caso>#<defecto>`.")
    totals: Totals


class Retired(Strict):
    """Migración explícita: un caso o un defecto que sale del inventario."""

    id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Baseline(Strict):
    format: Literal["ens-benchmark-baseline/1"]
    partition: Literal[Partition.DEV] = Partition.DEV
    written_at: AwareDatetime
    pack_sha256: Sha256
    inventory: dict[str, Sha256] = Field(
        default_factory=dict, description="Huella de cada caso y defecto del banco."
    )
    entries: list[BaselineEntry] = Field(default_factory=list)
    retired: list[Retired] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        pairs = [f"{e.provider}/{e.model}" for e in self.entries]
        if repeated := _repeated(pairs):
            raise ValueError(f"proveedor y modelo repetidos: {', '.join(repeated)}")
        if repeated := _repeated([r.id for r in self.retired]):
            raise ValueError(f"retirada repetida: {', '.join(repeated)}")
        return self


# --- mediciones de la reservada --------------------------------------------------------------


class Measurement(Strict):
    """Una línea de `measurements.jsonl`: cada mirada a la reservada hecha con la herramienta."""

    at: AwareDatetime
    action: str = Field(min_length=1)
    partition: Literal[Partition.HOLDOUT] = Partition.HOLDOUT
    pack_sha256: Sha256 | None = None
    provider: Provider | None = None
    model: str | None = Field(default=None, min_length=1)
    result: dict[str, Any] | None = None
