"""Recogida orquestada: `collect` con aislamiento, reintentos y frontera de secretos.

Cada recogida corre en un subproceso `spawn` con tiempo límite (ADR 0012, plan r2 §5). Todo fallo
del conector deja un `CollectionResult` ERROR generado por el planificador, para que el último
intento sea el que cuente y un PASS anterior no reaparezca. De las excepciones solo se guarda el
tipo, nunca el mensaje; un resultado con secretos no se importa.
"""

import importlib
import multiprocessing
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from multiprocessing.connection import Connection
from typing import Any

from pydantic import BaseModel

from ens_evidence import __version__
from ens_evidence.checks.inputs import config_asset_id
from ens_evidence.checks.registry import CONFIG_MODELS
from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.config import (
    ConnectorConfigError,
    connector_config_key,
    load_connector_config,
)
from ens_evidence.connectors.registry import ENTRY_POINT_GROUP, ConnectorRegistry
from ens_evidence.evidence.importer import import_collected
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scheduling.config import CollectConfig
from ens_evidence.scheduling.lock import WaitClock
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.workspace.factory import open_store
from ens_evidence.workspace.settings import Settings

GENERATED_BY = "scheduler"
STARTUP_SECONDS = 60.0
TERMINATE_GRACE_SECONDS = 5.0
_MAX_PATH = 200
_MAX_PROBLEM = 300
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]")
_CODE = re.compile(r"[a-z_]+(:[A-Za-z0-9_]{1,80})?")


class Phase(StrEnum):
    PLUGIN_LOAD = "plugin_load"
    CONFIG = "config"
    PREPARE = "prepare"
    COLLECT = "collect"
    TIMEOUT = "timeout"
    RESULT_REJECTED = "result_rejected"


# Un fallo de carga, un tiempo agotado o un secreto se repetirían igual: no se reintentan.
_RETRYABLE_PHASES = frozenset({Phase.CONFIG, Phase.PREPARE, Phase.COLLECT})


@dataclass(frozen=True)
class Plugins:
    """Los conectores instalados y los nombres de los entry points que no se pudieron cargar."""

    registry: ConnectorRegistry
    broken: tuple[str, ...] = ()

    def names(self) -> list[str]:
        return sorted({*self.registry.names(), *self.broken})


def load_plugins(found: Iterable[EntryPoint] | None = None) -> Plugins:
    """Carga tolerante: un plugin roto se anota y no impide cargar los demás."""
    registry = ConnectorRegistry()
    failed: list[tuple[str, str]] = []
    for entry_point in entry_points(group=ENTRY_POINT_GROUP) if found is None else found:
        try:
            registry.load_entry_points([entry_point])
        except Exception as error:
            failed.append((entry_point.name, type(error.__cause__ or error).__name__))
    installed = set(registry.names())
    return Plugins(registry, tuple(sorted({n for n, _ in failed if n not in installed})))


@dataclass(frozen=True)
class Attempt:
    """Un intento de recogida. `phase` y `error` solo si lo generó el planificador."""

    number: int
    at: datetime
    status: CollectionStatus
    phase: Phase | None = None
    error: str | None = None


@dataclass(frozen=True)
class CollectOutcome:
    """`result` es lo importado (con `evidence_id`); sin él, `problem` explica por qué no se
    recogió nada (configuración inválida o ausente): el motor de checks ya lo señala."""

    connector: str
    attempts: tuple[Attempt, ...] = ()
    result: CollectionResult | None = None
    evidence_id: str | None = None
    problem: str | None = None

    @property
    def imported(self) -> bool:
        return self.result is not None


@dataclass(frozen=True)
class _Reply:
    result: CollectionResult | None = None
    phase: Phase | None = None
    error: str | None = None
    problem: str | None = None

    def to_message(self) -> tuple[str, ...]:
        if self.result is not None:
            return ("result", self.result.model_dump_json())
        if self.problem is not None:
            return ("invalid", self.problem)
        assert self.phase is not None and self.error is not None
        return ("failed", self.phase.value, self.error)

    @classmethod
    def from_message(cls, message: Any) -> "_Reply":
        try:
            if message[0] == "result":
                return cls(result=CollectionResult.model_validate_json(message[1]))
            if message[0] == "invalid":
                return cls(problem=str(message[1])[:_MAX_PROBLEM])
            error = message[2]
            if not isinstance(error, str) or not _CODE.fullmatch(error):
                raise ValueError
            return cls(phase=Phase(message[1]), error=error)
        except Exception:
            return cls(phase=Phase.RESULT_REJECTED, error="result_invalid")


def _type_name(error: BaseException) -> str:
    return _SAFE_NAME.sub("", type(error).__name__)[:80] or "Exception"


