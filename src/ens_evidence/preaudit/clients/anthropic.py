"""Cliente de la Messages API de Anthropic con salida estructurada por herramienta forzada.

Contrato (docs.claude.com, consultado el 2026-11-04): `POST /v1/messages`, clave en `x-api-key`
(admitida junto a `Authorization: Bearer`), `anthropic-version: 2023-06-01` (sigue en el
historial de versiones; la `2026-10-21` elimina `temperature`), `tools[].input_schema` y
`tool_choice: {"type": "tool", "name": ...}`; la respuesta trae un bloque `tool_use` con `input`.
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind
from ens_evidence.preaudit.clients._http import JsonPoster, api_key

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
KEY_ENV = "ANTHROPIC_API_KEY"
TOOL_NAME = "record_findings"
TOOL_DESCRIPTION = "Registra los hallazgos propuestos de la pre-auditoría."


class AnthropicProvider:
    requires_shell_warning = False

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        env: Mapping[str, str] = os.environ,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        headers = {"x-api-key": api_key(env, KEY_ENV), "anthropic-version": API_VERSION}
        self._poster = JsonPoster(base_url, headers, transport=transport, sleep=sleep)

    def __repr__(self) -> str:
        return f"AnthropicProvider({self.destination!r})"

    @property
    def destination(self) -> str:
        return self._poster.base_url

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": payload.decode("utf-8")}],
            "tools": [{"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        if config.temperature is not None:
            body["temperature"] = config.temperature
        return _tool_input(self._poster.post("/v1/messages", body, config.timeout))


def _tool_input(reply: Any) -> str:
    blocks = reply.get("content") if isinstance(reply, dict) else None
    if not isinstance(blocks, list) or reply.get("stop_reason") == "max_tokens":
        raise ProviderError(ProviderErrorKind.RESPONSE, "respuesta del proveedor incompleta")
    uses = [
        b
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == TOOL_NAME
    ]
    if len(uses) != 1 or not isinstance(uses[0].get("input"), dict):
        raise ProviderError(ProviderErrorKind.RESPONSE, "el proveedor no devolvió los hallazgos")
    return json.dumps(uses[0]["input"], ensure_ascii=False)
