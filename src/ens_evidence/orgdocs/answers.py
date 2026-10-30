"""Respuestas de la organización (`org/answers.yaml`): esquema derivado de las plantillas."""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml

from ens_evidence.orgdocs.clauses import official_clause
from ens_evidence.orgdocs.contract import (
    DECLARED_PERSON_QUESTIONS,
    PEOPLE,
    PERSON_NAME,
    RELATION,
    RESPONSIBLES,
    SAME_PERSON,
    SECURITY_OFFICER,
    SYSTEM_OWNER,
)
from ens_evidence.orgdocs.keys import ANSWERS_KEY
from ens_evidence.orgdocs.template import (
    AnswerType,
    Condition,
    LoadedTemplate,
    Question,
    all_questions,
)
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

NOT_APPLICABLE = "not_applicable"
REASON = "reason"
_NON_ALNUM = re.compile(r"[\W_]+")
_PLACEHOLDER = re.compile(r"\[[^\]]*\]|<[^>]*>|\bx{3,}\b", re.IGNORECASE)
# Valores completos que no responden nada, tras normalizar (sin tildes, mayúsculas ni signos).
_FILLER = {"", "na", "nd", "pendiente", "pordefinir", "tbd"}
_LOREM = "loremipsum"


@dataclass(frozen=True)
class NotApplicable:
    reason: str


Value = str | list[str] | list[dict[str, str]] | NotApplicable


@dataclass(frozen=True)
class Pending:
    question: Question
    documents: tuple[str, ...]

    def describe(self) -> str:
        return (
            f"{self.question.id}: {self.question.text} "
            f"(cláusula {self.question.clause}; documentos: {', '.join(self.documents)})"
        )


@dataclass
class AnswersReport:
    values: dict[str, Value] = field(default_factory=dict)
    pending: list[Pending] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.pending and not self.problems

    def errors(self) -> list[str]:
        return [f"pendiente {p.describe()}" for p in self.pending] + self.problems

    def problems_in(self, question_ids: set[str]) -> list[str]:
        """Problemas de esas preguntas y los globales (los que no son de ninguna pregunta)."""

        def owner(problem: str) -> str:
            return problem.split(":", 1)[0]

        return [p for p in self.problems if owner(p) in question_ids or owner(p) not in self.values]


def squash(text: str) -> str:
    """Forma comparable: sin tildes, mayúsculas, signos ni espacios."""
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_ALNUM.sub("", stripped.casefold())


def is_filler(text: str, question: Question | None = None) -> bool:
    key = squash(text)
    return (
        key in _FILLER
        or key.startswith(_LOREM)
        or (question is not None and key == squash(question.text))
    )


def _clean(text: str) -> str:
    lines = [
        line.rstrip() for line in unicodedata.normalize("NFC", text).replace("\r", "").split("\n")
    ]
    return "\n".join(lines).strip()


def _one_line(text: str) -> str:
    return " ".join(_clean(text).split())


def requirement_label(question: Question) -> str:
    if not question.required:
        return "opcional"
    if condition := question.required_if:
        return f"obligatoria si {condition.question} es " + " o ".join(condition.any_of)
    return "obligatoria"


def declared_people(values: dict[str, Value]) -> list[str]:
    people = values.get(PEOPLE)
    if not isinstance(people, list):
        return []
    return [p[PERSON_NAME] for p in people if isinstance(p, dict) and p.get(PERSON_NAME)]


def unanswered(value: Value) -> bool:
    return not isinstance(value, NotApplicable) and not value


def _normalize(question: Question, raw: Any, problems: list[str]) -> Value:
    where = question.id
    if isinstance(raw, dict) and NOT_APPLICABLE in raw:
        return _not_applicable(question, raw, problems)
    match question.type:
        case AnswerType.TEXT:
            if raw is None:
                return ""
            if not isinstance(raw, str):
                problems.append(f"{where}: se esperaba texto")
                return ""
            text = _clean(raw)
            if is_filler(text, question):
                return ""
            return _choice(question, text, problems) if question.choices else text
        case AnswerType.LIST:
            return _items(question, raw, problems)
        case AnswerType.RECORDS:
            return _records(question, raw, problems)


