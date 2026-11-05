"""Destinos del banco: repetir una grabación (`ReplayProvider`) o grabar el envío real
(`Recorder`, que envuelve cada cliente en un `RecordingProvider`).

Los dos pasan por `preaudit.service` como cualquier destino (`client_factory`): el banco mide el
producto real y solo sustituye a quién se envía.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ens_evidence.benchmark.model import Recording, RepetitionOutcome
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.adapter import (
    JudgmentConfig,
    JudgmentProvider,
    ProviderError,
    ProviderErrorKind,
)
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.workspace import PreAuditSettings

UNPARSEABLE = ""
"""Lo que devuelve la repetición de un `schema_error`: no pasa el esquema, como lo grabado."""


class RecordingStale(Exception):
    """La grabación ya no corresponde al pack o al payload actuales: hay que regrabar. No es un
    `ProviderError`: aborta el benchmark en vez de contar como fallo del proveedor."""


# --- repetir ---------------------------------------------------------------------------------


class ReplayProvider:
    """Devuelve la respuesta grabada de la repetición `n`, si lo que recibe es lo que se grabó."""

    requires_shell_warning = False

    def __init__(self, recording: Recording, n: int) -> None:
        if not 1 <= n <= len(recording.repetitions):
            raise RecordingStale(f"{recording.case}: no hay repetición {n} grabada")
        self._recording = recording
        self._repetition = recording.repetitions[n - 1]

    def __repr__(self) -> str:
        return f"ReplayProvider({self._recording.case!r}, n={self._repetition.n})"

    @property
    def destination(self) -> str:
        return self._recording.destination

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        recording = self._recording
        if load_pack().sha256 != recording.pack_sha256:
            raise RecordingStale(f"{recording.case}: el audit pack ha cambiado; regraba")
        if hashlib.sha256(payload).hexdigest() != recording.payload_sha256:
            raise RecordingStale(f"{recording.case}: el payload ha cambiado; regraba")
        repetition = self._repetition
        match repetition.outcome:
            case RepetitionOutcome.RESPONSE:
                assert repetition.response is not None, "el modelo lo garantiza"
                return repetition.response
            case RepetitionOutcome.SCHEMA_ERROR:
                return UNPARSEABLE
            case RepetitionOutcome.PROVIDER_ERROR:
                assert repetition.kind is not None, "el modelo lo garantiza"
                raise ProviderError(repetition.kind, replayed_error(repetition.kind))


def replayed_error(kind: ProviderErrorKind) -> str:
    return f"error del proveedor grabado ({kind.value})"


def replay_factory(recording: Recording, n: int) -> service.ClientFactory:
    """La fábrica de `service` que repite la repetición `n` de `recording`."""

    def factory(provider: Provider, settings: PreAuditSettings) -> JudgmentProvider:
        if provider is not recording.provider or settings.model != recording.model:
            raise ProviderError(
                ProviderErrorKind.CONFIG, "la grabación es de otro proveedor o modelo"
            )
        return ReplayProvider(recording, n)

    return factory


# --- grabar ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Captured:
    """Lo que devolvió `judge()`: el texto o, si falló, solo el tipo de error."""

    response: str | None = None
    kind: ProviderErrorKind | None = None


class RecordingProvider:
    """El cliente real, que anota en `captured` lo que devuelve `judge()` y nada más."""

    def __init__(self, inner: JudgmentProvider, captured: list[Captured]) -> None:
        self._inner = inner
        self._captured = captured

    def __repr__(self) -> str:
        return f"RecordingProvider({self._inner!r})"

    @property
    def destination(self) -> str:
        return self._inner.destination

    @property
    def requires_shell_warning(self) -> bool:
        return self._inner.requires_shell_warning

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        try:
            text = self._inner.judge(system_prompt, payload, schema, config)
        except ProviderError as error:
            self._captured.append(Captured(kind=error.kind))
            raise
        self._captured.append(Captured(response=text))
        return text


class Recorder:
    """Fábrica de `service` que envuelve el cliente real (`default_client`, resuelto al llamar,
    o `inner`) en un `RecordingProvider`; `captured` guarda cada envío en orden."""

    def __init__(
        self, inner: Callable[[Provider, PreAuditSettings], JudgmentProvider] | None = None
    ) -> None:
        self._inner = inner
        self.captured: list[Captured] = []

    def __call__(self, provider: Provider, settings: PreAuditSettings) -> JudgmentProvider:
        client = (self._inner or service.default_client)(provider, settings)
        return RecordingProvider(client, self.captured)
