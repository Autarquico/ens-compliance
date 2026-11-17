"""Peticiones de recogida: la UI las deja, el servicio `app` las ejecuta bajo el lock del ciclo.

El servicio `ui` no recoge: deja `evidence/collect-requests/<conector>/<n>.json` y el planificador
recoge ese conector y evalúa sus checks con el mismo código que el ciclo. Espejo de
`preaudit/requests.py`: todo se escribe con creación exclusiva, como mucho hay una petición viva
por conector y caduca a las 24 h. Estados, derivados de los ficheros del número `n`:

- `<n>.result.json`: `DONE` (aunque la recogida o los checks fallaran: el resultado lo dice).
- `<n>.started.json` sin resultado: `RUNNING`; si el ejecutor la encuentra bajo el lock es de un
  proceso muerto y se cierra con error sin repetirla (la recogida pudo importar ya una evidencia).
- `<n>.json` sin reclamar: `EXPIRED` pasadas 24 h (no se ejecuta); si no, `PENDING`.

El resultado solo lleva enums, códigos y horas, más `evidence_id` y `check_key` (identificadores
con formato canónico): nunca un mensaje, porque el motivo legible de un fallo puede citar un
secreto de un tercero.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, Field, ValidationError

from ens_evidence.checks.engine import OutcomeKind, run_one
from ens_evidence.checks.store import _NAME as _CHECK_RUN_NAME
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.evidence.layout import CHECKS_PREFIX, is_evidence_id
from ens_evidence.models.contracts import Strict
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.preaudit.payload import canonical_json
from ens_evidence.scheduling.collect import Phase, Plugins, collect_one, error_type
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.scope import ScopeNotApproved
from ens_evidence.workspace import AlreadyExists, NotFound, Settings, WorkspaceStore
from ens_evidence.workspace.cycle_lock import Clock

REQUESTS_PREFIX = "evidence/collect-requests"
REQUEST_FORMAT = "ens-collect-request/1"
RESULT_FORMAT = "ens-collect-request-result/1"
EXPIRES_AFTER = timedelta(hours=24)
CONNECTOR_PATTERN = r"^[a-z][a-z0-9-]{0,39}$"
ERROR_TYPE_PATTERN = r"^[A-Za-z0-9_]{1,80}$"
_KEY = re.compile(
    rf"{re.escape(REQUESTS_PREFIX)}/(?P<connector>[a-z][a-z0-9-]{{0,39}})/(?P<n>\d{{6}})"
    r"(?P<part>\.started|\.result)?\.json"
)
_CHECK_KEY = re.compile(
    rf"{re.escape(CHECKS_PREFIX)}/(?P<connector>[a-z][a-z0-9-]{{0,39}})/{_CHECK_RUN_NAME.pattern}"
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")


class RequestRejected(Exception):
    """No se crea la petición; `errors` dice por qué (nunca el valor de lo rechazado)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _evidence_id(value: str | None) -> str | None:
    if value is not None and not is_evidence_id(value):
        raise ValueError("evidence_id no canónico")
    return value


def _check_key(value: str | None) -> str | None:
    if value is not None and _CHECK_KEY.fullmatch(value) is None:
        raise ValueError("check_key no canónica")
    return value


class CollectRequest(Strict):
    format: Literal["ens-collect-request/1"] = REQUEST_FORMAT
    connector: str = Field(pattern=CONNECTOR_PATTERN)
    email: str
    """Identidad de Access de quien la pide (no es una decisión ENS: no hay `by`)."""
    requested_at: AwareDatetime


class Outcome(StrEnum):
    DONE = "DONE"
    ERROR = "ERROR"
    """La petición no pudo ejecutarse. Una recogida en ERROR es DONE: el resultado lo cuenta."""


class ChecksOutcome(StrEnum):
    RECORDED = "RECORDED"
    UNCHANGED = "UNCHANGED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SKIPPED_SCOPE = "SKIPPED_SCOPE"
    HISTORY_UNREADABLE = "HISTORY_UNREADABLE"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


class ErrorCode(StrEnum):
    INTERRUPTED = "INTERRUPTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONNECTOR_UNKNOWN = "CONNECTOR_UNKNOWN"
    CONFIG_INVALID = "CONFIG_INVALID"
    COLLECT_CRASHED = "COLLECT_CRASHED"
    UNREADABLE = "UNREADABLE"


class CollectRequestResult(Strict):
    """Sin texto libre: enums, números, horas y dos identificadores con formato canónico."""

    format: Literal["ens-collect-request-result/1"] = RESULT_FORMAT
    outcome: Outcome
    at: AwareDatetime
    code: ErrorCode | None = None
    """Solo con `outcome=ERROR`."""
    collection_status: CollectionStatus | None = None
    evidence_id: Annotated[str | None, AfterValidator(_evidence_id)] = None
    """`manifest.id` de la evidencia importada."""
    phase: Phase | None = None
    """Fase en la que el planificador sintetizó el ERROR de la recogida."""
    error_type: str | None = Field(default=None, pattern=ERROR_TYPE_PATTERN)
    attempts: int = Field(default=0, ge=0)
    checks: ChecksOutcome = ChecksOutcome.NOT_RUN
    check_key: Annotated[str | None, AfterValidator(_check_key)] = None
    """Clave del registro de checks escrito (solo `RECORDED`)."""


