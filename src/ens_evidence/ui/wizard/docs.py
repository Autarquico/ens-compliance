"""Pasos de la documentación organizativa: cuestionario por bloques, generar y aprobar."""

import re
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any, cast

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
    AnswersInvalid,
    AnswerType,
    DocumentApprovalRejected,
    DocumentState,
    Question,
    UnknownDocument,
    all_statuses,
    approve_document,
    build_documents,
    document_names,
    load_templates,
    official_clause,
    validate_answers,
)
from ens_evidence.orgdocs.answers import (
    NOT_APPLICABLE,
    REASON,
    AnswersRejected,
    declared_people,
    set_answers,
    squash,
)
from ens_evidence.orgdocs.approval import ROLE_QUESTION, required_role
from ens_evidence.orgdocs.clauses import UnknownClause
from ens_evidence.orgdocs.contract import (
    DECLARED_PERSON_QUESTIONS,
    RELATION,
    SECURITY_OFFICER,
    SYSTEM_OWNER,
)
from ens_evidence.orgdocs.keys import document_key
from ens_evidence.scope import ScopeNotApproved, ScopeState, scope_status
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.ui.forms import Form, first
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard.base import (
    Context,
    Step,
    StepInput,
    StepRejected,
    declared_name,
    read_yaml,
)
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

GROUP = "documentos"
SCOPE_NOT_APPROVED = "El alcance no está aprobado ahora: apruébalo en el paso «Aprobar el alcance»."
GENERATE_STEP = "documentos-generar"
EXTRA_LIST_ROWS = 3
EXTRA_RECORD_ROWS = 1
SHORT_TEXTS = frozenset({"organizacion", "direccion", *DECLARED_PERSON_QUESTIONS})
LONG_CELLS = frozenset(
    {
        "pasos",
        "criterio",
        "escalado",
        "solicitud",
        "registro",
        "evidencia",
        "verificacion",
        "relacion",
        "integrantes",
    }
)
ROLE_LABELS = {
    Role.MANAGEMENT: "la dirección",
    Role.SECURITY_OFFICER: "el responsable de la seguridad",
    Role.SYSTEM_OWNER: "el responsable del sistema",
}
STATE_LABELS = {
    DocumentState.NOT_GENERATED: "Sin generar",
    DocumentState.GENERATED: "Generado, pendiente de aprobar",
    DocumentState.APPROVED: "Aprobado",
    DocumentState.OBSOLETE: "Aprobación caducada",
}
STATE_TONES = {
    DocumentState.NOT_GENERATED: "neutral",
    DocumentState.GENERATED: "warn",
    DocumentState.APPROVED: "ok",
    DocumentState.OBSOLETE: "warn",
}
POC_HELP_CUT = "Solo se puede no designar POC"
POC_HELP_TAIL = (
    "Solo se puede no designar POC por causa justificada y documentada: "
    "en ese caso marca «No aplica» y explica la causa concreta."
)


@dataclass(frozen=True)
class Block:
    slug: str
    label: str
    title: str
    intro: str
    question_ids: tuple[str, ...]


