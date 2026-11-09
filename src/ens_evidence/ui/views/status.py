"""Estado general: dónde estás en el flujo, el siguiente paso y lo que falta. Solo lee."""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.assessment import assessment_status
from ens_evidence.assessment.dossier import dossier_status
from ens_evidence.assessment.dossier.store import list_dossiers
from ens_evidence.assessment.render import blocker_lines_by_measure
from ens_evidence.categorization import categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE
from ens_evidence.matrix.messages import READINESS_LABELS
from ens_evidence.models.enums import Category, DossierState, Readiness
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.preaudit.findings import current_dossier_run
from ens_evidence.scope import scope_status
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.plain import translate
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.wizard import STEPS
from ens_evidence.ui.wizard.docs import GROUP

READINESS_NOTICE = (
    "La herramienta no declara conformidad; la valoración es de la persona que revisa."
)
FLOW_COMPLETE = (
    "Has recorrido todo el flujo. Lo que queda es de las personas: revisar, decidir y "
    "mantenerlo al día."
)

_SIGUIENTE_PASO = re.compile(r"\s*Siguiente paso:.*$", re.DOTALL)
_LOOSE_END = re.compile(r"\s*(?:\b(?:Comprueba|Revisa|Detalle)\b)?\s*[:,;]?\s*(?=\.|$)")
_MEASURE_ID = re.compile(r"[a-z]{2}\.[a-z]+\.\d")


def plain_text(line: str) -> str:
    """El texto de la CLI sin las órdenes: la web tiene sus propios botones."""
    text = _SIGUIENTE_PASO.sub("", line)
    text = translate(text)
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


def _wizard_phase(key: str, label: str, group: str) -> Callable[..., Phase]:
    """Una fase del asistente: hecha cuando lo están sus pasos; si no, va al primero que falta."""

    def compute(store: UiStore, now: datetime, _: object) -> Phase:
        required = [s for s in STEPS if s.group == group and not s.optional]
        pending = next((s for s in required if not s.done(store, now)), None)
        if pending is None:
            return Phase(key, label, True, "", "")
        return Phase(key, label, False, f"/asistente/{pending.slug}", pending.title)

    return compute


_scope = _wizard_phase("alcance", "Alcance", "alcance")
_categorization = _wizard_phase("categorizacion", "Categorización", "categorizacion")
_risk = _wizard_phase("riesgos", "Riesgos", "riesgos")
_applicability = _wizard_phase("aplicabilidad", "Aplicabilidad", "aplicabilidad")
_documents = _wizard_phase("documentos", "Documentos", GROUP)


INCIDENT_MEASURE = "op.exp.9"


def _has_incident_history(store: UiStore) -> bool:
    return any(m.source == INCIDENT_HISTORY_SOURCE for m in list_manifests(store).manifests)


def _matrix(store: UiStore, now: datetime, status) -> Phase:
    label = "Matriz"
    blockers = [
        b for b in status.result.readiness.blockers if b.kind is not BlockerKind.OPEN_FINDING
    ]
    if not blockers and not status.result.errors:
        return Phase("matriz", label, True, "", "")
    first = next((b.measure_id for b in blockers if b.measure_id), None)
    if first == INCIDENT_MEASURE and not _has_incident_history(store):
        return Phase(
            "matriz", label, False, "/incidentes",
            "Cerrar un incidente (o un simulacro) para sustentar op.exp.9",
        )  # fmt: skip
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
    measures = sum(1 for g in groups if g["measure_id"])
    summary = f"Preparación: {READINESS_LABELS[readiness.readiness]}"
    if readiness.readiness is Readiness.NOT_READY:
        summary += f", {count} bloqueo{'' if count == 1 else 's'}"
        summary += f" en {measures} medida{'' if measures == 1 else 's'}" if measures else ""
    categorization = categorization_status(store, now)
    category = categorization.computation.category if categorization.computation else None
    rows, pending = progress(store, now, status)
    labels = [r["label"] for r in rows]
    before_applicability = (
        rows[: labels.index("Aplicabilidad")] if "Aplicabilidad" in labels else []
    )
    earlier_phase_pending = any(r["state"] == "next" for r in before_applicability)
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
        earlier_phase_pending=earlier_phase_pending,
        errors=list(status.result.errors),
        generated_at=format_instant(now),
    )


STATUS_ROUTES: list[BaseRoute] = [Route("/", estado, methods=["GET"])]
