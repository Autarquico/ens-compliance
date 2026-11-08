"""Peticiones de pre-auditoría: la UI las deja, el servicio `app` las ejecuta.

El servicio `ui` no tiene salida a Internet y no prepara ni envía nada: deja una petición en
`evidence/preaudits/requests/<expediente>/<n>.json` y el planificador de `app` la ejecuta bajo el
lock del ciclo con el mismo servicio que la CLI (`service.prepare` / `service.run`).

- `prepare`: expediente, quién y su email de Access.
- `run`: además la huella consentida (`payload_sha256`), la preparación que la muestra
  (`prepared_key`) y `accept_shell_risk` explícito. Solo se crea si esa preparación existe y tiene
  esa huella: con otra, no hay petición.

Todo se escribe con creación exclusiva (`create_bytes`), así que nada se sustituye. Como mucho hay
una petición viva por expediente (pendiente o en curso) y caduca a las 24 h. Estados, derivados de
los ficheros del número `n`:

- `<n>.json`: la petición. Sin nada más: `PENDING` (o `EXPIRED` pasadas 24 h; no se ejecuta).
- `<n>.started.json`: reclamada antes de ejecutar, para que un corte no reenvíe. Sin resultado:
  `RUNNING`; si el ejecutor la encuentra bajo el lock, es de un proceso muerto y se cierra con
  error sin repetirla.
- `<n>.result.json`: `DONE` o `ERROR`, con un mensaje acotado que nunca lleva valores secretos.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, TypeAdapter, ValidationError

from ens_evidence.assessment.dossier.store import DOSSIER_ID
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.preaudit import service
from ens_evidence.preaudit.payload import canonical_json
from ens_evidence.preaudit.records import PREAUDITS_PREFIX
from ens_evidence.privacy_text import check_declared_name, sensitive_problems
from ens_evidence.workspace import AlreadyExists, NotFound, PreAuditSettings, WorkspaceStore

REQUESTS_PREFIX = f"{PREAUDITS_PREFIX}/requests"
REQUEST_FORMAT = "ens-preaudit-request/1"
RESULT_FORMAT = "ens-preaudit-request-result/1"
EXPIRES_AFTER = timedelta(hours=24)
MAX_MESSAGE = 500
_KEY = re.compile(
    rf"{re.escape(REQUESTS_PREFIX)}/(?P<dossier>[^/]+)/(?P<n>\d{{6}})(?P<part>\.started|\.result)?\.json"
)
_PREPARED_KEY = re.compile(
    rf"{service.PREAUDITS_PREFIX}/\d{{8}}T\d{{12}}Z-[0-9a-f]{{12}}/{re.escape(service.PREPARED_NAME)}"
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_INTERRUPTED = (
    "la ejecución se interrumpió antes de registrar su resultado; no se repite por si ya salió "
    "algo del equipo: revisa `preaudit` y vuelve a pedirla"
)


class RequestRejected(Exception):
    """No se crea la petición; `errors` dice por qué (nunca el valor de lo rechazado)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class _Base(Strict):
    format: Literal["ens-preaudit-request/1"] = REQUEST_FORMAT
    dossier: str
    by: str
    email: str
    requested_at: AwareDatetime


class PrepareRequest(_Base):
    kind: Literal["prepare"] = "prepare"


class RunRequest(_Base):
    kind: Literal["run"] = "run"
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    prepared_key: str
    accept_shell_risk: bool


Request = Annotated[PrepareRequest | RunRequest, Field(discriminator="kind")]
_REQUEST = TypeAdapter(Request)


class Outcome(StrEnum):
    DONE = "DONE"
    ERROR = "ERROR"


class RequestResult(Strict):
    format: Literal["ens-preaudit-request-result/1"] = RESULT_FORMAT
    outcome: Outcome
    at: AwareDatetime
    message: str | None = None
    prepared_key: str | None = None
    """Sólo `prepare` hecha: la preparación que deja."""
    run_key: str | None = None
    """Sólo `run` hecha: el registro de la ejecución."""