BLOCKS = (
    Block(
        "documentos-organizacion",
        "Tu organización",
        "Tu organización y su dirección",
        "Cuenta quién eres, para quién trabajas y cómo organizas la seguridad. Con esto se "
        "redacta la política de seguridad. Responde con lo que de verdad hacéis: la "
        "herramienta no inventa nada por ti.",
        (
            "organizacion", "direccion", "mision", "objetivos_seguridad", "marco_legal",
            "riesgos_datos_personales", "comite", "documentacion_estructura",
            "documentacion_gestion", "documentacion_acceso", "difusion_personal",
            "servicios_externalizados", "poc_servicios",
        ),
    ),
    Block(
        "documentos-responsables",
        "Responsables",
        "Responsables y designación",
        "El ENS pide personas concretas con nombre y apellidos para cada responsabilidad. "
        "Declara primero a las personas y luego elige a cada una en su puesto: el nombre "
        "debe coincidir con el de la lista.",
        (
            "personas", "firmante_direccion", "resp_informacion", "resp_informacion_funciones",
            "resp_servicio", "resp_servicio_funciones", "resp_seguridad",
            "resp_seguridad_funciones", "resp_sistema", "resp_sistema_funciones",
            "relacion_seguridad_sistema", "causa_ausencia_recursos", "medidas_compensatorias",
            "designacion_procedimiento", "renovacion_procedimiento", "coordinacion",
            "resolucion_conflictos", "autoridad_procedimientos",
        ),
    ),
    Block(
        "documentos-normativa",
        "Normativa de uso",
        "Normativa de uso para el personal",
        "Las reglas que debe seguir cualquier persona de la organización al usar equipos, "
        "servicios e instalaciones. Escribe una regla concreta por fila.",
        (
            "uso_equipos", "uso_servicios", "uso_instalaciones", "uso_indebido",
            "derechos_personal", "deberes_personal", "medidas_disciplinarias",
        ),
    ),
    Block(
        "documentos-procedimientos",
        "Procedimientos",
        "Procedimientos operativos",
        "Cómo hacéis en la práctica las tareas de seguridad del día a día. Describir un "
        "procedimiento no demuestra que se aplique: para eso hacen falta evidencias, que "
        "irán después en la matriz.",
        (
            "procedimientos", "anomalias_identificacion", "anomalias_comunicacion",
            "anomalias_registro", "analisis_riesgos", "inventario_activos", "gestion_cuentas",
            "credenciales", "actualizaciones", "codigo_danino", "registro_actividad",
            "perimetro", "puesta_en_produccion", "informacion_deberes", "concienciacion",
            "formacion",
        ),
    ),
    Block(
        "documentos-informacion",
        "Información",
        "Cómo tratáis la información",
        "Acceso, almacenamiento, copias, etiquetado y transmisión de la información. Una "
        "fila por procedimiento.",
        (
            "info_acceso", "info_almacenamiento", "info_copias", "info_etiquetado",
            "info_transmision", "info_otras",
        ),
    ),
    Block(
        "documentos-incidentes",
        "Incidentes",
        "Gestión de incidentes",
        "Qué hacéis cuando algo sale mal: detectarlo, clasificarlo, resolverlo, comunicarlo "
        "y aprender de ello. Algunas preguntas dependen de si prestáis servicios a otra "
        "entidad (se responde en el bloque «Tu organización»).",
        (
            "incidentes_alcance", "incidentes_deteccion", "incidentes_informe_eventos",
            "incidentes_niveles", "incidentes_analisis", "incidentes_comunicacion",
            "incidentes_registro", "incidentes_mejora", "incidentes_datos_personales",
            "incibe_quien", "incibe_como", "incibe_cuando", "incidentes_evidencia",
            "incidentes_eventos_auditables",
        ),
    ),
    Block(
        "documentos-autorizaciones",
        "Autorizaciones",
        "Proceso de autorización",
        "Quién autoriza qué antes de usar instalaciones, equipos, aplicaciones, enlaces, "
        "soportes o servicios de terceros. Donde no tengáis nada de eso, marca «No aplica» "
        "y explica por qué.",
        (
            "aut_instalaciones", "aut_instalaciones_alt", "aut_equipos", "aut_criptografia",
            "aut_aplicaciones", "aut_enlaces", "aut_comunicaciones", "aut_comunicaciones_alt",
            "aut_soportes", "aut_moviles", "aut_terceros",
        ),
    ),
)  # fmt: skip
BLOCK_OF = {qid: block for block in BLOCKS for qid in block.question_ids}


@cache
def _questions() -> dict[str, Question]:
    seen: dict[str, Question] = {}
    for loaded in load_templates():
        for question in loaded.template.questions:
            seen.setdefault(question.id, question)
    return seen


