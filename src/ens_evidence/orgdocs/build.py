"""`docs build`: valida todo, compone todo y solo entonces escribe, de forma idempotente."""

from dataclasses import dataclass, field
from enum import StrEnum

import yaml
from pydantic import ValidationError

from ens_evidence.orgdocs.answers import AnswersReport, read_answers, validate_answers
from ens_evidence.orgdocs.document import compose, sha256_text
from ens_evidence.orgdocs.keys import HISTORY_PREFIX, document_key, meta_key
from ens_evidence.orgdocs.meta import DocumentMeta
from ens_evidence.orgdocs.template import load_templates
from ens_evidence.scope import require_approved_scope
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

HISTORY_HASH_CHARS = 16


class AnswersInvalid(Exception):
    def __init__(self, report: AnswersReport) -> None:
        super().__init__("; ".join(report.errors()))
        self.report = report


class BuildStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class DocumentResult:
    name: str
    status: BuildStatus
    document_sha256: str
    edited_by_hand: bool = False


@dataclass
class BuildReport:
    documents: list[DocumentResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def dump_meta(meta: DocumentMeta) -> str:
    return yaml.safe_dump(meta.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def _read(store: WorkspaceStore, key: str) -> str | None:
    try:
        return store.read_text(key)
    except (NotFound, NotText):
        return None


def _recorded_sha(meta_text: str | None) -> str | None:
    if meta_text is None:
        return None
    try:
        return DocumentMeta.model_validate(yaml.safe_load(meta_text)).document_sha256
    except (yaml.YAMLError, ValidationError):
        return None


def _archive(store: WorkspaceStore, name: str, text: str, meta_text: str | None) -> None:
    stem = f"{HISTORY_PREFIX}/{name}/{sha256_text(text)[:HISTORY_HASH_CHARS]}"
    if not store.exists(f"{stem}.md"):
        store.write_text(f"{stem}.md", text)
    if meta_text is not None and not store.exists(f"{stem}.meta.yaml"):
        store.write_text(f"{stem}.meta.yaml", meta_text)


def build_documents(store: WorkspaceStore) -> BuildReport:
    """Genera `org/docs/<doc>.md` y `.meta.yaml`. Sin alcance aprobado o con respuestas
    pendientes no escribe nada. Una edición manual o una escritura interrumpida
    (documento distinto del sha256 del meta) se archiva en el historial antes de sustituirla."""
    require_approved_scope(store)
    templates = load_templates()
    try:
        raw = read_answers(store)
    except ValueError as exc:
        raise AnswersInvalid(AnswersReport(problems=[str(exc)])) from None
    answers = validate_answers(raw, templates)
    if not answers.valid:
        raise AnswersInvalid(answers)
    composed = [(loaded.id, *compose(loaded, answers.values)) for loaded in templates]
    report = BuildReport(warnings=answers.warnings)
    for name, text, meta in composed:
        meta_text = dump_meta(meta)
        current = _read(store, document_key(name))
        current_meta = _read(store, meta_key(name))
        if current == text and current_meta == meta_text:
            report.documents.append(
                DocumentResult(name, BuildStatus.UNCHANGED, meta.document_sha256)
            )
            continue
        edited = current is not None and sha256_text(current) != _recorded_sha(current_meta)
        if current is not None:
            _archive(store, name, current, current_meta)
        store.write_text(document_key(name), text)
        store.write_text(meta_key(name), meta_text)
        status = BuildStatus.UPDATED if current is not None else BuildStatus.CREATED
        report.documents.append(DocumentResult(name, status, meta.document_sha256, edited))
    return report
