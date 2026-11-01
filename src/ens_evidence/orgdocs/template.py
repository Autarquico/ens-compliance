"""Plantillas versionadas: documento -> secciones -> cláusulas cubiertas + preguntas."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources.abc import Traversable
from typing import Self

import yaml
from pydantic import Field, ValidationError, model_validator

from ens_evidence.models.contracts import Strict
from ens_evidence.orgdocs.clauses import clause_index
from ens_evidence.orgdocs.contract import (
    AUTHORITY_ROLE,
    DECLARED_PERSON_QUESTIONS,
    INCIDENT_LEVEL_FIELDS,
    INCIDENT_LEVELS,
    PEOPLE,
    PERSON_NAME,
    PROCEDURES_AUTHORITY_QUESTION,
    RELATION,
    SAME_PERSON,
)
from ens_evidence.orgdocs.keys import TEMPLATES, TEMPLATES_VERSION

IDENTIFIER = r"[a-z][a-z0-9_]*"
_ID = re.compile(rf"^{IDENTIFIER}$")
_EACH_OPEN = re.compile(rf"^\s*\{{\{{#each ({IDENTIFIER})\}}\}}\s*$")
_EACH_CLOSE = re.compile(r"^\s*\{\{/each\}\}\s*$")
_VARIABLE = re.compile(rf"\{{\{{\s*(\.?{IDENTIFIER}|\.)\s*\}}\}}")


class TemplateError(ValueError):
    def __init__(self, source: str, problems: list[str]) -> None:
        super().__init__(f"{source}: " + "; ".join(problems))
        self.source = source
        self.problems = problems


class AnswerType(StrEnum):
    TEXT = "text"
    LIST = "list"
    RECORDS = "records"


class RecordField(Strict):
    id: str
    text: str
    required: bool = True


class Condition(Strict):
    """Se cumple si la respuesta de `question` (con `choices`) es una de `any_of`."""

    question: str
    any_of: list[str] = Field(min_length=1)


class Question(Strict):
    id: str
    text: str
    type: AnswerType = AnswerType.TEXT
    required: bool = True
    clause: str = Field(description="Cláusula oficial que motiva la pregunta.")
    help: str | None = None
    allow_not_applicable: bool = False
    not_applicable_label: str = "No aplica"
    fields: list[RecordField] = Field(default_factory=list)
    choices: list[str] = Field(default_factory=list)
    required_if: Condition | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not _ID.match(self.id):
            raise ValueError(f"id de pregunta inválido: {self.id}")
        if self.choices and (self.type is not AnswerType.TEXT or len(self.choices) < 2):
            raise ValueError(f"{self.id}: `choices` es de `text` y exige al menos dos opciones")
        if (self.type is AnswerType.RECORDS) != bool(self.fields):
            raise ValueError(f"{self.id}: `fields` es obligatorio y exclusivo de `records`")
        ids = [f.id for f in self.fields]
        if len(ids) != len(set(ids)) or not all(_ID.match(i) for i in ids):
            raise ValueError(f"{self.id}: campos duplicados o con id inválido")
        return self


class Section(Strict):
    id: str
    title: str
    covers: list[str] = Field(default_factory=list)
    body: str
    when: Condition | None = None


class Template(Strict):
    id: str
    title: str
    questions: list[Question]
    sections: list[Section]


# Nodos del cuerpo ya analizado: texto con variables, o bloque repetido.
@dataclass(frozen=True)
class Line:
    text: str


@dataclass(frozen=True)
class Each:
    variable: str
    lines: tuple[str, ...]


Node = Line | Each


def parse_body(body: str) -> list[Node]:
    nodes: list[Node] = []
    open_variable: str | None = None
    open_lines: list[str] = []
    for line in body.rstrip().split("\n"):
        if match := _EACH_OPEN.match(line):
            if open_variable is not None:
                raise ValueError("`#each` anidado")
            open_variable, open_lines = match.group(1), []
        elif _EACH_CLOSE.match(line):
            if open_variable is None:
                raise ValueError("`/each` sin `#each`")
            nodes.append(Each(open_variable, tuple(open_lines)))
            open_variable = None
        elif open_variable is not None:
            open_lines.append(line)
        else:
            nodes.append(Line(line))
    if open_variable is not None:
        raise ValueError(f"`#each {open_variable}` sin cerrar")
    for line in _text_lines(nodes):
        if "{{" in _VARIABLE.sub("", line) or "}}" in _VARIABLE.sub("", line):
            raise ValueError(f"marca de variable mal formada: {line.strip()}")
    return nodes


def _text_lines(nodes: Iterable[Node]) -> Iterable[str]:
    for node in nodes:
        yield from (node.lines if isinstance(node, Each) else (node.text,))


def variables(line: str) -> list[str]:
    return [m.group(1) for m in _VARIABLE.finditer(line)]


def substitute(line: str, values: dict[str, str]) -> str:
    return _VARIABLE.sub(lambda m: values[m.group(1)], line)


@dataclass(frozen=True)
class LoadedTemplate:
    template: Template
    version: int
    bodies: dict[str, list[Node]]

    @property
    def id(self) -> str:
        return self.template.id


def check_template(template: Template, stem: str) -> dict[str, list[Node]]:
    problems: list[str] = []
    if template.id != stem:
        problems.append(f"el id `{template.id}` no coincide con el nombre del fichero")
    question_ids = [q.id for q in template.questions]
    if len(question_ids) != len(set(question_ids)):
        problems.append("preguntas duplicadas")
    section_ids = [s.id for s in template.sections]
    if len(section_ids) != len(set(section_ids)):
        problems.append("secciones duplicadas")
    known = clause_index()
    for question in template.questions:
        if question.clause not in known:
            problems.append(f"pregunta {question.id}: cláusula desconocida {question.clause}")
    by_id = {q.id: q for q in template.questions}
    used: dict[str, set[str]] = {}
    for question in template.questions:
        if question.required_if:
            problems += _check_condition(f"pregunta {question.id}", question.required_if, by_id)
            used.setdefault(question.required_if.question, set())
    bodies: dict[str, list[Node]] = {}
    for section in template.sections:
        if section.when:
            problems += _check_condition(f"sección {section.id}", section.when, by_id)
            used.setdefault(section.when.question, set())
        for clause_id in section.covers:
            if clause_id not in known:
                problems.append(f"sección {section.id}: cláusula desconocida {clause_id}")
        try:
            bodies[section.id] = parse_body(section.body)
        except ValueError as exc:
            problems.append(f"sección {section.id}: {exc}")
            continue
        problems += _check_references(section.id, bodies[section.id], by_id, used)
        problems += _check_conditional_use(section, bodies[section.id], by_id)
    for question in template.questions:
        if question.id not in used:
            problems.append(f"pregunta {question.id}: no se usa en ninguna sección")
        elif question.type is AnswerType.RECORDS:
            unused = {f.id for f in question.fields} - used[question.id]
            if unused:
                problems.append(f"pregunta {question.id}: campos sin usar {sorted(unused)}")
    if problems:
        raise TemplateError(stem, problems)
    return bodies


def _check_conditional_use(
    section: Section, nodes: list[Node], by_id: dict[str, Question]
) -> list[str]:
    """Una pregunta que puede quedar vacía solo se usa en secciones que se omiten entonces."""
    names = {node.variable for node in nodes if isinstance(node, Each)} | {
        name for node in nodes if isinstance(node, Line) for name in variables(node.text)
    }
    problems = []
    for name in sorted(names):
        required_if = by_id[name].required_if if name in by_id else None
        when = section.when
        if required_if and not (
            when
            and when.question == required_if.question
            and set(when.any_of) <= set(required_if.any_of)
        ):
            problems.append(
                f"sección {section.id}: usa {name}, que solo es obligatoria si "
                f"{required_if.question} es {' o '.join(required_if.any_of)}; la sección "
                "necesita un `when` igual o más restrictivo"
            )
    return problems


def _check_condition(where: str, condition: Condition, by_id: dict[str, Question]) -> list[str]:
    target = by_id.get(condition.question)
    if target is None:
        return [f"{where}: la condición apunta a una pregunta inexistente {condition.question}"]
    if not target.choices or target.required_if or not target.required:
        return [
            f"{where}: {target.id} debe ser una pregunta de `choices` obligatoria e incondicional"
        ]
    unknown = sorted(set(condition.any_of) - set(target.choices))
    return [f"{where}: valores fuera de las opciones de {target.id}: {unknown}"] if unknown else []


def _check_references(
    section_id: str, nodes: list[Node], by_id: dict[str, Question], used: dict[str, set[str]]
) -> list[str]:
    problems = []
    where = f"sección {section_id}"
    for node in nodes:
        if isinstance(node, Line):
            for name in variables(node.text):
                question = by_id.get(name)
                if question is None:
                    problems.append(f"{where}: variable desconocida {name}")
                elif question.type is not AnswerType.TEXT:
                    problems.append(f"{where}: {name} es {question.type.value}; usa #each")
                else:
                    used.setdefault(name, set())
            continue
        question = by_id.get(node.variable)
        if question is None or question.type is AnswerType.TEXT:
            problems.append(f"{where}: #each {node.variable} no es una lista")
            continue
        fields = used.setdefault(node.variable, set())
        allowed = {f".{f.id}" for f in question.fields} if question.fields else {"."}
        for line in node.lines:
            for name in variables(line):
                if name not in allowed:
                    problems.append(f"{where}: {name} no existe en {node.variable}")
                elif name != ".":
                    fields.add(name[1:])
    return problems


def load_template(file: Traversable) -> LoadedTemplate:
    stem = file.name.removesuffix(".yaml")
    try:
        template = Template.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise TemplateError(stem, [str(exc)]) from None
    return LoadedTemplate(template, TEMPLATES_VERSION, check_template(template, stem))


def check_shared_questions(templates: list[LoadedTemplate]) -> None:
    seen: dict[str, Question] = {}
    problems = []
    for loaded in templates:
        for question in loaded.template.questions:
            if seen.setdefault(question.id, question) != question:
                problems.append(f"pregunta {question.id} definida distinta en {loaded.id}")
    if problems:
        raise TemplateError("plantillas", problems)


def check_engine_contract(templates: Iterable[LoadedTemplate]) -> None:
    """Los ids y opciones que el motor da por hechos existen en las plantillas."""
    by_id = {q.id: q for q in all_questions(templates)}
    problems = [
        f"falta la pregunta {name}, de la que depende el motor"
        for name in (
            *DECLARED_PERSON_QUESTIONS,
            PEOPLE,
            RELATION,
            PROCEDURES_AUTHORITY_QUESTION,
            INCIDENT_LEVELS,
        )
        if name not in by_id
    ]
    if (people := by_id.get(PEOPLE)) and PERSON_NAME not in {f.id for f in people.fields}:
        problems.append(f"{PEOPLE} debe tener el campo {PERSON_NAME}")
    if levels := by_id.get(INCIDENT_LEVELS):
        missing = sorted(set(INCIDENT_LEVEL_FIELDS) - {f.id for f in levels.fields})
        if missing:
            problems.append(f"{INCIDENT_LEVELS} debe tener los campos {missing}")
    if (relation := by_id.get(RELATION)) and SAME_PERSON not in relation.choices:
        problems.append(f"{RELATION} debe ofrecer la opción «{SAME_PERSON}»")
    authority = by_id.get(PROCEDURES_AUTHORITY_QUESTION)
    if authority and set(authority.choices) != set(AUTHORITY_ROLE):
        problems.append(
            f"{PROCEDURES_AUTHORITY_QUESTION}: las opciones deben ser {sorted(AUTHORITY_ROLE)}"
        )
    if problems:
        raise TemplateError("plantillas", problems)


@cache
def load_templates() -> tuple[LoadedTemplate, ...]:
    files = sorted(
        (f for f in TEMPLATES.iterdir() if f.name.endswith(".yaml")), key=lambda f: f.name
    )
    loaded = [load_template(f) for f in files]
    check_shared_questions(loaded)
    check_engine_contract(loaded)
    return tuple(loaded)


def all_questions(templates: Iterable[LoadedTemplate]) -> list[Question]:
    """Preguntas únicas, en orden de primera aparición."""
    questions: dict[str, Question] = {}
    for loaded in templates:
        for question in loaded.template.questions:
            questions.setdefault(question.id, question)
    return list(questions.values())