def _choice(question: Question, text: str, problems: list[str]) -> str:
    for option in question.choices:
        if squash(option) == squash(text):
            return option
    problems.append(f"{question.id}: debe ser una de estas opciones: {', '.join(question.choices)}")
    return ""


def condition_met(condition: Condition, values: dict[str, Value]) -> bool:
    return values.get(condition.question) in condition.any_of


def _not_applicable(question: Question, raw: dict, problems: list[str]) -> Value:
    reason = raw.get(REASON)
    if not question.allow_not_applicable:
        problems.append(f"{question.id}: esta pregunta no admite «no aplica»")
    elif set(raw) != {NOT_APPLICABLE, REASON} or raw[NOT_APPLICABLE] is not True:
        problems.append(f"{question.id}: «no aplica» es {{not_applicable: true, reason: <motivo>}}")
    elif not isinstance(reason, str) or is_filler(reason, question):
        problems.append(f"{question.id}: «no aplica» exige un motivo concreto")
    else:
        return NotApplicable(_one_line(reason))
    return ""


def _items(question: Question, raw: Any, problems: list[str]) -> Value:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
        problems.append(f"{question.id}: se esperaba una lista de textos")
        return []
    items = []
    for number, item in enumerate(raw, 1):
        if is_filler(item, question):
            problems.append(f"{question.id}: el elemento {number} está vacío o es de relleno")
        items.append(_one_line(item))
    return items


def _records(question: Question, raw: Any, problems: list[str]) -> Value:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        problems.append(f"{question.id}: se esperaba una lista de registros")
        return []
    ids = {f.id for f in question.fields}
    records = []
    for number, record in enumerate(raw, 1):
        extra = set(record) - ids
        if extra:
            problems.append(
                f"{question.id}: registro {number} con campos desconocidos {sorted(extra)}"
            )
        clean: dict[str, str] = {}
        for spec in question.fields:
            value = record.get(spec.id)
            text = _one_line(value) if isinstance(value, str) else ""
            if spec.required and is_filler(text, question):
                problems.append(
                    f"{question.id}: registro {number}, «{spec.text}» vacío o de relleno"
                )
            clean[spec.id] = "" if is_filler(text, question) else text
        records.append(clean)
    return records


def validate_answers(
    raw: Any, templates: list[LoadedTemplate] | tuple[LoadedTemplate, ...]
) -> AnswersReport:
    report = AnswersReport()
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        report.problems.append(f"{ANSWERS_KEY}: debe ser un mapa de pregunta a respuesta")
        return report
    questions = all_questions(templates)
    known = {q.id for q in questions}
    report.problems += [
        f"{ANSWERS_KEY}: pregunta desconocida {key}" for key in sorted(map(str, set(raw) - known))
    ]
    documents = {
        q.id: tuple(t.id for t in templates if any(x.id == q.id for x in t.template.questions))
        for q in questions
    }
    for question in questions:
        report.values[question.id] = _normalize(question, raw.get(question.id), report.problems)
    _check_responsibles(report)
    same_person = _same_person(report.values)
    for question in questions:
        applies = question.required_if is None or condition_met(question.required_if, report.values)
        if question.required_if and question.required_if.question == RELATION:
            applies = applies or same_person
        value = report.values[question.id]
        if question.required and applies and unanswered(value) and not _reported(report, question):
            report.pending.append(Pending(question, documents[question.id]))
    report.warnings = _warnings(questions, report.values)
    return report


def _person(values: dict[str, Value], question_id: str) -> str:
    value = values.get(question_id)
    return squash(value) if isinstance(value, str) else ""


def _same_person(values: dict[str, Value]) -> bool:
    """La coincidencia se deriva de los nombres, no de lo que se declare en la relación."""
    security = _person(values, SECURITY_OFFICER)
    return bool(security) and security == _person(values, SYSTEM_OWNER)


