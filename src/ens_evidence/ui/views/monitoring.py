"""Monitorización: la salud de lo desplegado, leída de la instantánea que escribe el bucle.

Es operación, no cumplimiento: la página solo lee `state/monitoring` (montado de solo lectura en
la UI), no llama a la red y no toca la matriz. Una instantánea desactualizada se pinta entera
como desconocida: nunca se enseña verde un dato viejo.
"""

from collections import Counter
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from ens_evidence.monitoring.model import GROUPS, Item, Snapshot, Status
from ens_evidence.ui.monitoring_banner import current_snapshot
from ens_evidence.ui.pages import page_context, render
from ens_evidence.ui.views import NO_DATE, format_instant

RUNBOOK_URL = "/ayuda/monitorizacion"
GROUP_TITLES = {
    "herramientas": "Herramientas",
    "vps": "Servidor (VPS)",
    "caja": "La caja",
}
STATUS_VIEW = {
    Status.OK: ("ok", "Bien"),
    Status.WARN: ("warn", "Aviso"),
    Status.FAIL: ("bad", "Fallo"),
    Status.UNKNOWN: ("neutral", "Desconocido"),
}
SPARK_STEP = 2
SPARK_HEIGHT = 16


def _spark(item: Item, stale: bool) -> dict[str, Any]:
    bars = []
    for index, point in enumerate(item.series):
        status = Status.UNKNOWN if stale else point.status
        bars.append({"x": index * SPARK_STEP, "tone": STATUS_VIEW[status][0]})
    counts = Counter(bar["tone"] for bar in bars)
    summary = ", ".join(f"{counts[tone]} {label}" for tone, label in _TONE_LABELS if counts[tone])
    return {
        "bars": bars,
        "width": len(bars) * SPARK_STEP,
        "height": SPARK_HEIGHT,
        "summary": summary,
    }


_TONE_LABELS = [(tone, label.lower()) for tone, label in STATUS_VIEW.values()]


def _row(item: Item, stale: bool) -> dict[str, Any]:
    status = Status.UNKNOWN if stale else item.status
    tone, label = STATUS_VIEW[status]
    failing = not stale and status is Status.FAIL
    return {
        "id": item.id,
        "title": item.title,
        "tone": tone,
        "label": label,
        "detail": item.detail,
        "since": format_instant(item.since) if failing and item.since else None,
        "runbook_url": f"/ayuda/{item.runbook}",
        "spark": _spark(item, stale) if item.series else None,
        "observed_at": format_instant(item.observed_at),
    }


def _groups(snapshot: Snapshot) -> list[dict[str, Any]]:
    return [
        {
            "key": name,
            "title": GROUP_TITLES[name],
            "rows": [_row(item, snapshot.stale) for item in snapshot.group(name)],
        }
        for name in GROUPS
    ]


def panel(request: Request) -> Response:
    snapshot = current_snapshot(request)
    context = page_context(request, "monitorizacion", "Monitorización")
    if snapshot is None:
        return render(
            request,
            "views/monitorizacion.html",
            **context,
            snapshot=None,
            runbook_url=RUNBOOK_URL,
        )
    mail = snapshot.mail
    return render(
        request,
        "views/monitorizacion.html",
        **context,
        snapshot=snapshot,
        stale=snapshot.stale,
        generated_at=format_instant(snapshot.generated_at),
        interval_minutes=max(1, snapshot.interval_seconds // 60),
        groups=_groups(snapshot),
        mail_configured=mail.configured,
        mail_last_attempt=format_instant(mail.last_attempt) if mail.last_attempt else NO_DATE,
        mail_last_failed=mail.last_ok is False,
        mail_last_error=mail.last_error,
        runbook_url=RUNBOOK_URL,
    )


MONITORING_ROUTES: list[BaseRoute] = [Route("/monitorizacion", panel, methods=["GET"])]
