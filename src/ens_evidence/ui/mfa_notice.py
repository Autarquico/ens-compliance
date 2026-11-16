"""Aviso global mientras el último resultado de `cloudflare.ui_access_requires_mfa` no sea PASS.

Solo lectura. Un resultado ausente, ilegible, UNKNOWN, ERROR o FAIL deja el aviso: nunca se da por
hecho el MFA. Con PASS no se muestra nada (la interfaz no afirma que el MFA esté activo).
"""

from dataclasses import dataclass

from starlette.requests import Request

from ens_evidence.checks.store import StoredRun, connector_prefix, scan_runs
from ens_evidence.connectors.cloudflare.checks import UI_ACCESS_REQUIRES_MFA
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.store import UiStore

CONNECTOR = "cloudflare"
RUNBOOK = "docs/runbooks/interfaz-web.md, sección 3"
NOT_CHECKED = "not_checked"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class MfaState:
    """`code` es None solo cuando el último resultado es PASS."""

    code: str | None
    status: str


_PASS = MfaState(None, CheckStatus.PASS.value)


def _from_run(last: StoredRun | None) -> MfaState:
    if last is None:
        return MfaState(NOT_CHECKED, "sin resultado")
    ours = [r for r in last.run.results if r.check_id == UI_ACCESS_REQUIRES_MFA]
    if not ours:
        return MfaState(NOT_CHECKED, "sin resultado")
    worst = next((r for r in ours if r.status is not CheckStatus.PASS), None)
    if worst is None:
        return _PASS
    return MfaState(worst.reason_code or worst.status.value.lower(), worst.status.value)


def read_mfa_state(store: UiStore) -> MfaState:
    try:
        scan = scan_runs(store, CONNECTOR)
    except Exception:  # noqa: BLE001 - cualquier fallo de lectura es "no confirmada"
        return MfaState(UNREADABLE, "ilegible")
    if scan.unreadable:
        return MfaState(UNREADABLE, "ilegible")
    live = scan.live(CONNECTOR)
    return _from_run(live[-1] if live else None)


def _cached_state(request: Request) -> MfaState:
    """El historial es inmutable por clave: se relee solo si cambia la lista de claves."""
    store: UiStore = request.app.state.store
    try:
        keys = tuple(store.list(connector_prefix(CONNECTOR)))
    except Exception:  # noqa: BLE001
        return MfaState(UNREADABLE, "ilegible")
    cache: dict[tuple[str, ...], MfaState] = getattr(request.app.state, "mfa_state_cache", {})
    request.app.state.mfa_state_cache = cache
    if keys not in cache:
        cache.clear()
        cache[keys] = read_mfa_state(store)
    return cache[keys]


def mfa_notices(request: Request) -> list[dict[str, str]]:
    state = _cached_state(request)
    if state.code is None:
        return []
    return [
        {
            "kind": "warn",
            "title": "MFA no confirmada para esta interfaz.",
            "how": (
                f"El último resultado de la comprobación de la política de Access es "
                f"{state.status} (motivo: {state.code}). Esto no indica que alguien haya entrado "
                f"sin MFA: indica que el producto no ha podido confirmarlo. Revisa la política y "
                f"repite la comprobación: {RUNBOOK}."
            ),
        }
    ]
