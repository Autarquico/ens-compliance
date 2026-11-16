"""Aviso global mientras el último resultado de `cloudflare.ui_access_requires_mfa` no sea PASS.

Solo lectura. Un resultado ausente, ilegible, UNKNOWN, ERROR o FAIL deja el aviso: nunca se da por
hecho el MFA. Con PASS no se muestra nada (la interfaz no afirma que el MFA esté activo).
"""

from dataclasses import dataclass

from starlette.requests import Request

from ens_evidence.checks.store import StoredRun, connector_prefix, scan_runs
from ens_evidence.connectors.cloudflare.checks import UI_ACCESS_REQUIRES_MFA
from ens_evidence.connectors.cloudflare.mfa_checks import (
    APP_NOT_FOUND,
    APP_PARTIAL,
    APP_UNRESOLVED,
    BYPASS,
    HOSTNAME_NOT_CONFIGURED,
    MFA_MISSING,
    NO_ALLOW_POLICY,
    NON_IDENTITY,
    POLICY_UNRESOLVED,
    STALE_EVIDENCE,
)
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


_NO_SECOND_FACTOR = "Hay una política de Access que deja entrar sin segundo factor"
# Cada código que puede dejar el check, dicho para quien no lo conoce; el código va aparte.
_REASONS = {
    NOT_CHECKED: (
        "Todavía no se ha ejecutado la comprobación: recoge `cloudflare` "
        "y ejecuta las comprobaciones"
    ),
    UNREADABLE: "No se ha podido leer el historial de comprobaciones",
    STALE_EVIDENCE: "La recogida es anterior a esta comprobación: vuelve a recoger `cloudflare`",
    HOSTNAME_NOT_CONFIGURED: "Falta `ui_hostname` en `connectors/cloudflare.yaml`",
    APP_NOT_FOUND: "No hay en Access una aplicación para el hostname de esta interfaz",
    APP_PARTIAL: "Ninguna aplicación de Access cubre el hostname entero de esta interfaz",
    APP_UNRESOLVED: "El hostname configurado no es válido o no se pudo resolver",
    POLICY_UNRESOLVED: "No constan las políticas de la aplicación de Access",
    NO_ALLOW_POLICY: "La aplicación de Access no tiene ninguna política que permita entrar",
    NON_IDENTITY: "Hay una política por token de servicio: una persona debe confirmar que es "
    "solo para máquinas",
    MFA_MISSING: _NO_SECOND_FACTOR,
    BYPASS: _NO_SECOND_FACTOR,
    "error": "No se pudo recoger la configuración de Access",
    "unknown": "No se pudo leer parte de la configuración de Access",
    "fail": "Una política de Access no exige segundo factor",
}
_FALLBACK = "La política de Access no se ha podido confirmar"

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
    cached: tuple[tuple[str, ...], MfaState] | None = getattr(request.app.state, "mfa_state", None)
    if cached is None or cached[0] != keys:
        cached = (keys, read_mfa_state(store))
        request.app.state.mfa_state = cached
    return cached[1]


def mfa_notices(request: Request) -> list[dict[str, str]]:
    state = _cached_state(request)
    if state.code is None:
        return []
    result = "" if state.code in (NOT_CHECKED, UNREADABLE) else f"; resultado: {state.status}"
    return [
        {
            "kind": "warn",
            "title": "MFA no confirmada para esta interfaz.",
            "how": (
                f"{_REASONS.get(state.code, _FALLBACK)} (código: {state.code}{result}). "
                "Esto no indica que alguien haya entrado sin MFA: indica que el producto no ha "
                f"podido confirmarlo. Más en {RUNBOOK}."
            ),
        }
    ]
