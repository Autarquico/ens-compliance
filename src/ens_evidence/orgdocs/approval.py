"""Aprobación de documentos organizativos y vigencia de cada aprobación.

Aprobar un documento acredita que una persona con la autoridad debida lo revisó; no cambia
la evaluación de ninguna medida (eso lo decide la evidencia y la revisión de cada medida).
Los registros se conservan todos: `org/docs/approvals/<doc>/<instante>.yaml`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import yaml
from pydantic import AwareDatetime, Field, ValidationError, field_validator

from ens_evidence.approvals import safe_load_unique
from ens_evidence.catalog import catalog_sha256
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Role
from ens_evidence.orgdocs.answers import (
    AnswersReport,
    Value,
    read_answers,
    squash,
    validate_answers,
)
from ens_evidence.orgdocs.contract import (
    AUTHORITY_ROLE,
    DIRECTION_SIGNATORY,
    PROCEDURES_AUTHORITY_QUESTION,
    SECURITY_OFFICER,
    SYSTEM_OWNER,
)
from ens_evidence.orgdocs.document import answers_sha256, compose, sha256_text
from ens_evidence.orgdocs.keys import DOCS_PREFIX, document_key, document_names, meta_key
from ens_evidence.orgdocs.meta import DocumentMeta, TemplateRef
from ens_evidence.orgdocs.render import RENDERER_VERSION
from ens_evidence.orgdocs.sources import articulado_sha256
from ens_evidence.orgdocs.template import LoadedTemplate, load_templates
from ens_evidence.scope import ScopeState, require_approved_scope, scope_status
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

APPROVALS_PREFIX = f"{DOCS_PREFIX}/approvals"
JUSTIFICATION_CLAUSE = "art.13.3"
FIXED_AUTHORITY = {
    "politica-seguridad": Role.MANAGEMENT,
    "normativa-seguridad": Role.MANAGEMENT,
    "designacion-responsables": Role.MANAGEMENT,
}
ROLE_QUESTION = {
    Role.MANAGEMENT: DIRECTION_SIGNATORY,
    Role.SECURITY_OFFICER: SECURITY_OFFICER,
    Role.SYSTEM_OWNER: SYSTEM_OWNER,
}
STAMP = "%Y%m%dT%H%M%S%fZ"


class DocumentApproval(Strict):
    """Quién aprobó qué: persona declarada (no autenticada), rol y los hashes que fijan el
    documento, las respuestas, la plantilla, el catálogo, la fuente y el alcance de entonces."""

    document: str
    approved_by: str
    role: Role
    approved_at: AwareDatetime
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    answers_sha256: str = Field(pattern=SHA256_PATTERN)
    template: TemplateRef
    renderer_version: int
    catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    articulado_sha256: str = Field(pattern=SHA256_PATTERN)
    scope_fingerprint: str = Field(pattern=SHA256_PATTERN)

    @field_validator("approved_by")
    @classmethod
    def _declared(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quien aprueba no puede estar vacío")
        return value


class DocumentApprovalRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class UnknownDocument(KeyError):
    pass


class DocumentState(StrEnum):
    NOT_GENERATED = "NOT_GENERATED"
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    OBSOLETE = "OBSOLETE"


@dataclass(frozen=True)
class DocumentStatus:
    """GENERATED: sin aprobar (`reasons`: por qué ya no coincide con sus entradas, si procede).
    APPROVED: aprobado y vigente. OBSOLETE: aprobado, pero `reasons` dice qué cambió."""

    name: str
    state: DocumentState
    reasons: list[str] = field(default_factory=list)
    approval: DocumentApproval | None = None
    unreadable: list[str] = field(default_factory=list)

    @property
    def current(self) -> bool:
        return self.state is DocumentState.APPROVED


def required_role(name: str, answers: dict[str, Value]) -> Role | None:
    """Rol que debe aprobar el documento; None si la organización aún no lo ha declarado."""
    if name in FIXED_AUTHORITY:
        return FIXED_AUTHORITY[name]
    declared = answers.get(PROCEDURES_AUTHORITY_QUESTION)
    return AUTHORITY_ROLE.get(declared) if isinstance(declared, str) else None


def _declared_signatory(role: Role, by: str, values: dict[str, Value]) -> str:
    """Nombre declarado de quien aprueba, tal como figura en las respuestas. La dirección firma a
    través de la persona declarada en `firmante_direccion` (la dirección puede ser un órgano)."""
    question = ROLE_QUESTION[role]
    holder = values.get(question)
    if not isinstance(holder, str) or squash(holder) != squash(by):
        declared = f": {holder}" if isinstance(holder, str) and holder else ""
        raise DocumentApprovalRejected([f"{by} no es la persona declarada en {question}{declared}"])
    return holder


def _template(name: str) -> LoadedTemplate:
    for loaded in load_templates():
        if loaded.id == name:
            return loaded
    raise UnknownDocument(name)


def _read(store: WorkspaceStore, key: str) -> str | None:
    try:
        return store.read_text(key)
    except (NotFound, NotText):
        return None


def _meta(store: WorkspaceStore, name: str) -> DocumentMeta | None:
    text = _read(store, meta_key(name))
    try:
        return DocumentMeta.model_validate(safe_load_unique(text)) if text else None
    except (yaml.YAMLError, ValidationError):
        return None


def scan_approvals(store: WorkspaceStore, name: str) -> tuple[list[DocumentApproval], list[str]]:
    """Aprobaciones legibles (de la más antigua a la más reciente) y claves de las ilegibles.

    Un registro ilegible, o cuyo contenido no coincide con su clave, podría ser una aprobación
    posterior: su contenido es desconocido."""
    records = []
    unreadable = []
    prefix = f"{APPROVALS_PREFIX}/{name}/"
    for key in sorted(k for k in store.list(prefix) if k.startswith(prefix)):
        text = _read(store, key)
        try:
            if text is None:
                raise ValueError(key)
            approval = DocumentApproval.model_validate(safe_load_unique(text))
            # La clave codifica documento e instante: un registro que no los repite no es de fiar
            # (p. ej. `approved_at` retrofechado para que no parezca posterior a un registro).
            expected = f"{prefix}{approval.approved_at.astimezone(UTC).strftime(STAMP)}.yaml"
            if key != expected or approval.document != name:
                raise ValueError(key)
            records.append(approval)
        except (yaml.YAMLError, ValidationError, ValueError):
            unreadable.append(key)
    return records, unreadable


def approval_history(store: WorkspaceStore, name: str) -> list[DocumentApproval]:
    """Aprobaciones legibles del documento, de la más antigua a la más reciente."""
    return scan_approvals(store, name)[0]


def _relevant_pending(report: AnswersReport, loaded: LoadedTemplate) -> list[str]:
    own = {q.id for q in loaded.template.questions}
    pending = [p for p in report.pending if p.question.id in own]
    pending.sort(key=lambda p: p.question.clause != JUSTIFICATION_CLAUSE)
    return [f"pendiente {p.describe()}" for p in pending] + report.problems_in(own)


def _answers_report(
    store: WorkspaceStore, loaded: LoadedTemplate
) -> tuple[AnswersReport, list[str]]:
    """Respuestas validadas y, de ellas, lo que impide usar este documento."""
    try:
        report = validate_answers(read_answers(store), load_templates())
    except ValueError as exc:
        report = AnswersReport(problems=[str(exc)])
    return report, _relevant_pending(report, loaded)


@dataclass(frozen=True)
class _Basis:
    document_sha256: str
    answers_sha256: str
    template: TemplateRef
    renderer_version: int
    catalog_sha256: str
    articulado_sha256: str


def _drift(
    reference: _Basis, store: WorkspaceStore, loaded: LoadedTemplate, meta: DocumentMeta | None
) -> list[str]:
    """Por qué el documento actual y sus entradas ya no coinciden con `reference`."""
    text = _read(store, document_key(loaded.id))
    actual = sha256_text(text) if text is not None else None
    reasons = []
    report, blocked = _answers_report(store, loaded)
    if blocked:
        reasons.append("las respuestas actuales no son válidas o están pendientes")
    elif answers_sha256(loaded, report.values) != reference.answers_sha256:
        reasons.append("respuestas cambiadas")
    if reference.template != TemplateRef(id=loaded.id, version=loaded.version):
        reasons.append("plantilla cambiada de versión")
    if reference.renderer_version != RENDERER_VERSION:
        reasons.append("renderizador cambiado")
    if reference.catalog_sha256 != catalog_sha256():
        reasons.append("catálogo cambiado")
    if reference.articulado_sha256 != articulado_sha256():
        reasons.append("fuente del articulado cambiada")
    # El meta también es editable: con las entradas intactas el documento ha de ser, byte a
    # byte, el que sale de ellas.
    forged = not reasons and text != compose(loaded, report.values)[0]
    if actual != reference.document_sha256 or forged:
        by_hand = forged or meta is None or actual != meta.document_sha256
        reasons.insert(
            0,
            "documento editado a mano"
            if by_hand
            else "documento regenerado con contenido distinto",
        )
    return reasons


def _scope_drift(store: WorkspaceStore, approval: DocumentApproval) -> list[str]:
    status = scope_status(store)
    match status.state:
        case ScopeState.APPROVED if status.fingerprint == approval.scope_fingerprint:
            return []
        case ScopeState.APPROVED | ScopeState.STALE:
            return ["alcance cambiado desde la aprobación del documento"]
        case ScopeState.NOT_APPROVED:
            return ["aprobación del alcance revocada o ausente"]
        case ScopeState.INVALID:
            return ["alcance no válido", *status.errors]


def _basis_of_meta(meta: DocumentMeta) -> _Basis:
    return _Basis(
        meta.document_sha256,
        meta.answers_sha256,
        meta.template,
        meta.renderer_version,
        meta.catalog_sha256,
        meta.articulado_sha256,
    )


def _basis_of_approval(approval: DocumentApproval) -> _Basis:
    return _Basis(
        approval.document_sha256,
        approval.answers_sha256,
        approval.template,
        approval.renderer_version,
        approval.catalog_sha256,
        approval.articulado_sha256,
    )


def _signatory_drift(
    store: WorkspaceStore, loaded: LoadedTemplate, approval: DocumentApproval
) -> list[str]:
    """La aprobación vale mientras quien firmó siga siendo quien las respuestas declaran para
    ese rol, aunque el documento no lo muestre."""
    report, blocked = _answers_report(store, loaded)
    if blocked:
        return []
    if required_role(loaded.id, report.values) is not approval.role:
        return ["ha cambiado el rol que aprueba el documento"]
    try:
        _declared_signatory(approval.role, approval.approved_by, report.values)
    except DocumentApprovalRejected:
        question = ROLE_QUESTION[approval.role]
        return [f"{approval.approved_by} ya no es la persona declarada en {question}"]
    return []


def document_status(store: WorkspaceStore, name: str) -> DocumentStatus:
    loaded = _template(name)
    meta = _meta(store, name)
    if _read(store, document_key(name)) is None or meta is None:
        return DocumentStatus(name, DocumentState.NOT_GENERATED)
    history, unreadable = scan_approvals(store, name)
    broken = [f"registro de aprobación ilegible: {key}" for key in unreadable]
    if not history:
        reasons = _drift(_basis_of_meta(meta), store, loaded, meta)
        return DocumentStatus(name, DocumentState.GENERATED, reasons + broken, None, unreadable)
    approval = history[-1]
    reasons = (
        _drift(_basis_of_approval(approval), store, loaded, meta)
        + _signatory_drift(store, loaded, approval)
        + _scope_drift(store, approval)
        + broken
    )
    state = DocumentState.OBSOLETE if reasons else DocumentState.APPROVED
    return DocumentStatus(name, state, reasons, approval, unreadable)


def all_statuses(store: WorkspaceStore) -> list[DocumentStatus]:
    return [document_status(store, name) for name in document_names()]


def approve_document(
    store: WorkspaceStore, name: str, by: str, role: Role, now: datetime
) -> DocumentApproval:
    """Aprueba el documento generado y vigente. `by` es declarado, no autenticado.

    Exige alcance aprobado (`ScopeNotApproved` si no), documento sin editar a mano ni
    desactualizado respecto de las respuestas, y que `role` sea la autoridad del documento."""
    scope = require_approved_scope(store)
    loaded = _template(name)
    meta = _meta(store, name)
    if _read(store, document_key(name)) is None or meta is None:
        raise DocumentApprovalRejected([f"{name} no está generado; ejecuta `docs build`"])
    report, blocked = _answers_report(store, loaded)
    if blocked:
        if any(p.question.clause == JUSTIFICATION_CLAUSE for p in report.pending):
            blocked = [
                "justificación del art. 13.3 pendiente, la aprobación queda bloqueada",
                *blocked,
            ]
        raise DocumentApprovalRejected(blocked)
    stale = _drift(_basis_of_meta(meta), store, loaded, meta)
    if stale:
        raise DocumentApprovalRejected(
            [f"{name} no está al día ({reason})" for reason in stale]
            + ["revisa los cambios y ejecuta `docs build`"]
        )
    expected = required_role(name, report.values)
    if expected is None:
        raise DocumentApprovalRejected(
            [f"falta declarar quién aprueba {name} ({PROCEDURES_AUTHORITY_QUESTION})"]
        )
    if role is not expected:
        raise DocumentApprovalRejected(
            [f"{name} lo aprueba el rol {expected.value}, no {role.value}"]
        )
    if by.strip():
        by = _declared_signatory(role, by, report.values)
    try:
        approval = DocumentApproval(
            document=name,
            approved_by=by,
            role=role,
            approved_at=now,
            document_sha256=meta.document_sha256,
            answers_sha256=meta.answers_sha256,
            template=meta.template,
            renderer_version=meta.renderer_version,
            catalog_sha256=meta.catalog_sha256,
            articulado_sha256=meta.articulado_sha256,
            scope_fingerprint=scope.fingerprint or "",
        )
    except ValidationError as exc:
        raise DocumentApprovalRejected([e["msg"] for e in exc.errors()]) from None
    key = f"{APPROVALS_PREFIX}/{name}/{now.astimezone(UTC).strftime(STAMP)}.yaml"
    store.create_bytes(
        key, yaml.safe_dump(approval.model_dump(mode="json"), sort_keys=False).encode("utf-8")
    )
    return approval