class State(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    EXPIRED = "EXPIRED"

    @property
    def alive(self) -> bool:
        return self in {State.PENDING, State.RUNNING}


@dataclass(frozen=True)
class RequestStatus:
    key: str
    connector: str
    """El de la clave: es la identidad con la que se ejecuta."""
    request: CollectRequest | None
    """None si el fichero no se puede leer o no es de este conector."""
    state: State
    result: CollectRequestResult | None = None


# --- crear -------------------------------------------------------------------------------------


def request_collect(
    store: WorkspaceStore, connector: str, *, email: str, now: datetime
) -> RequestStatus:
    """Pide recoger `connector` y evaluar sus checks. `RequestRejected` si no se puede pedir (nombre
    inválido, sin configurar, identidad que no es un email, o ya hay otra petición viva)."""
    errors = _identity_problems(connector, email)
    if not errors and not store.exists(connector_config_key(connector)):
        errors.append(f"el conector {connector} no está configurado")
    if errors:
        raise RequestRejected(errors)
    existing = requests_of(store, connector, now)
    if any(item.state.alive for item in existing):
        raise RequestRejected([f"ya hay una petición de recogida de {connector} sin terminar"])
    request = CollectRequest(connector=connector, email=email, requested_at=now)
    key = _key(connector, len(existing) + 1)
    try:
        store.create_bytes(key, canonical_json(request.model_dump(mode="json")))
    except AlreadyExists:
        raise RequestRejected(
            [f"otra petición de {connector} se creó a la vez; revisa y reintenta"]
        ) from None
    return RequestStatus(key, connector, request, State.PENDING)


def _identity_problems(connector: str, email: str) -> list[str]:
    errors = []
    if not re.fullmatch(CONNECTOR_PATTERN, connector):
        errors.append("el conector no tiene un nombre válido")
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        errors.append("la identidad de Access no es un email")
    return errors


def _key(connector: str, number: int, part: str = "") -> str:
    return f"{REQUESTS_PREFIX}/{connector}/{number:06d}{part}.json"


# --- leer --------------------------------------------------------------------------------------


def requests_of(store: WorkspaceStore, connector: str, now: datetime) -> list[RequestStatus]:
    """Las peticiones de `connector` por número."""
    if not re.fullmatch(CONNECTOR_PATTERN, connector):
        return []
    return _statuses(store, f"{REQUESTS_PREFIX}/{connector}/", now)


def list_requests(store: WorkspaceStore, now: datetime) -> list[RequestStatus]:
    """Todas las peticiones, por conector y número."""
    return _statuses(store, REQUESTS_PREFIX, now)


def _statuses(store: WorkspaceStore, prefix: str, now: datetime) -> list[RequestStatus]:
    keys = set(store.list(prefix))
    found = []
    for key in sorted(keys):
        match = _KEY.fullmatch(key)
        if match is None or match["part"]:
            continue
        found.append(_status(store, key, match["connector"], int(match["n"]), keys, now))
    return found


def pending_keys(store: WorkspaceStore, now: datetime) -> list[str]:
    """Lo que el planificador debe ejecutar: lo pendiente y lo que quedó a medias de un proceso
    muerto (que se cierra con error). Primero lo más antiguo."""
    live = [item for item in list_requests(store, now) if item.state.alive]
    return [item.key for item in sorted(live, key=_requested_at)]


def _requested_at(item: RequestStatus) -> datetime:
    assert item.request is not None  # una viva siempre se leyó
    return item.request.requested_at


def _status(
    store: WorkspaceStore, key: str, connector: str, number: int, keys: set[str], now: datetime
) -> RequestStatus:
    try:
        request = CollectRequest.model_validate_json(store.read_bytes(key))
    except (ValidationError, ValueError, NotFound):
        return RequestStatus(key, connector, None, State.DONE, _unreadable(now))
    if request.connector != connector:
        return RequestStatus(key, connector, None, State.DONE, _unreadable(now))
    result = _read_result(store, connector, _key(connector, number, ".result"), keys)
    if result is not None:
        return RequestStatus(key, connector, request, State.DONE, result)
    if _key(connector, number, ".started") in keys:
        return RequestStatus(key, connector, request, State.RUNNING)
    if now - request.requested_at >= EXPIRES_AFTER:
        return RequestStatus(key, connector, request, State.EXPIRED)
    return RequestStatus(key, connector, request, State.PENDING)


def _unreadable(at: datetime) -> CollectRequestResult:
    return CollectRequestResult(outcome=Outcome.ERROR, at=at, code=ErrorCode.UNREADABLE)


def _read_result(
    store: WorkspaceStore, connector: str, key: str, keys: set[str]
) -> CollectRequestResult | None:
    if key not in keys:
        return None
    try:
        result = CollectRequestResult.model_validate_json(store.read_bytes(key))
        _check_ownership(result, connector)
    except (ValidationError, ValueError, NotFound):
        return _unreadable(datetime.min.replace(tzinfo=UTC))
    return result


def _check_ownership(result: CollectRequestResult, connector: str) -> None:
    if result.check_key is not None:
        match = _CHECK_KEY.fullmatch(result.check_key)
        if match is None or match["connector"] != connector:
            raise ValueError("check_key de otro conector")


# --- ejecutar ----------------------------------------------------------------------------------


def execute(
    store: WorkspaceStore,
    settings: Settings,
    key: str,
    *,
    plugins: Plugins,
    cfg: CollectConfig,
    clock: Clock,
) -> CollectRequestResult | None:
    """Ejecuta la petición `key` y registra su resultado; None si ya no está viva (otra orden la
    cerró). El llamador tiene el lock del ciclo: una petición en curso es de un proceso muerto.

    Se reclama con creación exclusiva antes de actuar. Recoge un solo conector (el de la clave) con
    un intento (`retries=0`: los reintentos del diario esperan minutos con el lock tomado) y el
    tiempo límite de `cfg`; los checks se evalúan siempre que el conector tenga configuración.
    Un fallo de la recogida o de los checks queda en el resultado; uno al escribir el resultado se
    propaga."""
    match = _KEY.fullmatch(key)
    if match is None or match["part"]:
        return None
    connector, number = match["connector"], int(match["n"])
    status = _status(store, key, connector, number, set(store.list(REQUESTS_PREFIX)), clock.now())
    if not status.state.alive:
        return None
    if status.state is State.RUNNING:
        return _finish(store, connector, number, _failed(ErrorCode.INTERRUPTED, clock.now()))
    try:
        store.create_bytes(_key(connector, number, ".started"), b"{}\n")
    except AlreadyExists:
        return None
    result = _carry_out(store, settings, connector, plugins, cfg, clock)
    return _finish(store, connector, number, result)


def _carry_out(
    store: WorkspaceStore,
    settings: Settings,
    connector: str,
    plugins: Plugins,
    cfg: CollectConfig,
    clock: Clock,
) -> CollectRequestResult:
    if not store.exists(connector_config_key(connector)):
        return _failed(ErrorCode.NOT_CONFIGURED, clock.now())
    collected = _collect(store, settings, connector, plugins, cfg, clock)
    checked = collected.model_dump() | _run_checks(store, connector, clock) | {"at": clock.now()}
    return CollectRequestResult.model_validate(checked)


def _collect(
    store: WorkspaceStore,
    settings: Settings,
    connector: str,
    plugins: Plugins,
    cfg: CollectConfig,
    clock: Clock,
) -> CollectRequestResult:
    """La recogida y nada más: nunca lanza (salvo la parada del planificador)."""
    if connector not in plugins.names():
        return _failed(ErrorCode.CONNECTOR_UNKNOWN, clock.now())
    try:
        outcome = collect_one(
            store,
            settings,
            plugins,
            connector,
            clock.now(),
            clock=clock,
            cfg=cfg.model_copy(update={"retries": 0}),
        )
    except KeyError:
        return _failed(ErrorCode.CONNECTOR_UNKNOWN, clock.now())
    except Exception:
        return _failed(ErrorCode.COLLECT_CRASHED, clock.now())
    if outcome.result is None:  # configuración inválida: el motivo no se guarda
        return _failed(ErrorCode.CONFIG_INVALID, clock.now())
    last = outcome.attempts[-1]
    return CollectRequestResult(
        outcome=Outcome.DONE,
        at=clock.now(),
        collection_status=outcome.result.collection_status,
        evidence_id=outcome.evidence_id,
        phase=last.phase,
        error_type=_error_type(last.error),
        attempts=len(outcome.attempts),
    )


def _error_type(code: str | None) -> str | None:
    kind = error_type(code) if code else None
    return kind if kind and re.fullmatch(ERROR_TYPE_PATTERN, kind) else None


def _run_checks(store: WorkspaceStore, connector: str, clock: Clock) -> dict[str, object]:
    """Los checks tras la recogida, con su propia frontera de error: lo recogido se conserva."""
    try:
        outcome = run_one(store, connector, clock.now())
    except ScopeNotApproved:
        return {"checks": ChecksOutcome.SKIPPED_SCOPE}
    except Exception:
        return {"checks": ChecksOutcome.ERROR}
    if outcome is None:
        return {"checks": ChecksOutcome.NOT_APPLICABLE}
    if outcome.kind is OutcomeKind.NOT_CONFIGURED:
        return {"checks": ChecksOutcome.NOT_RUN}
    return {"checks": ChecksOutcome(outcome.kind.name), "check_key": outcome.key}


def _failed(code: ErrorCode, at: datetime) -> CollectRequestResult:
    return CollectRequestResult(outcome=Outcome.ERROR, at=at, code=code)


def _finish(
    store: WorkspaceStore, connector: str, number: int, result: CollectRequestResult
) -> CollectRequestResult | None:
    _check_ownership(result, connector)
    data = canonical_json(result.model_dump(mode="json"))
    try:
        store.create_bytes(_key(connector, number, ".result"), data)
    except AlreadyExists:
        return None
    return result
