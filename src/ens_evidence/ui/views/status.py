"""Estado general: dónde estás en el flujo, el siguiente paso y lo que falta. Solo lee."""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.applicability import DeclarationState, declaration_status, validate_applicability
from ens_evidence.assessment import assessment_status
from ens_evidence.assessment.dossier import dossier_status
from ens_evidence.assessment.dossier.store import list_dossiers
from ens_evidence.assessment.render import blocker_lines_by_measure
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix.messages import READINESS_LABELS
from ens_evidence.models.enums import Category, DossierState, Readiness
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.orgdocs import (
    DocumentState,
    all_statuses,
    load_templates,
    read_answers,
    validate_answers,
)
from ens_evidence.preaudit.findings import current_dossier_run
from ens_evidence.risk import RiskState, risk_status, validate_risk
from ens_evidence.risk.models import METHODOLOGY_MD_KEY
from ens_evidence.risk.templates import METHODOLOGY_MD
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.workspace import NotFound

READINESS_NOTICE = (
    "La herramienta no declara conformidad; la valoración es de la persona que revisa."
)
FLOW_COMPLETE = (
    "Has recorrido todo el flujo. Lo que queda es de las personas: revisar, decidir y "
    "mantenerlo al día."
)

_SIGUIENTE_PASO = re.compile(r"\s*Siguiente paso:.*$", re.DOTALL)
_COMMAND = re.compile(
    r"\s*[:(]?\s*(?:ens-evidence\s+)?\b(?:"
    r"matrix (?:review|link|set|unlink|init|status)"
    r"|docs (?:approve|status|build|init)"
    r"|evidence (?:import|verify)"
    r"|incident (?:verify|new|update|close)"
    r"|preaudit (?:review|show)"
    r"|applicability (?:status|propose)"
    r"|catalog show"
    r"|collect [a-z]+"
    r"|check --all"
    r")\b(?:[^.;]|\.(?=\S))*\)?"
)
_LOOSE_END = re.compile(r"\s*(?:\b(?:Comprueba|Revisa|Detalle)\b)?\s*[:,;]?\s*(?=\.|$)")
_MEASURE_ID = re.compile(r"[a-z]{2}\.[a-z]+\.\d")


def plain_text(line: str) -> str:
    """El texto de la CLI sin las órdenes: la web tiene sus propios botones."""
    text = _SIGUIENTE_PASO.sub("", line)
    text = _COMMAND.sub("", text)
    text = _LOOSE_END.sub("", text).strip(" .:;,")
    if not _MEASURE_ID.match(text):
        text = text[:1].upper() + text[1:]
    return text + "."


def blocker_groups(blockers) -> list[dict]:
    """Los bloqueos por medida y sin repetir: el mismo recuento que `assessment status`."""
    return [
        {
            "measure_id": None if measure == "general" else measure,
            "texts": [plain_text(t) for t in texts],
        }
        for measure, texts in blocker_lines_by_measure(blockers).items()
    ]


@dataclass(frozen=True)
class Phase:
    key: str
    label: str
    done: bool
    url: str
    action: str
    """Qué hacer si falta (el texto del enlace del siguiente paso)."""


# --- cada fase calcula su estado real y, si falta, adónde ir ------------------------------------


def _scope(store: UiStore, now: datetime, _: object) -> Phase:
    done = scope_status(store).state is ScopeState.APPROVED
    return Phase("alcance", "Alcance", done, "/asistente/sistema", "Completar el alcance")


def _categorization(store: UiStore, now: datetime, _: object) -> Phase:
    done = categorization_status(store, now).state is CategorizationState.CURRENT
    return Phase(
        "categorizacion", "Categorización", done, "/asistente/informacion",
        "Completar la categorización",
    )  # fmt: skip


def _methodology_adopted(store: UiStore) -> bool:
    try:
        return store.read_text(METHODOLOGY_MD_KEY) != METHODOLOGY_MD
    except NotFound:
        return False


def _risk(store: UiStore, now: datetime, _: object) -> Phase:
    label = "Riesgos"
    if risk_status(store, now).state is RiskState.APPROVED:
        return Phase("riesgos", label, True, "", "")
    if not _methodology_adopted(store):
        return Phase(
            "riesgos", label, False, "/asistente/riesgos-metodologia",
            "Elegir tu metodología de riesgos",
        )  # fmt: skip
    analysis, _errors = validate_risk(store, now)
    if analysis is None or not analysis.register.risks:
        return Phase("riesgos", label, False, "/asistente/riesgos", "Registrar tus riesgos")
    return Phase(
        "riesgos", label, False, "/asistente/aprobacion-riesgos", "Aprobar el análisis de riesgos"
    )


def _applicability(store: UiStore, now: datetime, _: object) -> Phase:
    label = "Aplicabilidad"
    if declaration_status(store, now).state is DeclarationState.CURRENT:
        return Phase("aplicabilidad", label, True, "", "")
    validation = validate_applicability(store, now)
    undecided = validation.measures is None or any(
        m.applicability.value == "UNDETERMINED" or m.proposed for m in validation.measures.measures
    )
    if undecided or validation.errors:
        return Phase(
            "aplicabilidad", label, False, "/asistente/aplicabilidad",
            "Decidir qué medidas te aplican",
        )  # fmt: skip
    return Phase(
        "aplicabilidad", label, False, "/asistente/aprobacion-aplicabilidad",
        "Firmar la Declaración de Aplicabilidad",
    )  # fmt: skip