@cache
def _titles() -> dict[str, str]:
    return {loaded.id: loaded.template.title for loaded in load_templates()}


# --- lectura de las respuestas ----------------------------------------------------------------


def _stored(store: UiStore) -> dict[str, Any]:
    return read_yaml(store, ANSWERS_KEY)


def _report(store: UiStore):
    return validate_answers(_stored(store), load_templates())


def _visible(question: Question, values: dict) -> bool:
    condition = question.required_if
    if condition is None or values.get(condition.question) in condition.any_of:
        return True
    if condition.question != RELATION:
        return False
    security, system = (squash(str(values.get(q) or "")) for q in (SECURITY_OFFICER, SYSTEM_OWNER))
    return bool(security) and security == system


def _block_questions(block: Block, values: dict, form: Form) -> list[Question]:
    """Las preguntas que tocan; las condicionales se juzgan con lo que el formulario acaba de
    decir, para que una respuesta y lo que depende de ella se puedan guardar de una vez."""
    questions = _questions()
    posted = {
        qid: first(form, f"a~{qid}")
        for qid in block.question_ids
        if f"q~{qid}" in form and questions[qid].type is AnswerType.TEXT
    }
    effective = {**values, **posted}
    return [questions[i] for i in block.question_ids if _visible(questions[i], effective)]


def _plain(message: str) -> str:
    """El mensaje de una API sin ids de pregunta ni órdenes de línea de comandos."""
    for question_id in sorted(_questions(), key=len, reverse=True):
        label = f"«{_questions()[question_id].text}»"
        pattern = (
            rf"\b{question_id}\b"
            if "_" in question_id
            else rf"^{question_id}\b|(?<=declarada en ){question_id}\b"
        )
        message = re.sub(pattern, lambda _, label=label: label, message)
    message = re.sub(r"[;,]? ?(revisa los cambios y )?ejecuta `docs build`", "", message)
    message = message.replace("`docs build`", "el paso «Generar los documentos»")
    return message.replace("`", "").strip()


def _help(question: Question) -> str:
    text = (question.help or "").replace("por línea", "por fila")
    if question.id == "poc_servicios":
        text = text.split(POC_HELP_CUT)[0] + POC_HELP_TAIL
    return text


# --- vista de cada pregunta --------------------------------------------------------------------


def _safe(text: str) -> str:
    return "" if find_secrets({"valor": text}) else text


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _cells(question: Question, values: list[str]) -> list[dict]:
    return [
        {
            "id": spec.id,
            "label": spec.text,
            "long": spec.id in LONG_CELLS,
            "name": f"r~{question.id}~{spec.id}",
            "value": value,
        }
        for spec, value in zip(question.fields, values, strict=True)
    ]


def _blank(question: Question) -> list[str]:
    return [""] * len(question.fields)


def _row_values(question: Question, record: dict) -> list[str]:
    return [str(record.get(spec.id) or "") for spec in question.fields]


def _stored_rows(question: Question, raw: Any) -> list[tuple[list[str], bool]]:
    """Filas guardadas (valores, quitada) y, al final, las vacías para añadir más."""
    rows: list[tuple[list[str], bool]] = []
    if question.type is AnswerType.LIST and isinstance(raw, list):
        rows = [([str(i)], False) for i in raw if isinstance(i, str)]
    elif question.type is AnswerType.RECORDS and isinstance(raw, list):
        rows = [(_row_values(question, r), False) for r in raw if isinstance(r, dict)]
    extra = EXTRA_LIST_ROWS if question.type is AnswerType.LIST else EXTRA_RECORD_ROWS
    return rows + [(_blank(question) if question.fields else [""], False)] * extra


