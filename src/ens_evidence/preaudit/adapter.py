"""Adaptador de la capa de juicio: un `JudgmentProvider` y la validación de su respuesta.

El proveedor recibe el prompt de sistema del pack, el payload (bytes JSON canónicos, ya
minimizados y redactados) y el esquema, y devuelve el texto JSON en bruto. Nada de eso se guarda
ni se registra: este módulo no escribe logs, y los errores llevan mensajes fijos por tipo de fallo.

Una respuesta que no cumple el esquema deja toda la capa en `ERROR` y sin hallazgos. Una que lo
cumple pasa por la validación semántica (`validate.py`), que descarta hallazgos uno a uno.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from ens_evidence.models.preaudit import Finding, LayerStatus
from ens_evidence.preaudit.pack import AuditPack
from ens_evidence.preaudit.validate import validate_findings

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_TOKENS = 8192


class ProviderErrorKind(StrEnum):
    CONFIG = "CONFIG"
    CLI_MISSING = "CLI_MISSING"
    TIMEOUT = "TIMEOUT"
    TRANSPORT = "TRANSPORT"
    REDIRECT = "REDIRECT"
    HTTP = "HTTP"
    RESPONSE = "RESPONSE"


class ProviderError(Exception):
    """Fallo del proveedor. El mensaje es fijo: nunca incluye payload, respuesta ni clave."""

    def __init__(self, kind: ProviderErrorKind, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class JudgmentConfig:
    """Lo elige quien configura: el modelo no tiene valor por defecto.

    `temperature=None` no envía el parámetro (los modelos que solo admiten el valor por defecto
    rechazan cualquier otro; las CLIs no lo exponen)."""

    model: str
    temperature: float | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ProviderError(ProviderErrorKind.CONFIG, "indica el modelo del proveedor")
        if self.timeout <= 0:
            raise ProviderError(ProviderErrorKind.CONFIG, "el timeout debe ser positivo")
        if self.temperature is not None and self.temperature < 0:
            raise ProviderError(ProviderErrorKind.CONFIG, "la temperatura no puede ser negativa")


class JudgmentProvider(Protocol):
    """Un destino de la capa de juicio.

    `destination` identifica a dónde va el payload (URL base normalizada, o CLI y su versión): va
    en el consentimiento. `requires_shell_warning` indica que el destino conserva una herramienta
    de shell que no se puede quitar, y el consentimiento debe avisarlo."""

    @property
    def destination(self) -> str: ...

    @property
    def requires_shell_warning(self) -> bool: ...

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        """Texto JSON en bruto de la respuesta, o `ProviderError`."""
        ...


# Equivalente pydantic de `audit_pack/v1/response.schema.json` (un test vigila que no derive).
class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CitedItem(_Response):
    ref: str
    quote: str


class ResponseFinding(_Response):
    criterion: str
    measure: str
    proposal: Literal["NC_MAYOR", "NC_MENOR", "OBSERVACION"]
    documents: list[CitedItem]
    evidence: list[CitedItem]
    reason: str


class JudgmentResponse(_Response):
    findings: list[ResponseFinding]


@dataclass(frozen=True)
class JudgmentOutcome:
    status: LayerStatus
    findings: tuple[Finding, ...] = ()
    discarded: int = 0
    error: str | None = None


PAYLOAD_ERROR = "el payload preparado no tiene la forma esperada; capa de juicio sin hallazgos"
SCHEMA_ERROR = "la respuesta del proveedor no cumple el esquema; capa de juicio sin hallazgos"


def parse_response(raw: str) -> JudgmentResponse | None:
    """La respuesta validada contra el esquema, o None. Sin detalle: el error de pydantic cita
    fragmentos de la entrada."""
    try:
        return JudgmentResponse.model_validate_json(raw)
    except ValidationError:
        return None


def run_judgment(
    provider: JudgmentProvider, pack: AuditPack, payload: bytes, config: JudgmentConfig
) -> JudgmentOutcome:
    try:
        raw = provider.judge(pack.system_prompt, payload, pack.response_schema, config)
    except ProviderError as error:
        return JudgmentOutcome(LayerStatus.ERROR, error=error.message)
    response = parse_response(raw)
    if response is None:
        return JudgmentOutcome(LayerStatus.ERROR, error=SCHEMA_ERROR)
    try:
        validation = validate_findings(pack, json.loads(payload), response)
    except ValueError:
        return JudgmentOutcome(LayerStatus.ERROR, error=PAYLOAD_ERROR)
    return JudgmentOutcome(
        LayerStatus.DONE, findings=tuple(validation.findings), discarded=validation.discarded
    )
