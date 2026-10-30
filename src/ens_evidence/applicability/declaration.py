"""Declaración de Aplicabilidad (art. 28.2 del RD 311/2022): relación de medidas seleccionadas
firmada por el responsable de la seguridad, con las huellas del contexto en que se firmó."""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import yaml
from pydantic import AwareDatetime, Field, ValidationError, field_validator

from ens_evidence.applicability.context import Context
from ens_evidence.applicability.validate import safeguard_findings, validate_applicability
from ens_evidence.approvals import Rejected, SecurityOfficer, file_sha256, read_record
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import (
    CatalogVersion,
    MeasuresFileError,
    current_catalog_version,
    load_measures,
)
from ens_evidence.categorization import (
    CategorizationState,
    categorization_status,
)
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Applicability, Category, Role
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import AlreadyExists, WorkspaceStore

DECLARATION_KEY = "controls/applicability.yaml"
DECLARATIONS_PREFIX = "controls/declarations"


class SelectedMeasure(Strict):
    id: str
    required_by: str | None = Field(
        default=None, description="Por qué la exige el anexo II; None si es adicional (art. 28.2)."
    )
    required_reinforcements: list[str] = Field(default_factory=list)
    alternatives: list[list[str]] = Field(default_factory=list)
    chosen_reinforcements: list[str] = Field(
        default_factory=list, description="Refuerzo que adopta la organización en cada grupo."
    )


class NotSelected(Strict):
    id: str
    justification: str


class AbsentAssetsRecord(Strict):
    """Medida exigida por el anexo II que no aplica porque no existe el tipo de activo."""

    id: str
    required_by: str
    asset_type: str
    justification: str


class CompensationRecord(Strict):
    replaces: str = Field(description="Medida del anexo II que se sustituye.")
    obligations: list[str] = Field(
        description="Obligaciones de la medida que sustituyen: `base` y/o refuerzos."
    )
    measures: list[str]
    justification: str