def _posted_rows(question: Question, form: Form) -> list[tuple[list[str], bool]]:
    removed = _removed(question, form)
    if question.type is AnswerType.LIST:
        columns = [form.get(f"a~{question.id}", [])]
    else:
        columns = [form.get(f"r~{question.id}~{spec.id}", []) for spec in question.fields]
    count = max((len(c) for c in columns), default=0)
    rows = [
        ([_safe(c[i].strip()) if i < len(c) else "" for c in columns], i + 1 in removed)
        for i in range(count)
    ]
    if not rows or any(rows[-1][0]):
        rows.append((_blank(question) if question.fields else [""], False))
    return rows


def _removed(question: Question, form: Form) -> set[int]:
    return {int(n) for n in form.get(f"del~{question.id}", []) if n.isdigit()}


def _condition_note(question: Question) -> str | None:
    condition = question.required_if
    if condition is None:
        return None
    target = _questions()[condition.question].text
    return (
        f"Esta pregunta solo aparece si, en «{target}», respondes: {' o '.join(condition.any_of)}."
    )


def _clause_of(question: Question) -> dict[str, str]:
    try:
        clause = official_clause(question.clause)
    except UnknownClause:
        return {"reference": question.clause, "text": ""}
    return {"reference": clause.reference, "text": clause.text}


def _question_view(question: Question, raw: Any, form: Form) -> dict[str, Any]:
    posted = f"q~{question.id}" in form
    na_raw = raw if isinstance(raw, dict) and raw.get(NOT_APPLICABLE) else None
    if posted:
        na = first(form, f"na~{question.id}") == "1"
        reason = _safe(first(form, f"reason~{question.id}"))
    else:
        na = na_raw is not None
        reason = str(na_raw.get(REASON) or "") if na_raw else ""
    if question.type is AnswerType.TEXT:
        value = (
            _safe(first(form, f"a~{question.id}"))
            if posted
            else raw
            if isinstance(raw, str)
            else ""
        )
        rows: list[dict] = []
    else:
        value = ""
        source = _posted_rows(question, form) if posted else _stored_rows(question, raw)
        rows = [
            {
                "n": n,
                "name": f"a~{question.id}",
                "value": values[0],
                "cells": _cells(question, values) if question.fields else [],
                "removed": removed,
                "filled": any(values),
            }
            for n, (values, removed) in enumerate(source, 1)
        ]
    return {
        "id": question.id,
        "name": f"a~{question.id}",
        "text": question.text,
        "help": _help(question),
        "type": question.type.value,
        "choices": question.choices,
        "multiline": question.id not in SHORT_TEXTS,
        "people_list": question.id in DECLARED_PERSON_QUESTIONS,
        "optional": not question.required,
        "clause": _clause_of(question),
        "condition_note": _condition_note(question),
        "allow_na": question.allow_not_applicable,
        "na_label": question.not_applicable_label,
        "na": na,
        "reason": reason,
        "value": value,
        "rows": rows,
    }


# --- pasos de cuestionario ---------------------------------------------------------------------


def _block_context(block: Block, step: StepInput) -> Context:
    raw = _stored(step.store)
    values = _report(step.store).values
    return {
        "block_intro": block.intro,
        "questions": [
            _question_view(q, raw.get(q.id), step.form)
            for q in _block_questions(block, values, step.form)
        ],
        "people": declared_people(values),
    }


def _answer_from_form(question: Question, form: Form) -> Any:
    """Lo que el formulario dice de la pregunta, listo para `set_answer`."""
    if question.allow_not_applicable and first(form, f"na~{question.id}") == "1":
        return {NOT_APPLICABLE: True, REASON: first(form, f"reason~{question.id}")}
    removed = _removed(question, form)
    match question.type:
        case AnswerType.TEXT:
            return first(form, f"a~{question.id}").replace("\r\n", "\n")
        case AnswerType.LIST:
            texts = form.get(f"a~{question.id}", [])
            return [_collapse(t) for n, t in enumerate(texts, 1) if n not in removed and t.strip()]
        case AnswerType.RECORDS:
            rows = [values for values, gone in _posted_rows(question, form) if not gone]
            return [
                dict(zip((s.id for s in question.fields), map(_collapse, values), strict=True))
                for values in rows
                if any(values)
            ]