def _check_responsibles(report: AnswersReport) -> None:
    """Art. 13.2-13.3: responsables y firmante de la dirección son personas declaradas, y la
    relación entre el de la seguridad y el del sistema no puede contradecir sus nombres."""
    values, problems = report.values, report.problems
    declared = {squash(name) for name in declared_people(values)}
    if not declared:
        return
    for question_id in DECLARED_PERSON_QUESTIONS:
        value = values.get(question_id)
        if isinstance(value, str) and value and squash(value) not in declared:
            problems.append(f"{question_id}: «{value}» no figura en la lista de personas")
    relation = values.get(RELATION)
    if not relation:
        return
    same = _same_person(values)
    if same and relation != SAME_PERSON:
        problems.append(
            f"{RELATION}: {SECURITY_OFFICER} y {SYSTEM_OWNER} son la misma persona; "
            f"responde «{SAME_PERSON}»"
        )
    elif relation == SAME_PERSON and not same:
        problems.append(
            f"{RELATION}: «{SAME_PERSON}» contradice {SECURITY_OFFICER} y {SYSTEM_OWNER}, "
            "que son personas distintas"
        )


def _reported(report: AnswersReport, question: Question) -> bool:
    """Un valor con problema ya tiene su propio mensaje; no se duplica como pendiente."""
    return any(p.startswith(f"{question.id}:") for p in report.problems)


def _texts(value: Value) -> list[str]:
    match value:
        case str():
            return [value]
        case NotApplicable():
            return [value.reason]
        case [*items]:
            return [t for i in items for t in (i.values() if isinstance(i, dict) else [i])]


def _warnings(questions: list[Question], values: dict[str, Value]) -> list[str]:
    warnings = []
    seen: dict[str, str] = {}
    for question in questions:
        if question.choices:
            continue
        for text in _texts(values[question.id]):
            if not text:
                continue
            if _PLACEHOLDER.search(text):
                warnings.append(f"{question.id}: parece contener un marcador sin sustituir")
            official = squash(official_clause(question.clause).text)
            if squash(text) == official:
                warnings.append(f"{question.id}: repite el texto oficial de la cláusula")
            if question.type is not AnswerType.TEXT or question.id in RESPONSIBLES:
                continue
            other = seen.setdefault(squash(text), question.id)
            if other != question.id:
                warnings.append(f"{question.id}: repite literalmente la respuesta de {other}")
    return sorted(dict.fromkeys(warnings))


def read_answers(store: WorkspaceStore) -> Any:
    """Contenido YAML de `org/answers.yaml`; lanza ValueError con un mensaje legible."""
    try:
        text = store.read_text(ANSWERS_KEY)
    except NotFound:
        raise ValueError(f"falta {ANSWERS_KEY}; créalo con `ens-evidence docs init`") from None
    except NotText:
        raise ValueError(f"{ANSWERS_KEY} no es texto UTF-8") from None
    try:
        return safe_load_unique(text)
    except yaml.YAMLError:
        raise ValueError(f"{ANSWERS_KEY} no es YAML válido") from None


def skeleton(templates: list[LoadedTemplate] | tuple[LoadedTemplate, ...]) -> str:
    lines = [
        "# Respuestas para la documentación organizativa (ens-evidence docs build).",
        "# Cada respuesta debe ser concreta; un valor vacío o de relleno no cuenta.",
        "# «No aplica» solo donde se indica: {not_applicable: true, reason: <motivo concreto>}.",
        "",
    ]
    for question in all_questions(templates):
        lines.append(f"# {question.text}")
        lines.append(f"#   cláusula {question.clause}; {requirement_label(question)}")
        if question.help:
            lines.append(f"#   {question.help}")
        if question.allow_not_applicable:
            lines.append("#   admite «no aplica» con motivo")
        if question.choices:
            lines.append("#   opciones: " + "; ".join(question.choices))
        if question.type is AnswerType.RECORDS:
            lines.append("#   campos: " + "; ".join(f"{f.id} ({f.text})" for f in question.fields))
        empty = '""' if question.type is AnswerType.TEXT else "[]"
        lines += [f"{question.id}: {empty}", ""]
    return "\n".join(lines)