def _crash(phase: Phase | str, type_name: str) -> str:
    return f"{phase}_crashed:{type_name}"


def _run_sequence(
    store: WorkspaceStore, settings: Settings, name: str, connector_class: type[Connector]
) -> _Reply:
    """La secuencia de `cli.collect`; nunca lanza, devuelve la fase y el tipo del fallo."""
    phase = Phase.CONFIG
    try:
        config = load_connector_config(store, name, connector_class.config_model)
        phase = Phase.PLUGIN_LOAD
        connector = connector_class.from_settings(settings)
        phase = Phase.PREPARE
        prepared = connector.prepare(config, store)
        phase = Phase.COLLECT
        result = connector.collect(prepared)
    except WorkspaceError as error:
        if phase is Phase.PREPARE:  # el conector rechaza la configuración frente al workspace
            return _Reply(problem=str(error)[:_MAX_PROBLEM])
        return _Reply(phase=phase, error=_crash(phase, _type_name(error)))
    except Exception as error:
        return _Reply(phase=phase, error=_crash(phase, _type_name(error)))
    if not isinstance(result, CollectionResult):
        return _Reply(phase=Phase.RESULT_REJECTED, error="result_invalid")
    return _Reply(result=result)


def _resolve(module: str, qualname: str) -> type[Connector]:
    target: Any = importlib.import_module(module)
    for part in qualname.split("."):
        target = getattr(target, part)
    return target


def _child_main(
    conn: Connection, settings: Settings, name: str, module: str, qualname: str
) -> None:
    conn.send(("ready",))
    try:
        connector_class = _resolve(module, qualname)
    except Exception as error:
        reply = _Reply(phase=Phase.PLUGIN_LOAD, error=_crash(Phase.PLUGIN_LOAD, _type_name(error)))
    else:
        try:
            store = open_store(settings)
        except Exception as error:
            reply = _Reply(phase=Phase.CONFIG, error=_crash(Phase.CONFIG, _type_name(error)))
        else:
            reply = _run_sequence(store, settings, name, connector_class)
    conn.send(reply.to_message())
    conn.close()


def _receive(receiver: Connection, seconds: float) -> Any:
    if not receiver.poll(seconds):
        raise TimeoutError
    return receiver.recv()


def _stop(process: Any) -> None:
    if process.is_alive():
        process.terminate()
        process.join(TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
    process.join()


def _run_isolated(
    settings: Settings, name: str, connector_class: type[Connector], timeout_seconds: float
) -> _Reply:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child_main,
        args=(sender, settings, name, connector_class.__module__, connector_class.__qualname__),
        daemon=True,
    )
    process.start()
    sender.close()
    started = False
    try:
        _receive(receiver, STARTUP_SECONDS)
        started = True
        return _Reply.from_message(_receive(receiver, timeout_seconds))
    except TimeoutError:
        return _Reply(phase=Phase.TIMEOUT, error="timeout")
    except EOFError:
        phase = Phase.COLLECT if started else Phase.PLUGIN_LOAD
        return _Reply(phase=phase, error=_crash(phase, "ChildExit"))
    finally:
        receiver.close()
        _stop(process)


def _leaked_paths(data: Any, path: str = "") -> list[str]:
    """Rutas de los valores con forma de secreto (claves privadas, tokens, URLs con clave).

    No usa la heurística por nombre de clave de `find_secrets`: marcaría `secret_scanning:
    enabled` de GitHub y rechazaría resultados legítimos.
    """
    if isinstance(data, dict):
        return [p for k, v in data.items() for p in _leaked_paths(v, f"{path}.{k}" if path else k)]
    if isinstance(data, list):
        return [p for i, v in enumerate(data) for p in _leaked_paths(v, f"{path}[{i}]")]
    return find_secrets(data, path) if isinstance(data, str) else []


def _safe_secret_path(path: str) -> str:
    """La ruta del campo con el secreto; si la propia ruta parece un secreto, no se cita."""
    if not path or find_secrets({"path": path}):
        return "<redacted>"
    return path[:_MAX_PATH]


def _synthetic(
    name: str, asset_id: str, at: datetime, phase: Phase, error: str
) -> CollectionResult:
    return CollectionResult(
        source=name,
        collector=name,
        collected_at=at,
        asset_id=asset_id,
        data={"error": error, "generated_by": GENERATED_BY, "phase": phase.value},
        collection_status=CollectionStatus.ERROR,
    )


def _identity_mismatch(result: CollectionResult, name: str, asset_id: str) -> str | None:
    """El primer campo de identidad que no es el del conector (su nombre, no su valor)."""
    for field, found, expected in (
        ("source", result.source, name),
        ("collector", result.collector, name),
        ("asset_id", result.asset_id, asset_id),
    ):
        if found != expected:
            return field
    return None