def _same(new: Any, stored: Any) -> bool:
    if not new and not stored:
        return True
    return new == stored


def _field_messages(question: Question, error: ValueError) -> list[str]:
    text = str(error).replace(f"{question.id}: ", "")
    return [_plain(part) for part in text.split("; ") if part]


def _block_save(block: Block, step: StepInput) -> None:
    raw = _stored(step.store)
    values = _report(step.store).values
    changes = {}
    for question in _block_questions(block, values, step.form):
        if f"q~{question.id}" not in step.form:
            continue
        new = _answer_from_form(question, step.form)
        if not _same(new, raw.get(question.id)):
            changes[question.id] = new
    try:
        set_answers(cast(WorkspaceStore, step.store), changes)
    except AnswersRejected as exc:
        errors = {
            f"a~{question_id}": _field_messages(_questions()[question_id], ValueError(message))
            for question_id, message in exc.by_question.items()
        }
        raise StepRejected(errors, ["Revisa las respuestas marcadas."]) from None


def _block_done(block: Block, store: UiStore, now: datetime) -> bool:
    report = _report(store)
    ids = set(block.question_ids)
    return not any(p.question.id in ids for p in report.pending) and not report.problems_in(ids)


def _block_step(block: Block) -> Step:
    return Step(
        block.slug,
        block.label,
        block.title,
        GROUP,
        "wizard/documentos_bloque.html",
        lambda store, now: _block_done(block, store, now),
        lambda step: _block_context(block, step),
        lambda step: _block_save(block, step),
    )


# --- generar -----------------------------------------------------------------------------------


def _issue_groups(report) -> list[dict[str, Any]]:
    """Lo que falta o está mal, agrupado por bloque del cuestionario."""
    grouped: dict[str, list[str]] = {}
    unplaced: list[str] = []

    def add(question_id: str, text: str) -> None:
        block = BLOCK_OF.get(question_id)
        (grouped.setdefault(block.slug, []) if block else unplaced).append(text)

    for pending in report.pending:
        add(pending.question.id, f"Falta responder: {pending.question.text}")
    for problem in report.problems:
        owner = problem.split(":", 1)[0]
        add(owner, _plain(problem))
    groups = [
        {"slug": b.slug, "label": b.title, "items": grouped[b.slug]}
        for b in BLOCKS
        if b.slug in grouped
    ]
    if unplaced:
        groups.append({"slug": None, "label": "Otros avisos", "items": unplaced})
    return groups


def _document_rows(store: UiStore) -> list[dict[str, Any]]:
    return [
        {
            "name": status.name,
            "title": _titles()[status.name],
            "state": status.state.value,
            "state_label": STATE_LABELS[status.state],
            "tone": STATE_TONES[status.state],
            "reasons": [_plain(r) for r in status.reasons],
        }
        for status in all_statuses(store)
    ]


def _generar_context(step: StepInput) -> Context:
    report = _report(step.store)
    scope_ok = scope_status(step.store).state is ScopeState.APPROVED
    return {
        "scope_ok": scope_ok,
        "answers_started": step.store.exists(ANSWERS_KEY),
        "issue_groups": _issue_groups(report),
        "warnings": [_plain(w) for w in report.warnings],
        "documents": _document_rows(step.store),
        "can_generate": scope_ok and report.valid and step.store.exists(ANSWERS_KEY),
    }


