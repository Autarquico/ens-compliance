"""El aviso común de monitorización: independiente de `pages` para que ambas puedan importarlo."""

from typing import Literal, TypedDict

from starlette.requests import Request

from ens_evidence.monitoring.model import Snapshot
from ens_evidence.monitoring.snapshot import load_snapshot
from ens_evidence.workspace.cycle_lock import resolve_state_dir


class Banner(TypedDict):
    kind: Literal["stale", "failing"]
    titles: list[str]


def banner(snapshot: Snapshot | None) -> Banner | None:
    """Datos desactualizados o elementos en fallo; nada si no hay instantánea o todo va bien."""
    if snapshot is None:
        return None
    if snapshot.stale:
        return {"kind": "stale", "titles": []}
    failing = snapshot.failing()
    if not failing:
        return None
    return {"kind": "failing", "titles": [item.title for item in failing]}


def current_snapshot(request: Request) -> Snapshot | None:
    return load_snapshot(resolve_state_dir(), request.app.state.clock())


def banner_for(request: Request) -> Banner | None:
    return banner(current_snapshot(request))
