"""Registro de incidentes (op.exp.7 y op.exp.9): lista, alta, reportes y cierre.

Las mismas APIs y validaciones que `incident new|update|close|history|list|show`. «Quién»
es un nombre declarado: el registro no admite correos ni otros identificadores personales.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.incidents import (
    ActionKind,
    IncidentRejected,
    IncidentState,
    IncidentStatus,
    Recorded,
    RecordKind,
    TriState,
    check_all_incidents,
    check_incident,
    declared_levels,
    next_incident_id,
    record_history,
    record_incident,
    simulation_procedure,
)
from ens_evidence.incidents.model import INCIDENT_ID_PATTERN, ReviewResult
from ens_evidence.incidents.present import (
    ACTION_LABELS,
    RECORD_LABELS,
    REVIEW_LABELS,
    SIMULATION_NOTE,
    STATUS_LABELS,
    TRISTATE_LABELS,
)
from ens_evidence.incidents.privacy import check_declared_name
from ens_evidence.incidents.verify import IncidentCheck, scan_incident
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.messages import describe_validation
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import NO_DATE, format_instant
from ens_evidence.ui.views.matrix import plain

STEP = "incidentes"
OP_EXP_9 = "op.exp.9"
OP_EXP_7 = "op.exp.7"
NAME_LABEL = "Quién lo registra"
LIST_SPLIT = re.compile(r"[\s,;]+")
_MESSAGE = re.compile(r"^(?P<path>[^:]{1,120}): (?P<text>.+)$")
_INDEX = re.compile(r"\[\d+\]")

FIELD_LABELS = {
    "title": "Título",
    "severity": "Severidad",
    "detected_at": "Cuándo se detectó",
    "assets": "Activos",
    "system": "Sistema de origen",
    "alert_ids": "Ids de alerta",
    "personal_data": "Datos personales",
    "summary": "Análisis",
    "description": "Actuación",
    "evidence_ids": "Evidencias relacionadas",
    "conclusion": "Conclusión",
    "legal_relevance": "Relevancia jurisdiccional",
    "legal_advice": "Asesoramiento legal",
    "preserved_evidence_ids": "Evidencias preservadas",
    "detail": "Detalle de la revisión de eventos auditables",
    "procedure": "Procedimiento",
    "closure": "Cierre",
}
SAVED_TEXTS = {
    "nuevo": "Incidente registrado: es el reporte inicial.",
    "actualizar": "Reporte intermedio registrado.",
    "cerrar": "Incidente cerrado: el reporte final y su historial están registrados.",
    "cerrar-sin-historial": (
        "El cierre está registrado y no se puede repetir, pero el historial no se generó. "
        "Complétalo abajo: los registros no se tocan."
    ),
    "historial": "Historial generado.",
}
PERSONAL_DATA_FIRST = (
    "Antes de cerrar hay que decidir si el incidente afecta a datos personales. "
    "Indícalo en un reporte intermedio (sí o no)."
)
NOTHING_CHANGED = "No has cambiado nada: este reporte repetiría el anterior."
SOURCE_NEEDED = "Con ids de alerta hay que indicar el sistema de origen."
ACTION_NEEDED = "Para registrar la hora o el tipo de una actuación, descríbela."


class Rejected(Exception):
    """Lo que no se pudo hacer, en lenguaje llano y sin valores del formulario."""

    def __init__(self, *messages: str) -> None:
        super().__init__("rechazado")
        self.messages = list(messages)


# --- mensajes en lenguaje llano ------------------------------------------------------------------


def humanize(message: str) -> str:
    """Un mensaje del registro (con rutas de campo) sin jerga ni órdenes de la CLI."""
    found = _MESSAGE.match(message)
    if found:
        field = _INDEX.sub("", re.split(r"[.›]", found["path"].strip())[-1]).strip()
        label = FIELD_LABELS.get(field)
        if label:
            return f"{label}: {plain(found['text']).rstrip('.')}."
    return plain(message)


def humanized(errors: list[str]) -> list[str]:
    return list(dict.fromkeys(humanize(e) for e in errors))


def _rejected_by(exc: IncidentRejected | ValidationError) -> Rejected:
    if isinstance(exc, ValidationError):
        return Rejected(*humanized(describe_validation("incidente", exc)))
    return Rejected(*humanized(exc.errors))


# --- lectura del formulario ----------------------------------------------------------------------


def _by(form: Form) -> str:
    by = first(form, "by")
    if errors := check_declared_name(by, NAME_LABEL):
        raise Rejected(*(humanize(e).replace("--by", NAME_LABEL) for e in errors))
    return by


def _instant(text: str, label: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise Rejected(f"{label}: usa una fecha y hora válidas, como 2026-11-03 09:12.") from None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _optional_instant(form: Form, name: str, label: str) -> datetime | None:
    text = first(form, name)
    return _instant(text, label) if text else None


def _lines(form: Form, name: str) -> list[str]:
    return [item for item in LIST_SPLIT.split(first(form, name)) if item]


def _choice[T](form: Form, name: str, kind: Callable[[str], T], label: str, default: str = "") -> T:
    try:
        return kind(first(form, name) or default)
    except ValueError:
        raise Rejected(f"{label}: elige una de las opciones.") from None


def _scan_of(store: UiStore, incident_id: str) -> IncidentCheck:
    if not re.fullmatch(INCIDENT_ID_PATTERN, incident_id):
        raise HTTPException(404)
    check = check_incident(store, incident_id)
    if not check.scan.records and not check.scan.problems:
        raise HTTPException(404)
    return check


def _last_state(store: UiStore, incident_id: str) -> IncidentState:
    scan = scan_incident(store, incident_id)
    if not scan.records:
        raise Rejected("Este incidente no tiene registros legibles.")
    return scan.records[-1].record.state


def _changed(state: IncidentState, changes: dict[str, Any]) -> IncidentState:
    try:
        return IncidentState.model_validate({**state.model_dump(mode="json"), **changes})
    except ValidationError as exc:
        raise _rejected_by(exc) from None


def _record(
    store: UiStore, now: datetime, state: IncidentState, kind: RecordKind, by: str
) -> Recorded:
    try:
        return record_incident(store, state, kind=kind, by=by, now=now)
    except IncidentRejected as exc:
        raise _rejected_by(exc) from None


# --- escrituras (lo mismo que la CLI) ------------------------------------------------------------


def open_incident(store: UiStore, now: datetime, form: Form) -> Recorded:
    """Lo mismo que `incident new`."""
    by = _by(form)
    title = first(form, "title")
    if not title:
        raise Rejected("Título: escribe un título breve, sin datos personales.")
    detected_text = first(form, "detected_at")
    if not detected_text:
        raise Rejected("Cuándo se detectó: indica la fecha y la hora.")
    detected = _instant(detected_text, "Cuándo se detectó")
    system, alert_ids = first(form, "source_system"), _lines(form, "alert_ids")
    if alert_ids and not system:
        raise Rejected(SOURCE_NEEDED)
    personal_data = _choice(form, "personal_data", TriState, "Datos personales", "unknown")
    simulated = "simulated" in form
    try:
        procedure = simulation_procedure(store) if simulated else None
        incident_id = next_incident_id(store, simulated=simulated, year=detected.year)
    except IncidentRejected as exc:
        raise _rejected_by(exc) from None
    try:
        state = IncidentState.model_validate(
            {
                "id": incident_id,
                "simulated": simulated,
                "title": title,
                "severity": first(form, "severity") or None,
                "detected_at": detected,
                "assets": form.get("assets", []),
                "source": {"system": system, "alert_ids": alert_ids} if system else None,
                "personal_data": personal_data,
                "analysis": {"summary": first(form, "summary") or None},
                "procedure": procedure,
            }
        )
    except ValidationError as exc:
        raise _rejected_by(exc) from None
    return _record(store, now, state, RecordKind.OPENED, by)


def add_report(store: UiStore, now: datetime, incident_id: str, form: Form) -> Recorded:
    """Lo mismo que `incident update`."""
    by = _by(form)
    before = _last_state(store, incident_id)
    action = first(form, "action")
    kind = (
        _choice(form, "action_kind", ActionKind, "Tipo de actuación", "other") if action else None
    )
    at = _optional_instant(form, "action_at", "Cuándo se hizo la actuación")
    if not action and at:
        raise Rejected(ACTION_NEEDED)
    changes: dict[str, Any] = {}
    if severity := first(form, "severity"):
        changes["severity"] = severity
    if "summary" in form and first(form, "summary") != (before.analysis.summary or ""):
        changes["analysis"] = {
            **before.analysis.model_dump(mode="json"),
            "summary": first(form, "summary") or None,
        }
    if assets := form.get("assets", []):
        changes["assets"] = list(dict.fromkeys([*before.assets, *assets]))
    if first(form, "personal_data"):
        changes["personal_data"] = _choice(form, "personal_data", TriState, "Datos personales")
    if action:
        new_action = {"kind": kind, "at": at or now, "description": action}
        changes["actions"] = [*before.model_dump(mode="json")["actions"], new_action]
    if evidence := _lines(form, "evidence"):
        changes["evidence_ids"] = list(dict.fromkeys([*before.evidence_ids, *evidence]))
    after = _changed(before, changes)
    if after == before:
        raise Rejected(NOTHING_CHANGED)
    return _record(store, now, after, RecordKind.UPDATED, by)


def close_incident(store: UiStore, now: datetime, incident_id: str, form: Form) -> Recorded:
    """Lo mismo que `incident close`."""
    by = _by(form)
    before = _last_state(store, incident_id)
    if before.personal_data is TriState.UNKNOWN:
        raise Rejected(PERSONAL_DATA_FIRST)
    conclusion = first(form, "conclusion")
    detail = first(form, "auditable_events_detail")
    missing = [
        message
        for value, message in (
            (conclusion, "Conclusión: escribe cómo se resolvió el incidente."),
            (detail, "Eventos auditables: cuenta qué cambió o por qué no cambia."),
        )
        if not value
    ]
    if missing:
        raise Rejected(*missing)
    closure = {
        "closed_at": now,
        "conclusion": conclusion,
        "legal_relevance": _choice(form, "legal_relevance", TriState, "Relevancia jurisdiccional"),
        "legal_advice": first(form, "legal_advice") or None,
        "preserved_evidence_ids": _lines(form, "preserved_evidence"),
        "auditable_events_review": {
            "result": _choice(form, "auditable_events_result", ReviewResult, "Eventos auditables"),
            "detail": detail,
        },
    }
    after = _changed(before, {"status": IncidentStatus.CLOSED, "closure": closure})
    return _record(store, now, after, RecordKind.CLOSED, by)


def complete_history(store: UiStore, now: datetime, incident_id: str, form: Form) -> None:
    """Lo mismo que `incident history`: no modifica ningún registro."""
    by = _by(form)
    if not scan_incident(store, incident_id).records:
        raise Rejected("Este incidente no tiene registros legibles.")
    try:
        record_history(store, incident_id, by=by, now=now)
    except IncidentRejected as exc:
        raise _rejected_by(exc) from None


# --- lectura -------------------------------------------------------------------------------------


def _list_row(incident_id: str, check: IncidentCheck) -> dict[str, Any]:
    state = check.scan.records[-1].record.state if check.scan.records else None
    return {
        "id": incident_id,
        "simulated": bool(state and state.simulated),
        "broken": bool(check.problems),
        "readable": state is not None,
        "status": STATUS_LABELS[state.status] if state else NO_DATE,
        "closed": bool(state and state.status is IncidentStatus.CLOSED),
        "severity": (state.severity or "Sin severidad") if state else NO_DATE,
        "detected_at": format_instant(state.detected_at) if state else NO_DATE,
        "title": state.title if state else "Sin registros legibles",
    }


def _record_rows(check: IncidentCheck) -> list[dict[str, Any]]:
    scan = check.scan
    return [
        {
            "seq": s.record.seq,
            "kind": RECORD_LABELS[s.record.kind],
            "at": format_instant(s.record.recorded_at),
            "evidence_id": scan.evidence[s.sha256].id if s.sha256 in scan.evidence else None,
        }
        for s in scan.records
    ]


def _closure(state: IncidentState) -> dict[str, Any] | None:
    closure = state.closure
    if closure is None:
        return None
    return {
        "at": format_instant(closure.closed_at),
        "conclusion": closure.conclusion,
        "legal_relevance": TRISTATE_LABELS[closure.legal_relevance],
        "legal_advice": closure.legal_advice,
        "preserved": list(closure.preserved_evidence_ids),
        "events_review": REVIEW_LABELS[closure.auditable_events_review.result],
        "events_detail": closure.auditable_events_review.detail,
    }


def _asset_choices(store: UiStore) -> list[dict[str, str | None]]:
    inventory, _ = load_assets(store)
    return [{"id": a.id, "label": None} for a in inventory.assets] if inventory else []


def _form_context(store: UiStore) -> dict[str, Any]:
    return {
        "asset_choices": _asset_choices(store),
        "levels": declared_levels(store),
        "personal_data_options": [
            {"value": t.value, "label": TRISTATE_LABELS[t]} for t in TriState
        ],
        "action_kinds": [{"value": k.value, "label": ACTION_LABELS[k]} for k in ActionKind],
        "review_results": [{"value": r.value, "label": REVIEW_LABELS[r]} for r in ReviewResult],
    }


def _form_state(request: Request, values: Form | None, errors: list[str] | None) -> dict[str, Any]:
    echo = {k: v[0] for k, v in (values or {}).items() if v}
    return {
        "values": {"by": "", **echo},
        "selected_assets": list((values or {}).get("assets", [])),
        "form_errors": errors or [],
        "errors": {},
    }


def _now_minute(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


async def _read(request: Request) -> Form | Response:
    try:
        return await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")


def _audited(request: Request, store: UiStore, action: str, now: datetime) -> None:
    audit_write(
        identity=identity_of(request).email, step=STEP, action=action, keys=store.written, at=now
    )


# --- páginas -------------------------------------------------------------------------------------


def lista(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    checks = check_all_incidents(store)
    return render(
        request,
        "views/incidentes.html",
        **page_context(request, "incidentes", "Incidentes"),
        rows=[_list_row(i, c) for i, c in sorted(checks.items(), reverse=True)],
        generated_at=format_instant(now),
    )


def _new_page(
    request: Request, *, values: Form | None = None, errors: list[str] | None = None, status=200
) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    state = _form_state(request, values, errors)
    state["values"].setdefault("detected_at", _now_minute(now))
    return render(
        request,
        "views/incidente_nuevo.html",
        status_code=status,
        **page_context(request, "incidentes", "Nuevo incidente"),
        **state,
        **_form_context(store),
        simulation_note=SIMULATION_NOTE,
    )


def nuevo(request: Request) -> Response:
    return _new_page(request)


async def crear(request: Request) -> Response:
    form = await _read(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    try:
        recorded = await run_in_threadpool(open_incident, store, now, form)
    except Rejected as rejected:
        return _new_page(request, values=form, errors=rejected.messages, status=422)
    _audited(request, store, "nuevo", now)
    return RedirectResponse(f"/incidentes/{recorded.record.state.id}?hecho=nuevo", status_code=303)


def _detail_page(
    request: Request,
    incident_id: str,
    *,
    values: Form | None = None,
    errors: list[str] | None = None,
    saved: str | None = None,
    status: int = 200,
) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    check = _scan_of(store, incident_id)
    scan = check.scan
    state = scan.records[-1].record.state if scan.records else None
    closed = bool(state and state.status is IncidentStatus.CLOSED)
    history = scan.histories[0] if len(scan.histories) == 1 else None
    form_state = _form_state(request, values, errors)
    return render(
        request,
        "views/incidente.html",
        status_code=status,
        **page_context(request, "incidentes", incident_id),
        **form_state,
        **_form_context(store),
        incident_id=incident_id,
        readable=state is not None,
        simulated=bool(state and state.simulated),
        simulation_note=SIMULATION_NOTE,
        title=state.title if state else "",
        incident_status=state.status.value if state else "",
        status_label=STATUS_LABELS[state.status] if state else "",
        closed=closed,
        severity=(state.severity or "Sin severidad") if state else "",
        detected_at=format_instant(state.detected_at) if state else NO_DATE,
        personal_data=state.personal_data.value if state else "",
        personal_data_label=TRISTATE_LABELS[state.personal_data] if state else "",
        assets=list(state.assets) if state else [],
        source=(
            {"system": state.source.system, "alert_ids": list(state.source.alert_ids)}
            if state and state.source
            else None
        ),
        procedure=(
            {
                "name": state.procedure.name,
                "approved_at": format_instant(state.procedure.approved_at),
            }
            if state and state.procedure
            else None
        ),
        summary=(state.analysis.summary if state else None),
        actions=[
            {
                "at": format_instant(a.at),
                "kind": ACTION_LABELS[a.kind],
                "description": a.description,
            }
            for a in (state.actions if state else ())
        ],
        evidence_ids=list(state.evidence_ids) if state else [],
        closure=_closure(state) if state else None,
        records=_record_rows(check),
        problems=humanized(list(check.problems)),
        history_id=history.id if history else None,
        history_missing=scan.closed and not scan.histories,
        can_write=not check.problems and not closed,
        saved=SAVED_TEXTS.get(saved or ""),
        op_exp_9=OP_EXP_9,
        op_exp_7=OP_EXP_7,
        generated_at=format_instant(now),
    )


def detalle(request: Request) -> Response:
    return _detail_page(
        request, request.path_params["incidente"], saved=request.query_params.get("hecho")
    )


ACTIONS: dict[str, Callable[[UiStore, datetime, str, Form], Recorded | None]] = {
    "actualizar": add_report,
    "cerrar": close_incident,
    "historial": complete_history,
}


async def escribir(request: Request) -> Response:
    name = request.path_params["accion"]
    if name not in ACTIONS:
        raise HTTPException(404)
    incident_id = request.path_params["incidente"]
    _scan_of(request.app.state.store, incident_id)
    form = await _read(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    try:
        result = await run_in_threadpool(ACTIONS[name], store, now, incident_id, form)
    except Rejected as rejected:
        return _detail_page(request, incident_id, values=form, errors=rejected.messages, status=422)
    _audited(request, store, name, now)
    done = name
    if isinstance(result, Recorded) and name == "cerrar" and not result.history_evidence_id:
        done = "cerrar-sin-historial"
    return RedirectResponse(f"/incidentes/{incident_id}?hecho={done}", status_code=303)


INCIDENT_ROUTES: list[BaseRoute] = [
    Route("/incidentes", lista, methods=["GET"]),
    Route("/incidentes/nuevo", nuevo, methods=["GET"]),
    Route("/incidentes/nuevo", crear, methods=["POST"]),
    Route("/incidentes/{incidente}", detalle, methods=["GET"]),
    Route("/incidentes/{incidente}/{accion}", escribir, methods=["POST"]),
]