def _generar_save(step: StepInput) -> None:
    if first(step.form, "accion") != "generar":
        return
    if not step.store.exists(ANSWERS_KEY):
        raise StepRejected(
            form_errors=[
                "Todavía no has respondido el cuestionario. Empieza por «Tu organización»."
            ]
        )
    try:
        build_documents(step.store)
    except ScopeNotApproved:
        raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    except AnswersInvalid as exc:
        groups = _issue_groups(exc.report)
        raise StepRejected(
            form_errors=[
                "Aún no se pueden generar los documentos: faltan respuestas o no son válidas.",
                *(f"{g['label']}: {item}" for g in groups for item in g["items"][:3]),
            ]
        ) from None


def _generar_done(store: UiStore, now: datetime) -> bool:
    return all(
        s.state is DocumentState.APPROVED or (s.state is DocumentState.GENERATED and not s.reasons)
        for s in all_statuses(store)
    )


# --- aprobar -----------------------------------------------------------------------------------


def _signatory(role: Role | None, values: dict) -> str:
    holder = values.get(ROLE_QUESTION[role]) if role else None
    return holder if isinstance(holder, str) else ""


def _approval_rows(step: StepInput) -> list[dict[str, Any]]:
    values = _report(step.store).values
    posted = first(step.form, "documento")
    rows = []
    for row, status in zip(_document_rows(step.store), all_statuses(step.store), strict=True):
        role = required_role(row["name"], values)
        approval = status.approval
        rows.append(
            {
                **row,
                "role_label": ROLE_LABELS[role] if role else None,
                "signatory": _signatory(role, values),
                "by": first(step.form, "approved_by") if posted == row["name"] else "",
                "can_approve": status.state in (DocumentState.GENERATED, DocumentState.OBSOLETE),
                "approval": {
                    "approved_by": approval.approved_by,
                    "approved_at": format_instant(approval.approved_at),
                }
                if approval and status.state is DocumentState.APPROVED
                else None,
                "error_key": f"approved_by~{row['name']}",
            }
        )
    return rows


def _viewing(step: StepInput) -> dict[str, str] | None:
    name = step.query.get("ver", "")
    if name not in document_names():
        return None
    try:
        return {
            "name": name,
            "title": _titles()[name],
            "text": step.store.read_text(document_key(name)),
        }
    except (NotFound, NotText):
        return None


def _aprobar_context(step: StepInput) -> Context:
    return {"documents": _approval_rows(step), "viewing": _viewing(step)}


def _aprobar_save(step: StepInput) -> None:
    if first(step.form, "accion") != "aprobar":
        return
    name = first(step.form, "documento")
    if name not in document_names():
        raise StepRejected(form_errors=["No reconocemos ese documento."])
    by = first(step.form, "approved_by")
    declared_name(by, "Quién lo aprueba", f"approved_by~{name}")
    role = required_role(name, _report(step.store).values)
    if role is None:
        raise StepRejected(
            form_errors=[
                "Antes de aprobar este documento di quién aprueba los procedimientos, en el "
                "bloque «Responsables y designación»."
            ]
        )
    try:
        approve_document(step.store, name, by, role, step.now)
    except ScopeNotApproved:
        raise StepRejected(form_errors=[SCOPE_NOT_APPROVED]) from None
    except UnknownDocument:
        raise StepRejected(form_errors=["No reconocemos ese documento."]) from None
    except DocumentApprovalRejected as exc:
        raise StepRejected(form_errors=[_plain(e) for e in exc.errors]) from None


def _aprobar_done(store: UiStore, now: datetime) -> bool:
    return all(s.state is DocumentState.APPROVED for s in all_statuses(store))


DOC_STEPS = (
    *(_block_step(block) for block in BLOCKS),
    Step(
        GENERATE_STEP, "Generar", "Generar los documentos", GROUP, "wizard/documentos_generar.html",
        _generar_done, _generar_context, _generar_save,
    ),
    Step(
        "documentos-aprobar", "Aprobar", "Aprobar los documentos", GROUP,
        "wizard/documentos_aprobar.html", _aprobar_done, _aprobar_context, _aprobar_save,
    ),
)  # fmt: skip
