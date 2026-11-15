"""Pre-auditoría por la web: pedir la preparación, consentir el envío y decidir los hallazgos.

El servicio `ui` no prepara ni envía: deja peticiones (`preaudit/requests.py`) que ejecuta `app`.
Las decisiones usan la misma API que `preaudit review`. Los hallazgos son propuestas, no
conclusiones.
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from ens_evidence.assessment.dossier.store import DOSSIER_ID, list_dossiers
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.models.preaudit import (
    NEXT_STATES,
    DispositionState,
    Finding,
    Layer,
    Provider,
    RefKind,
)
from ens_evidence.preaudit.deterministic import RULE_LABELS
from ens_evidence.preaudit.findings import findings_view
from ens_evidence.preaudit.records import PREAUDITS_PREFIX, scan_runs
from ens_evidence.preaudit.requests import (
    RequestRejected,
    RequestStatus,
    State,
    request_prepare,
    request_run,
    requests_of,
)
from ens_evidence.preaudit.review import ReviewRejected, review
from ens_evidence.preaudit.service import PreparedRecord, read_prepared
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.ui.audit import audit_write
from ens_evidence.ui.forms import Form, FormInvalid, FormTooLarge, first, read_form
from ens_evidence.ui.pages import error_page, identity_of, page_context, render
from ens_evidence.ui.plain import translate
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import NO_DATE, format_instant
from ens_evidence.workspace import NotFound

STEP = "preauditoria"
NOT_DISPOSED = "No se dispone"
LAYER_LABELS = {Layer.DETERMINISTIC: "Capa determinista", Layer.JUDGMENT: "Capa de juicio"}
PLAN_RECIPIENT = {Provider.CLAUDE_CODE.value: "Anthropic", Provider.CODEX.value: "OpenAI"}
SHELL_PROVIDERS = {Provider.CODEX.value}
MAX_REQUESTS_SHOWN = 5
SAVED_TEXTS = {
    "preparar": "Petición hecha. El servicio la ejecutará en unos minutos; no se envía nada.",
    "enviar": "Petición hecha. El servicio la enviará en unos minutos con la huella revisada.",
    "decidir": "Decisión registrada.",
}
KIND_LABELS = {"prepare": "Preparar el envío", "run": "Enviar a la revisión automática"}
STATE_LABELS = {
    State.PENDING: "En cola: el servicio la ejecutará en cuanto pueda",
    State.RUNNING: "En curso",
    State.DONE: "Hecha",
    State.ERROR: "No se pudo hacer",
    State.EXPIRED: "Caducada: pasaron 24 horas sin ejecutarse; pídela de nuevo",
}
STATE_TONES = {
    State.PENDING: "warn",
    State.RUNNING: "warn",
    State.DONE: "ok",
    State.ERROR: "bad",
    State.EXPIRED: "neutral",
}
REDACTION_LIMIT = (
    "Los nombres de personas que no constan en ningún registro del workspace no se redactan: "
    "léelo antes de aceptar."
)
SHELL_RISK = (
    "Este destino conserva una herramienta de shell que no se puede quitar y podría leer "
    "ficheros de este equipo. Para enviar tienes que aceptarlo expresamente."
)
DECISIONS = {
    DispositionState.ACCEPTED: ("accepted", "Aceptado: lo reconozco y sigue bloqueando"),
    DispositionState.RESOLVED: ("resolved", "Resuelto: lo corrige una evidencia"),
    DispositionState.DISMISSED: ("dismissed", "Descartado: explico el motivo"),
}
BY_LABEL = "Quién lo hace"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class Rejected(Exception):
    """Lo que no se pudo hacer, en lenguaje llano."""

    def __init__(self, *messages: str, finding: str | None = None) -> None:
        super().__init__("rechazado")
        self.messages = list(messages)
        self.finding = finding


def plain(message: str) -> str:
    """Sin órdenes de CLI en el texto que ve la persona."""
    return translate(message)


# --- lectura -----------------------------------------------------------------------------------


def _dossier_ids(store: UiStore) -> list[str]:
    return [d.id for d in list_dossiers(store) if d.snapshot is not None]


def _chosen(request: Request, ids: list[str]) -> str | None:
    wanted = request.query_params.get("expediente", "")
    if wanted in ids:
        return wanted
    return ids[-1] if ids else None


def latest_prepared(store: UiStore, dossier: str) -> tuple[str, PreparedRecord] | None:
    """La última preparación de `dossier` con su clave; None si no hay o no se puede leer."""
    for key in sorted(store.list(PREAUDITS_PREFIX), reverse=True):
        prepared = read_prepared(store, key)
        if prepared and prepared.dossier == dossier:
            return key, prepared
    return None


def _count(content: dict[str, Any], name: str) -> int:
    items = content.get(name)
    return len(items) if isinstance(items, list) else 0


def _prepared_summary(store: UiStore, found: tuple[str, PreparedRecord]) -> dict[str, Any]:
    key, prepared = found
    provider, sha256 = prepared.provider, prepared.payload_sha256
    counts = {"measures": 0, "documents": 0, "evidence": 0}
    intact = False
    try:
        raw = store.read_bytes(prepared.payload)
        intact = hashlib.sha256(raw).hexdigest() == sha256 and bool(_SHA256.fullmatch(sha256))
        content = json.loads(raw).get("dossier", {}) if intact else {}
        counts = {name: _count(content, name) for name in counts}
    except (ValueError, OSError, AttributeError, NotFound):
        intact = False
    return {
        "key": key,
        "provider": provider,
        "model": prepared.model,
        "destination": prepared.destination,
        "recipient": PLAN_RECIPIENT.get(provider),
        "shell": prepared.requires_shell_warning or provider in SHELL_PROVIDERS,
        "pack_version": prepared.pack_version,
        "prepared_at": prepared.prepared_at,
        "sha256": sha256,
        "intact": intact,
        **counts,
    }


def _request_rows(statuses: list[RequestStatus]) -> list[dict[str, Any]]:
    rows = []
    for item in reversed(statuses[-MAX_REQUESTS_SHOWN:]):
        request, result = item.request, item.result
        rows.append(
            {
                "kind_label": KIND_LABELS[request.kind] if request else "Petición",
                "state": item.state.value,
                "state_label": STATE_LABELS[item.state],
                "tone": STATE_TONES[item.state],
                "at": format_instant(request.requested_at) if request else NO_DATE,
                "by": request.by if request else "",
                "message": plain(result.message) if result and result.message else None,
            }
        )
    return rows


def _criterion(finding: Finding) -> str:
    if finding.layer is Layer.DETERMINISTIC:
        return RULE_LABELS.get(finding.criterion, finding.criterion)
    return finding.criterion


def _finding_row(finding: Finding, view, evidence_options: list[dict[str, str]]) -> dict[str, Any]:
    state = view.state_of(finding.id) if finding.layer is Layer.JUDGMENT else None
    cited = {ref.id for ref in finding.refs if ref.kind is RefKind.EVIDENCE}
    next_states = NEXT_STATES[state.state] if state else frozenset()
    return {
        "id": finding.id,
        "measure_id": finding.measure,
        "classification": finding.proposal.value,
        "disposition": state.state.value if state else NOT_DISPOSED,
        "text": finding.reason,
        "criterion": _criterion(finding),
        "source": finding.source,
        "classification_source": finding.classification_source,
        "layer_label": LAYER_LABELS[finding.layer],
        "decidable": bool(state),
        "closed": bool(state) and not next_states,
        "decisions": [DECISIONS[s] for s in DECISIONS if s in next_states],
        "evidence_options": [o for o in evidence_options if o["id"] not in cited],
    }


def _evidence_options(store: UiStore) -> list[dict[str, str]]:
    return [
        {"id": m.id, "label": f"{m.id} · {format_instant(m.collected_at)}"}
        for m in list_manifests(store).manifests
    ]


def _latest_run(store: UiStore, now: datetime, dossier: str | None) -> dict[str, Any]:
    scan, view = scan_runs(store, now=now), findings_view(store, now)
    empty = {"generated_at": NO_DATE, "layers": {}, "judgment": [], "deterministic": []}
    if scan.problems or view.problems:
        return {**empty, "layers": {"Registro de pre-auditorías": "ERROR"}}
    runs = [r.record for r in scan.records if r.record.run.dossier == dossier]
    if not runs:
        return empty
    record = runs[-1]
    options = _evidence_options(store)
    rows = [_finding_row(f, view, options) for f in record.findings]
    layer_of = {f.id: f.layer for f in record.findings}
    by_measure: dict[str, list[dict[str, Any]]] = {}
    for row in (r for r in rows if layer_of[r["id"]] is Layer.DETERMINISTIC):
        by_measure.setdefault(row["measure_id"], []).append(row)
    return {
        "generated_at": format_instant(record.run.at),
        "layers": {
            LAYER_LABELS[Layer.DETERMINISTIC]: record.run.deterministic_status.value,
            LAYER_LABELS[Layer.JUDGMENT]: record.run.judgment_status.value,
        },
        "judgment": [r for r in rows if layer_of[r["id"]] is Layer.JUDGMENT],
        "deterministic": [
            {"measure_id": m, "findings": fs} for m, fs in sorted(by_measure.items())
        ],
    }


def _page(
    request: Request,
    *,
    form_errors: list[str] | None = None,
    values: dict[str, Any] | None = None,
    error_finding: str | None = None,
    dossier: str | None = None,
    saved: str | None = None,
    status_code: int = 200,
) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    ids = _dossier_ids(store)
    chosen = dossier if dossier in ids else _chosen(request, ids)
    statuses = requests_of(store, chosen, now) if chosen else []
    live = any(item.state.alive for item in statuses)
    stored = latest_prepared(store, chosen) if chosen else None
    prepared = _prepared_summary(store, stored) if stored else None
    return render(
        request,
        "views/preauditoria.html",
        status_code=status_code,
        **page_context(request, "preauditoria", "Pre-auditoría"),
        dossiers=ids,
        dossier_id=chosen,
        requests=_request_rows(statuses),
        has_live_request=live,
        prepared=prepared,
        can_consent=bool(prepared and prepared["intact"] and not live),
        **_latest_run(store, now, chosen),
        redaction_limit=REDACTION_LIMIT,
        shell_risk=SHELL_RISK,
        form_errors=form_errors or [],
        error_finding=error_finding,
        values={"by": "", **(values or {})},
        saved=SAVED_TEXTS.get(saved or ""),
    )


def preauditoria(request: Request) -> Response:
    return _page(request, saved=request.query_params.get("hecho"))


# --- escritura ---------------------------------------------------------------------------------


async def _form(request: Request) -> Form | Response:
    try:
        return await read_form(request)
    except FormTooLarge:
        return error_page(request, 413, "El formulario es demasiado grande.")
    except FormInvalid:
        return error_page(request, 400, "No se ha entendido el formulario.")


def _echo(form: Form) -> dict[str, Any]:
    return {n: v[0] for n, v in form.items() if v and n not in {"confirm_sha256"}}


def _dossier_of(request: Request, form: Form) -> str:
    dossier = first(form, "dossier")
    if not DOSSIER_ID.fullmatch(dossier) or dossier not in _dossier_ids(request.app.state.store):
        raise HTTPException(404)
    return dossier


def _declared_dossier(form: Form) -> str:
    dossier = first(form, "dossier")
    return dossier if DOSSIER_ID.fullmatch(dossier) else ""


def _author(form: Form) -> str:
    by = first(form, "by")
    problems = check_declared_name(by, BY_LABEL)
    if problems:
        raise Rejected(*map(plain, problems))
    return by


def ask_prepare(store: UiStore, now: datetime, dossier: str, by: str, email: str) -> None:
    try:
        request_prepare(store, dossier, by=by, email=email, now=now)
    except RequestRejected as exc:
        raise Rejected(*map(plain, exc.errors)) from None


def ask_run(store: UiStore, now: datetime, dossier: str, form: Form, by: str, email: str) -> None:
    prepared = latest_prepared(store, dossier)
    if prepared is None:
        raise Rejected("Antes hay que preparar el envío y revisar lo que se enviaría.")
    summary = _prepared_summary(store, prepared)
    problems = []
    if "reviewed" not in form:
        problems.append("Marca que has revisado lo que se envía.")
    if first(form, "confirm_sha256").lower() != summary["sha256"]:
        problems.append("La huella no es la de lo que se enviaría; cópiala tal como aparece.")
    shell_accepted = "accept_shell_risk" in form
    if summary["shell"] and not shell_accepted:
        problems.append("Este destino exige aceptar expresamente el riesgo de la shell.")
    if not summary["intact"]:
        problems.append("Lo preparado no se puede verificar; prepáralo de nuevo.")
    if problems:
        raise Rejected(*problems)
    try:
        request_run(
            store,
            dossier,
            payload_sha256=summary["sha256"],
            prepared_key=summary["key"],
            by=by,
            email=email,
            now=now,
            accept_shell_risk=summary["shell"] and shell_accepted,
        )
    except RequestRejected as exc:
        raise Rejected(*map(plain, exc.errors)) from None


def decide(store: UiStore, now: datetime, finding: str, form: Form) -> None:
    by = _author(form)
    wanted = first(form, "decision")
    by_value = {value: state for state, (value, _) in DECISIONS.items()}
    if wanted not in by_value:
        raise Rejected("Elige una decisión.", finding=finding)
    try:
        review(
            store,
            finding,
            by_value[wanted],
            by=by,
            now=now,
            evidence_id=first(form, "evidence") or None,
            reason=first(form, "reason") or None,
        )
    except ReviewRejected as exc:
        raise Rejected(*map(plain, exc.errors), finding=finding) from None


def _audited(request: Request, store: UiStore, action: str, now: datetime) -> None:
    audit_write(
        identity=identity_of(request).email or "",
        step=STEP,
        action=action,
        keys=store.written,
        at=now,
    )


async def _write(request: Request, action: str, work) -> Response:
    form = await _form(request)
    if isinstance(form, Response):
        return form
    store, now = request.app.state.store.recording(), request.app.state.clock()
    dossier = _dossier_of(request, form) if action != "decidir" else _declared_dossier(form)
    try:
        await run_in_threadpool(work, store, now, dossier, form)
    except Rejected as rejected:
        return _page(
            request,
            form_errors=rejected.messages,
            values=_echo(form),
            error_finding=rejected.finding,
            dossier=dossier,
            status_code=422,
        )
    _audited(request, store, action, now)
    where = f"expediente={dossier}&" if dossier else ""
    return RedirectResponse(f"/preauditoria?{where}hecho={action}", status_code=303)


async def preparar(request: Request) -> Response:
    email = identity_of(request).email or ""

    def work(store, now, dossier, form):
        ask_prepare(store, now, dossier, _author(form), email)

    return await _write(request, "preparar", work)


async def enviar(request: Request) -> Response:
    email = identity_of(request).email or ""

    def work(store, now, dossier, form):
        ask_run(store, now, dossier, form, _author(form), email)

    return await _write(request, "enviar", work)


async def decidir(request: Request) -> Response:
    finding = request.path_params["finding"]
    if not _SHA256.fullmatch(finding):
        raise HTTPException(404)

    def work(store, now, dossier, form):
        decide(store, now, finding, form)

    return await _write(request, "decidir", work)


PREAUDIT_ROUTES: list[BaseRoute] = [
    Route("/preauditoria", preauditoria, methods=["GET"]),
    Route("/preauditoria/preparar", preparar, methods=["POST"]),
    Route("/preauditoria/enviar", enviar, methods=["POST"]),
    Route("/preauditoria/hallazgos/{finding}", decidir, methods=["POST"]),
]