def _rejection(result: CollectionResult, name: str, asset_id: str) -> str | None:
    """Por qué no se importa el resultado: identidad ajena o secreto (nunca los valores)."""
    if field := _identity_mismatch(result, name, asset_id):
        return f"identity_mismatch:{field}"
    if leaked := _leaked_paths(result.data):
        return f"secret_detected:{_safe_secret_path(leaked[0])}"
    return None


def _attempt(
    store: WorkspaceStore,
    settings: Settings,
    name: str,
    connector_class: type[Connector] | None,
    asset_id: str,
    at: datetime,
    number: int,
    timeout_seconds: float,
    isolated: bool,
) -> tuple[CollectionResult | str, Attempt | None, bool]:
    """(resultado a importar o motivo de configuración inválida, intento, si reintentar)."""
    if connector_class is None:
        reply = _Reply(phase=Phase.PLUGIN_LOAD, error=_crash(Phase.PLUGIN_LOAD, "LoadError"))
    elif isolated:
        reply = _run_isolated(settings, name, connector_class, timeout_seconds)
    else:
        reply = _run_sequence(store, settings, name, connector_class)
    if reply.problem is not None:
        return reply.problem, None, False
    if reply.result is not None:
        if (rejection := _rejection(reply.result, name, asset_id)) is None:
            status = reply.result.collection_status
            return reply.result, Attempt(number, at, status), status is CollectionStatus.ERROR
        reply = _Reply(phase=Phase.RESULT_REJECTED, error=rejection)
    assert reply.phase is not None and reply.error is not None
    result = _synthetic(name, asset_id, at, reply.phase, reply.error)
    attempt = Attempt(number, at, CollectionStatus.ERROR, reply.phase, reply.error)
    return result, attempt, reply.phase in _RETRYABLE_PHASES


def _asset_id(store: WorkspaceStore, name: str, model: type[BaseModel]) -> str:
    return config_asset_id(load_connector_config(store, name, model))


def collect_one(
    store: WorkspaceStore,
    settings: Settings,
    plugins: Plugins,
    name: str,
    now: datetime,
    *,
    clock: WaitClock,
    cfg: CollectConfig | None = None,
    timeout_minutes: float | None = None,
    isolated: bool = True,
) -> CollectOutcome:
    """Recoge `name` e importa el resultado final (los intentos intermedios no se importan).

    `KeyError` si el conector no está instalado ni se conoce. `isolated=False` ejecuta en este
    proceso (sin tiempo límite): para la CLI interactiva y conectores inyectados.
    """
    cfg = cfg or CollectConfig()
    connector_class = None if name in plugins.broken else plugins.registry.get(name)
    model = CONFIG_MODELS.get(name) or (connector_class.config_model if connector_class else None)
    if model is None:
        return CollectOutcome(name, problem="no hay modelo de configuración para este conector")
    try:
        asset_id = _asset_id(store, name, model)
    except ConnectorConfigError as error:
        return CollectOutcome(name, problem=str(error))
    except KeyError:
        return CollectOutcome(name, problem=f"{connector_config_key(name)}: falta asset_id")
    timeout_seconds = (cfg.timeout_minutes if timeout_minutes is None else timeout_minutes) * 60
    attempts: list[Attempt] = []
    for number in range(1, cfg.retries + 2):
        at = now if number == 1 else clock.now()
        result, attempt, retry = _attempt(
            store, settings, name, connector_class, asset_id, at, number, timeout_seconds, isolated
        )
        if isinstance(result, str):
            return CollectOutcome(name, tuple(attempts), problem=result)
        assert attempt is not None
        attempts.append(attempt)
        if not retry or number > cfg.retries:
            break
        clock.sleep(cfg.delay_for(number) * 60)
    manifest = import_collected(store, result, collector_version=__version__, now=clock.now())
    return CollectOutcome(name, tuple(attempts), result, manifest.id)


def collect_all(
    store: WorkspaceStore,
    settings: Settings,
    plugins: Plugins,
    now: datetime,
    cfg: CollectConfig,
    clock: WaitClock,
) -> list[CollectOutcome]:
    """Los conectores instalados con configuración, en orden; uno que falla no para a los demás."""
    outcomes: list[CollectOutcome] = []
    for name in plugins.names():
        if cfg.connectors is not None and name not in cfg.connectors:
            continue
        if not store.exists(connector_config_key(name)):
            continue
        try:
            outcomes.append(collect_one(store, settings, plugins, name, now, clock=clock, cfg=cfg))
        except Exception as error:
            outcomes.append(CollectOutcome(name, problem=f"collect_failed:{_type_name(error)}"))
    return outcomes