class State(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    EXPIRED = "EXPIRED"
    DONE = "DONE"
    ERROR = "ERROR"

    @property
    def alive(self) -> bool:
        return self in {State.PENDING, State.RUNNING}


@dataclass(frozen=True)
class RequestStatus:
    key: str
    """Clave de la petición; identifica el trabajo."""
    dossier: str
    request: PrepareRequest | RunRequest | None
    """None si el fichero no se puede leer."""
    state: State
    result: RequestResult | None = None


# --- crear -------------------------------------------------------------------------------------


def request_prepare(
    store: WorkspaceStore, dossier: str, *, by: str, email: str, now: datetime
) -> RequestStatus:
    """Pide preparar el envío de `dossier`. `RequestRejected` si ya hay otra petición viva."""
    _check_identity(dossier, by, email)
    request = PrepareRequest(dossier=dossier, by=by, email=email, requested_at=now)
    return _create(store, request, now)


def request_run(
    store: WorkspaceStore,
    dossier: str,
    *,
    payload_sha256: str,
    prepared_key: str,
    by: str,
    email: str,
    now: datetime,
    accept_shell_risk: bool,
) -> RequestStatus:
    """Pide ejecutar la pre-auditoría con la huella consentida. Sólo si `prepared_key` es una
    preparación de `dossier` con exactamente esa huella."""
    errors = _check_identity_problems(dossier, by, email)
    if not re.fullmatch(SHA256_PATTERN, payload_sha256):
        errors.append("la huella consentida no es un sha256")
    elif not errors and _prepared_sha256(store, prepared_key, dossier) != payload_sha256:
        errors.append("la huella consentida no es la de esa preparación; vuelve a prepararla")
    if errors:
        raise RequestRejected(errors)
    request = RunRequest(
        dossier=dossier,
        by=by,
        email=email,
        requested_at=now,
        payload_sha256=payload_sha256,
        prepared_key=prepared_key,
        accept_shell_risk=accept_shell_risk,
    )
    return _create(store, request, now)


def _check_identity_problems(dossier: str, by: str, email: str) -> list[str]:
    errors = check_declared_name(by)
    if not DOSSIER_ID.fullmatch(dossier):
        errors.append("el expediente no tiene un identificador válido")
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        errors.append("la identidad de Access no es un email")
    return errors


def _check_identity(dossier: str, by: str, email: str) -> None:
    if errors := _check_identity_problems(dossier, by, email):
        raise RequestRejected(errors)


def _prepared_sha256(store: WorkspaceStore, prepared_key: str, dossier: str) -> str | None:
    if not _PREPARED_KEY.fullmatch(prepared_key):
        return None
    try:
        prepared = json.loads(store.read_bytes(prepared_key))
    except (ValueError, NotFound):
        return None
    if not isinstance(prepared, dict) or prepared.get("dossier") != dossier:
        return None
    return prepared.get("payload_sha256")


def _create(
    store: WorkspaceStore, request: PrepareRequest | RunRequest, now: datetime
) -> RequestStatus:
    existing = requests_of(store, request.dossier, now)
    if any(item.state.alive for item in existing):
        raise RequestRejected(
            [f"ya hay una petición de pre-auditoría de {request.dossier} sin terminar"]
        )
    number = len(existing) + 1
    key = _key(request.dossier, number)
    data = canonical_json(request.model_dump(mode="json"))
    try:
        store.create_bytes(key, data)
    except AlreadyExists:
        raise RequestRejected(
            [f"otra petición de {request.dossier} se creó a la vez; revisa y reintenta"]
        ) from None
    return RequestStatus(key, request.dossier, request, State.PENDING)


def _key(dossier: str, number: int, part: str = "") -> str:
    return f"{REQUESTS_PREFIX}/{dossier}/{number:06d}{part}.json"


# --- leer --------------------------------------------------------------------------------------


def requests_of(store: WorkspaceStore, dossier: str, now: datetime) -> list[RequestStatus]:
    return [item for item in list_requests(store, now) if item.dossier == dossier]


def list_requests(store: WorkspaceStore, now: datetime) -> list[RequestStatus]:
    """Todas las peticiones, por expediente y número."""
    keys = set(store.list(REQUESTS_PREFIX))
    found = []
    for key in sorted(keys):
        match = _KEY.fullmatch(key)
        if match is None or match["part"]:
            continue
        found.append(_status(store, key, match["dossier"], int(match["n"]), keys, now))
    return found


def pending_keys(store: WorkspaceStore, now: datetime) -> list[str]:
    """Lo que el planificador debe ejecutar: lo pendiente y lo que quedó a medias de un proceso
    muerto (que se cierra con error). Primero lo más antiguo."""
    live = [item for item in list_requests(store, now) if item.state.alive]
    return [item.key for item in sorted(live, key=_requested_at)]


def _requested_at(item: RequestStatus) -> datetime:
    return item.request.requested_at if item.request else datetime.min.replace(tzinfo=UTC)


def _status(
    store: WorkspaceStore, key: str, dossier: str, number: int, keys: set[str], now: datetime
) -> RequestStatus:
    try:
        request = _REQUEST.validate_json(store.read_bytes(key))
    except (ValidationError, ValueError, NotFound):
        return RequestStatus(key, dossier, None, State.ERROR, _unreadable(now))
    result = _read_result(store, _key(dossier, number, ".result"), keys)
    if result is not None:
        return RequestStatus(key, dossier, request, State(result.outcome.value), result)
    if now - request.requested_at >= EXPIRES_AFTER:
        return RequestStatus(key, dossier, request, State.EXPIRED)
    started = _key(dossier, number, ".started") in keys
    return RequestStatus(key, dossier, request, State.RUNNING if started else State.PENDING)


def _unreadable(now: datetime) -> RequestResult:
    return RequestResult(outcome=Outcome.ERROR, at=now, message="la petición no se puede leer")


def _read_result(store: WorkspaceStore, key: str, keys: set[str]) -> RequestResult | None:
    if key not in keys:
        return None
    try:
        return RequestResult.model_validate_json(store.read_bytes(key))
    except (ValidationError, ValueError, NotFound):
        return RequestResult(
            outcome=Outcome.ERROR, at=datetime.min.replace(tzinfo=UTC), message="resultado ilegible"
        )


# --- ejecutar ----------------------------------------------------------------------------------


def execute(
    store: WorkspaceStore,
    key: str,
    settings: PreAuditSettings | None,
    *,
    now: Callable[[], datetime],
    client_factory: service.ClientFactory | None = None,
) -> RequestResult | None:
    """Ejecuta la petición `key` y registra su resultado; None si ya no está viva (otra orden la
    cerró). El llamador tiene el lock del ciclo: una petición en curso es de un proceso muerto.

    Se reclama con creación exclusiva antes de actuar: un corte entre enviar y registrar no
    repite el envío."""
    match = _KEY.fullmatch(key)
    if match is None or match["part"]:
        return None
    dossier, number = match["dossier"], int(match["n"])
    status = _status(store, key, dossier, number, set(store.list(REQUESTS_PREFIX)), now())
    if not status.state.alive:
        return None
    if status.state is State.RUNNING:
        return _finish(store, dossier, number, _failed(_INTERRUPTED, now()))
    assert status.request is not None
    try:
        store.create_bytes(_key(dossier, number, ".started"), b"{}\n")
    except AlreadyExists:
        return None
    result = _carry_out(store, status.request, settings, now, client_factory)
    return _finish(store, dossier, number, result)


def _carry_out(
    store: WorkspaceStore,
    request: PrepareRequest | RunRequest,
    settings: PreAuditSettings | None,
    now: Callable[[], datetime],
    client_factory: service.ClientFactory | None,
) -> RequestResult:
    try:
        match request:
            case PrepareRequest():
                prepared = service.prepare(
                    store, request.dossier, None, settings, now=now(), client_factory=client_factory
                )
                return RequestResult(outcome=Outcome.DONE, at=now(), prepared_key=prepared.key)
            case RunRequest():
                if (
                    _prepared_sha256(store, request.prepared_key, request.dossier)
                    != request.payload_sha256
                ):
                    return _failed(
                        "la huella consentida no coincide con la preparación; no se envía nada",
                        now(),
                    )
                done = service.run(
                    store,
                    request.dossier,
                    None,
                    request.payload_sha256,
                    request.by,
                    settings=settings,
                    now=now(),
                    accept_shell_risk=request.accept_shell_risk,
                    client_factory=client_factory,
                )
                return RequestResult(outcome=Outcome.DONE, at=now(), run_key=done.key)
    except service.PreAuditRejected as exc:
        return _failed("; ".join(exc.errors), now())
    except service.PreAuditUnconfigured as exc:
        return _failed(str(exc), now())
    except Exception as exc:
        return _failed(f"error inesperado: {type(exc).__name__}", now())


def _failed(message: str, at: datetime) -> RequestResult:
    if sensitive_problems(message):
        message = "el motivo se omite porque parece contener datos sensibles; mira los registros"
    return RequestResult(outcome=Outcome.ERROR, at=at, message=message[:MAX_MESSAGE])


def _finish(
    store: WorkspaceStore, dossier: str, number: int, result: RequestResult
) -> RequestResult | None:
    data = canonical_json(result.model_dump(mode="json"))
    try:
        store.create_bytes(_key(dossier, number, ".result"), data)
    except AlreadyExists:
        return None
    return result
