"""Rutas HTTP del protocolo remoto de osquery (`/agent/*`)."""

import logging
from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ens_evidence.agent.node_key import host_hash
from ens_evidence.agent.protocol import (
    AgentService,
    Busy,
    MalformedBody,
    NodeInvalid,
    Reply,
    Unavailable,
)
from ens_evidence.http.limits import read_json_body
from ens_evidence.http.logging import log_request

SMALL_BODY_BYTES = 64 * 1024
LARGE_BODY_BYTES = 10 * 1024 * 1024
RETRY_AFTER_SECONDS = "30"
_LOGGER = logging.getLogger("ens_evidence.agent")


def routes(service: AgentService) -> list[Route]:
    """Los cinco endpoints POST, listos para `create_app(routes(service))`."""
    table = [
        ("enroll", service.enroll, SMALL_BODY_BYTES),
        ("config", service.config, SMALL_BODY_BYTES),
        ("log", service.log, LARGE_BODY_BYTES),
        ("distributed/read", service.distributed_read, SMALL_BODY_BYTES),
        ("distributed/write", service.distributed_write, LARGE_BODY_BYTES),
    ]
    return [
        Route(f"/agent/{name}", _endpoint(name, handle, limit), methods=["POST"])
        for name, handle, limit in table
    ]


def _endpoint(name: str, handle: Callable[[Any], Reply], limit: int) -> Callable:
    async def view(request: Request) -> Response:
        body = await read_json_body(request, max_bytes=limit)
        try:
            reply = await run_in_threadpool(handle, body)
        except NodeInvalid:
            log_request(_LOGGER, outcome="node_invalid", endpoint=name)
            return JSONResponse({"node_invalid": True})
        except MalformedBody as bad:
            log_request(
                _LOGGER,
                outcome="invalid_body",
                endpoint=name,
                asset_id=bad.node.asset_id,
                host=host_hash(bad.node.host_identifier),
            )
            return JSONResponse({"error": "invalid_body"}, status_code=400)
        except Busy:
            log_request(_LOGGER, outcome="busy", endpoint=name)
            return _retry("busy")
        except Unavailable:
            log_request(_LOGGER, outcome="unavailable", endpoint=name)
            return _retry("unavailable")
        log_request(
            _LOGGER,
            outcome=reply.outcome,
            endpoint=name,
            asset_id=reply.node.asset_id,
            host=host_hash(reply.node.host_identifier),
        )
        return JSONResponse(reply.payload)

    return view


def _retry(code: str) -> Response:
    return JSONResponse(
        {"error": code}, status_code=503, headers={"Retry-After": RETRY_AFTER_SECONDS}
    )