def _documents(store: UiStore, now: datetime, _: object) -> Phase:
    label = "Documentos"
    statuses = all_statuses(store)
    if all(s.state is DocumentState.APPROVED for s in statuses):
        return Phase("documentos", label, True, "", "")
    try:
        raw = read_answers(store)
    except ValueError:
        raw = None
    report = validate_answers(raw, load_templates())
    if report.pending:
        document = report.pending[0].documents[0]
        return Phase(
            "documentos", label, False, f"/asistente/documentos-{document}",
            "Responder el cuestionario de documentos",
        )  # fmt: skip
    needs_build = any(
        s.state is DocumentState.NOT_GENERATED or (s.state is DocumentState.GENERATED and s.reasons)
        for s in statuses
    )
    if needs_build:
        return Phase(
            "documentos", label, False, "/asistente/documentos-generar", "Generar los documentos"
        )
    return Phase(
        "documentos", label, False, "/asistente/documentos-aprobar", "Aprobar los documentos"
    )


def _matrix(store: UiStore, now: datetime, status) -> Phase:
    label = "Matriz"
    blockers = [
        b for b in status.result.readiness.blockers if b.kind is not BlockerKind.OPEN_FINDING
    ]
    if not blockers and not status.result.errors:
        return Phase("matriz", label, True, "", "")
    first = next((b.measure_id for b in blockers if b.measure_id), None)
    url = f"/matriz/{first}" if first else "/matriz"
    action = f"Revisar la medida {first}" if first else "Revisar la matriz de medidas"
    return Phase("matriz", label, False, url, action)


def _finished(state: DossierState | None, category: Category | None) -> bool:
    return state is DossierState.DECLARED or (
        category is not Category.BASICA and state is DossierState.ANALYZED
    )


def _dossier(store: UiStore, now: datetime, _: object) -> Phase:
    label = "Expediente"
    readable = [d for d in list_dossiers(store) if d.snapshot is not None]
    if not readable:
        return Phase("expediente", label, False, "/expediente", "Generar el expediente")
    latest = readable[-1]
    status = dossier_status(store, latest.id, now=now)
    if _finished(status.state, status.category):
        return Phase("expediente", label, True, "", "")
    if status.drift is not None and not status.drift.current:
        return Phase(
            "expediente", label, False, "/expediente",
            "Generar un expediente nuevo: el último ya no describe el estado actual",
        )  # fmt: skip
    return Phase("expediente", label, False, "/expediente", "Seguir con el expediente")


def _preaudit(store: UiStore, now: datetime, status) -> Phase:
    label = "Pre-auditoría"
    open_findings = any(
        b.kind is BlockerKind.OPEN_FINDING for b in status.result.readiness.blockers
    )
    done = current_dossier_run(store, now) is not None and not open_findings
    return Phase(
        "preauditoria", label, done, "/preauditoria",
        "Resolver los hallazgos de la pre-auditoría" if open_findings else "Pasar la pre-auditoría",
    )  # fmt: skip


PHASES: tuple[Callable, ...] = (
    _scope, _categorization, _risk, _applicability, _documents, _matrix, _dossier, _preaudit,
)  # fmt: skip
PHASE_LABELS = (
    "Alcance", "Categorización", "Riesgos", "Aplicabilidad", "Documentos", "Matriz",
    "Expediente", "Pre-auditoría",
)  # fmt: skip


def phases(store: UiStore, now: datetime, status) -> Iterator[Phase]:
    """Las fases en orden hasta la primera pendiente: las siguientes dependen de ella."""
    for compute in PHASES:
        phase = compute(store, now, status)
        yield phase
        if not phase.done:
            return


def progress(store: UiStore, now: datetime, status) -> tuple[list[dict], Phase | None]:
    reached = list(phases(store, now, status))
    pending = reached[-1] if reached and not reached[-1].done else None
    rows = []
    for index, label in enumerate(PHASE_LABELS):
        if index < len(reached) and reached[index].done:
            state = "done"
        elif pending is not None and index == len(reached) - 1:
            state = "next"
        else:
            state = "todo"
        rows.append({"label": label, "state": state})
    return rows, pending


def estado(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    status = assessment_status(store, now, StoredCheckResults(reference_now=now))
    readiness = status.result.readiness
    groups = blocker_groups(readiness.blockers)
    count = sum(len(g["texts"]) for g in groups)
    measures = len(groups)
    summary = f"Preparación: {READINESS_LABELS[readiness.readiness]}"
    if readiness.readiness is Readiness.NOT_READY:
        summary += f", {count} bloqueo{'' if count == 1 else 's'}"
        summary += f" en {measures} medida{'' if measures == 1 else 's'}" if measures else ""
    categorization = categorization_status(store, now)
    category = categorization.computation.category if categorization.computation else None
    rows, pending = progress(store, now, status)
    return render(
        request,
        "views/estado.html",
        **page_context(request, "estado", "Estado"),
        readiness={
            "state": readiness.readiness.value,
            "summary": f"{summary}. {READINESS_NOTICE}",
        },
        scope_state=scope_status(store).state.value,
        categorization_state=categorization.state.value,
        category=category.value if category else None,
        phases=rows,
        next_step_url=pending.url if pending else None,
        next_step_label=pending.action if pending else FLOW_COMPLETE,
        blockers=groups,
        errors=list(status.result.errors),
        generated_at=format_instant(now),
    )


STATUS_ROUTES: list[BaseRoute] = [Route("/", estado, methods=["GET"])]