class Declaration(Strict):
    measures_sha256: str = Field(pattern=SHA256_PATTERN)
    categorization_sha256: str = Field(pattern=SHA256_PATTERN)
    category: Category
    catalog_version: CatalogVersion
    scope_fingerprint: str = Field(pattern=SHA256_PATTERN)
    selected: list[SelectedMeasure]
    not_selected: list[NotSelected]
    required_but_absent_assets: list[AbsentAssetsRecord] = Field(
        description="Exigidas por el anexo II y no aplicables por ausencia del tipo de activo."
    )
    compensations: list[CompensationRecord]
    approved_by: str
    role: SecurityOfficer
    approved_at: AwareDatetime

    @field_validator("approved_by")
    @classmethod
    def _declared(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quien firma no puede estar vacío")
        return value


class DeclarationState(StrEnum):
    INVALID = "INVALID"
    NOT_APPROVED = "NOT_APPROVED"
    STALE = "STALE"
    CURRENT = "CURRENT"


@dataclass(frozen=True)
class DeclarationStatus:
    state: DeclarationState
    errors: list[str] = field(default_factory=list)
    declaration: Declaration | None = None


class DeclarationRejected(Rejected):
    """Motivos en `errors`."""


def _build(
    context: Context, measures: list, measures_sha256: str, by: str, now: datetime
) -> Declaration:
    selected, not_selected, absent, compensations = [], [], [], []
    for measure in measures:
        selection = context.selections[measure.id]
        if measure.applicability is Applicability.APPLICABLE:
            selected.append(
                SelectedMeasure(
                    id=measure.id,
                    required_by=selection.required_by,
                    required_reinforcements=list(selection.reinforcements),
                    alternatives=[list(group) for group in selection.alternatives],
                    chosen_reinforcements=list(measure.chosen_reinforcements),
                )
            )
        elif measure.absent_assets is not None:
            absent.append(
                AbsentAssetsRecord(
                    id=measure.id,
                    required_by=selection.required_by or "",
                    asset_type=measure.absent_assets.asset_type,
                    justification=measure.absent_assets.justification,
                )
            )
        else:
            not_selected.append(
                NotSelected(id=measure.id, justification=measure.justification or "")
            )
        if measure.compensation:
            compensations.append(
                CompensationRecord(
                    replaces=measure.id,
                    obligations=list(measure.compensation.replaces),
                    measures=measure.compensation.measures,
                    justification=measure.compensation.justification,
                )
            )
    approval = context.categorization.approval
    assert approval is not None
    return Declaration(
        measures_sha256=measures_sha256,
        categorization_sha256=approval.file_sha256,
        category=context.category,
        catalog_version=context.version,
        scope_fingerprint=context.scope.fingerprint or "",
        selected=selected,
        not_selected=not_selected,
        required_but_absent_assets=absent,
        compensations=compensations,
        approved_by=by,
        role=Role.SECURITY_OFFICER,
        approved_at=now,
    )


def approve_declaration(store: WorkspaceStore, by: str, now: datetime) -> Declaration:
    """Firma la Declaración como `security_officer`. `by` es declarado, no autenticado.

    Lanza `ScopeNotApproved`, `CategorizationNotCurrent` o `DeclarationRejected`.
    """
    validation = validate_applicability(store, now)
    if validation.errors or validation.measures is None:
        raise DeclarationRejected(validation.errors)
    try:
        declaration = _build(
            validation.context,
            validation.measures.measures,
            file_sha256(store, MEASURES_KEY),
            by,
            now,
        )
    except ValidationError as exc:
        raise DeclarationRejected(describe_validation("firma", exc)) from None
    text = yaml.safe_dump(declaration.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    store.write_text(DECLARATION_KEY, text)
    _archive(store, declaration, text)
    return declaration


def _archive(store: WorkspaceStore, declaration: Declaration, text: str) -> None:
    """Copia de solo adición de cada firma: la Declaración vigente se sobrescribe y, sin esta
    copia, volver a un contexto anterior sería indistinguible de no haberlo dejado nunca."""
    stamp = declaration.approved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    try:
        store.create_bytes(f"{DECLARATIONS_PREFIX}/{stamp}-{digest}.yaml", text.encode())
    except AlreadyExists:
        pass


def declaration_history(store: WorkspaceStore) -> tuple[list[Declaration], list[str]]:
    """Declaraciones firmadas, de la más antigua a la más reciente, y los ficheros ilegibles
    (su contenido es desconocido). No incluye la vigente si se firmó antes de existir la copia."""
    found: list[Declaration] = []
    errors: list[str] = []
    for key in store.list(DECLARATIONS_PREFIX):
        declaration, _ = read_record(store, key, Declaration, "no se puede reconstruir")
        if declaration is None:
            errors.append(key)
        else:
            found.append(declaration)
    found.sort(key=lambda d: d.approved_at)
    return found, errors


def _stored(store: WorkspaceStore) -> tuple[Declaration | None, list[str]]:
    return read_record(store, DECLARATION_KEY, Declaration, "vuelve a firmar")


def _safeguard_reasons(store: WorkspaceStore) -> list[str]:
    """Una salvaguarda de un riesgo sin seleccionar vuelve obsoleta una Declaración ya firmada."""
    try:
        document = load_measures(store)
    except MeasuresFileError:
        return []
    return safeguard_findings(store, document)[0] if document else []


def declaration_status(store: WorkspaceStore, now: datetime) -> DeclarationStatus:
    """Vigente solo si nada de lo que se firmó ha cambiado."""
    declaration, errors = _stored(store)
    if errors:
        return DeclarationStatus(DeclarationState.INVALID, errors)
    if declaration is None:
        return DeclarationStatus(DeclarationState.NOT_APPROVED)
    reasons = []
    scope = scope_status(store)
    if scope.state is not ScopeState.APPROVED:
        reasons.append("el alcance no está aprobado ahora (scope approve)")
    elif scope.fingerprint != declaration.scope_fingerprint:
        reasons.append("el alcance cambió desde que se firmó la Declaración")
    categorization = categorization_status(store, now)
    approval = categorization.approval
    if categorization.state is not CategorizationState.CURRENT or approval is None:
        reasons.append("la categorización no está vigente (categorization status)")
    elif approval.file_sha256 != declaration.categorization_sha256:
        reasons.append("la categorización cambió desde que se firmó la Declaración")
    if not store.exists(MEASURES_KEY):
        reasons.append(f"{MEASURES_KEY} ya no existe")
    elif file_sha256(store, MEASURES_KEY) != declaration.measures_sha256:
        reasons.append(f"{MEASURES_KEY} cambió desde que se firmó la Declaración")
    reasons.extend(_safeguard_reasons(store))
    if current_catalog_version(load_catalog()) != declaration.catalog_version:
        reasons.append("la versión del catálogo cambió desde que se firmó la Declaración")
    state = DeclarationState.STALE if reasons else DeclarationState.CURRENT
    return DeclarationStatus(state, reasons, declaration)
