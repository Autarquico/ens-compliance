"""Cliente de Chat Completions de OpenAI con Structured Outputs.

Contrato (platform.openai.com, guía «Structured Outputs», consultada el 2026-11-04):
`POST /v1/chat/completions` con `Authorization: Bearer`, `response_format: {"type":
"json_schema", "json_schema": {"name", "schema", "strict": true}}`; la respuesta trae el JSON en
`choices[0].message.content`, o `refusal`, y `finish_reason` distinto de `stop` si se cortó.
"""

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from ens_evidence.preaudit.adapter import JudgmentConfig, ProviderError, ProviderErrorKind
from ens_evidence.preaudit.clients._http import JsonPoster, api_destination, api_key

NAME = "openai"
DEFAULT_BASE_URL = "https://api.openai.com"
KEY_ENV = "OPENAI_API_KEY"
SCHEMA_NAME = "preaudit_findings"


class OpenAIProvider:
    requires_shell_warning = False

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        env: Mapping[str, str] = os.environ,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        headers = {"authorization": f"Bearer {api_key(env, KEY_ENV)}"}
        self._poster = JsonPoster(base_url, headers, transport=transport, sleep=sleep)

    def __repr__(self) -> str:
        return f"OpenAIProvider({self.destination!r})"

    @property
    def destination(self) -> str:
        return api_destination(NAME, self._poster.base_url)

    def judge(
        self, system_prompt: str, payload: bytes, schema: dict[str, Any], config: JudgmentConfig
    ) -> str:
        body: dict[str, Any] = {
            "model": config.model,
            "max_completion_tokens": config.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload.decode("utf-8")},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": SCHEMA_NAME, "schema": schema, "strict": True},
            },
        }
        return _content(self._poster.post("/v1/chat/completions", body, config.timeout))


def _content(reply: Any) -> str:
    choices = reply.get("choices") if isinstance(reply, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict) or choice.get("finish_reason") != "stop":
        raise ProviderError(ProviderErrorKind.RESPONSE, "respuesta del proveedor incompleta")
    if message.get("refusal"):
        raise ProviderError(ProviderErrorKind.RESPONSE, "el proveedor rechazó la petición")
    content = message.get("content")
    if not isinstance(content, str):
        raise ProviderError(ProviderErrorKind.RESPONSE, "el proveedor no devolvió los hallazgos")
    return content
