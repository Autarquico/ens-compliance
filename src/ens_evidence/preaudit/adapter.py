"""Adaptador de la capa de juicio: un `JudgmentProvider` y la validación de su respuesta.

El proveedor recibe el prompt de sistema del pack, el payload (bytes JSON canónicos, ya
minimizados y redactados) y el esquema, y devuelve el texto JSON en bruto. Nada de eso se guarda
ni se registra: este módulo no escribe logs, y los errores llevan mensajes fijos por tipo de fallo.

Una respuesta que no cumple el esquema deja toda la capa en `ERROR` y sin hallazgos. Una que lo
cumple pierde antes los hallazgos con texto que no se puede mostrar ni guardar tal cual
(caracteres de control o longitud desmedida) y pasa por la validación semántica (`validate.py`),
que descarta hallazgos uno a uno.
"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from ens_evidence.models.preaudit import Finding, LayerStatus
from ens_evidence.preaudit.pack import AuditPack
from ens_evidence.preaudit.validate import CitedItem as CitedItem
from ens_evidence.preaudit.validate import JudgmentResponse, validate_findings
from ens_evidence.preaudit.validate import ResponseFinding as ResponseFinding

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_TOKENS = 8192
MAX_REASON_CHARS = 2000
MAX_QUOTE_CHARS = 1000
# C0 salvo tabulador y salto de línea, DEL y C1: secuencias que manipularían el terminal de quien
# revisa (`preaudit show`).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


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
    """Lo elige quien configura: el modelo no tiene valor por defecto. Sin temperatura: los
    modelos recientes solo admiten la suya y las CLIs no la exponen."""

    model: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ProviderError(ProviderErrorKind.CONFIG, "indica el modelo del proveedor")
        if self.timeout <= 0:
            raise ProviderError(ProviderErrorKind.CONFIG, "el timeout debe ser positivo")


class JudgmentProvider(Protocol):
    """Un destino de la capa de juicio.

    `destination` identifica a dónde va el payload (`<proveedor> <URL base>`, o la CLI con su
    versión y su ejecutable): va tal cual en el consentimiento. `requires_shell_warning` indica
    que el destino conserva una herramienta de shell que no se puede quitar, y el consentimiento
    debe avisarlo."""

    @property
    def destination(self) -> str: ...

    @property
    def requires_shell_warning(self) -> bool: ...

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        """Texto JSON en bruto de la respuesta, o `ProviderError`."""
        ...


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
    displayable = [f for f in response.findings if _displayable(f)]
    unsafe = len(response.findings) - len(displayable)
    try:
        validation = validate_findings(
            pack, json.loads(payload), response.model_copy(update={"findings": displayable})
        )
    except ValueError:
        return JudgmentOutcome(LayerStatus.ERROR, error=PAYLOAD_ERROR)
    return JudgmentOutcome(
        LayerStatus.DONE,
        findings=tuple(validation.findings),
        discarded=validation.discarded + unsafe,
    )


def _displayable(finding: ResponseFinding) -> bool:
    cited = (*finding.documents, *finding.evidence, *finding.justifications)
    quotes = [item.quote for item in cited]
    return (
        len(finding.reason) <= MAX_REASON_CHARS
        and all(len(quote) <= MAX_QUOTE_CHARS for quote in quotes)
        and not any(_CONTROL.search(text) for text in (finding.reason, *quotes))
    )
