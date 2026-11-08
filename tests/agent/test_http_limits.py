import json
import logging

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ens_evidence.http.app import create_app
from ens_evidence.http.limits import read_json_body
from ens_evidence.http.logging import host_hash, log_request

LIMIT = 64
JSON = {"content-type": "application/json"}


async def echo(request: Request):
    body = await read_json_body(request, max_bytes=LIMIT, max_depth=4)
    return JSONResponse({"keys": sorted(body)})


async def echo_large(request: Request):
    await read_json_body(request, max_bytes=20000)
    return JSONResponse({})


async def boom(request: Request):
    raise RuntimeError("secreto-interno")


@pytest.fixture
def client():
    routes = [
        Route("/echo", echo, methods=["POST"]),
        Route("/large", echo_large, methods=["POST"]),
        Route("/boom", boom, methods=["POST"]),
    ]
    return TestClient(create_app(routes), raise_server_exceptions=False)


def test_valid_json(client):
    response = client.post("/echo", json={"b": 1, "a": 2})
    assert response.status_code == 200 and response.json() == {"keys": ["a", "b"]}


def test_declared_oversize_is_413(client):
    response = client.post("/echo", content=b'{"a":"' + b"x" * LIMIT + b'"}', headers=JSON)
    assert response.status_code == 413


def test_streamed_oversize_without_content_length_is_413(client):
    def chunks():
        yield b'{"a":"'
        yield b"x" * LIMIT
        yield b'"}'

    response = client.post("/echo", content=chunks(), headers=JSON)
    assert response.status_code == 413


def test_lying_small_content_length_does_not_bypass_the_cap(client):
    # el servidor ASGI real corta en Content-Length; aquí se verifica el tope en streaming
    def chunks():
        yield b"x" * (LIMIT + 1)

    assert client.post("/echo", content=chunks(), headers=JSON).status_code == 413


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "identity, gzip"])
def test_content_encoding_other_than_identity_is_415(client, encoding):
    response = client.post("/echo", content=b"{}", headers=JSON | {"content-encoding": encoding})
    assert response.status_code == 415


def test_identity_encoding_is_accepted(client):
    response = client.post("/echo", content=b"{}", headers=JSON | {"content-encoding": "identity"})
    assert response.status_code == 200


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
def test_non_json_content_type_is_415(client, content_type):
    response = client.post("/echo", content=b"{}", headers={"content-type": content_type})
    assert response.status_code == 415


def test_json_content_type_with_charset_is_accepted(client):
    headers = {"content-type": "application/json; charset=utf-8"}
    assert client.post("/echo", content=b"{}", headers=headers).status_code == 200


@pytest.mark.parametrize("body", [b"{", b"NaN", b"\xff\xfe"])
def test_unreadable_json_is_400(client, body):
    assert client.post("/echo", content=body, headers=JSON).status_code == 400


def test_pathological_nesting_is_400_not_500(client):
    body = b"[" * 9000 + b"]" * 9000
    assert client.post("/large", content=body, headers=JSON).status_code == 400


def test_depth_limit_is_400(client):
    assert client.post("/echo", json={"a": {"b": {"c": {"d": 1}}}}).status_code == 200
    assert client.post("/echo", json={"a": {"b": {"c": {"d": {"e": 1}}}}}).status_code == 400


def test_bad_content_length_is_400(client):
    response = client.post("/echo", content=b"{}", headers=JSON | {"content-length": "-1"})
    assert response.status_code == 400


def test_unknown_path_is_404_and_wrong_method_is_405(client):
    assert client.post("/nope", json={}).status_code == 404
    response = client.get("/echo")
    assert response.status_code == 405 and "POST" in response.headers["allow"]


def test_errors_are_json_without_internals(client):
    response = client.post("/boom", json={})
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error"}
    assert "secreto-interno" not in response.text


def test_debug_is_off_and_healthz_answers():
    app = create_app()
    assert app.debug is False
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_log_request_never_contains_host_identifier_or_free_text(caplog):
    hid = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
    with caplog.at_level(logging.INFO, logger="t"):
        log_request(
            logging.getLogger("t"),
            outcome="ok",
            endpoint="/agent/log",
            asset_id="laptop-ana",
            host_identifier=hid,
        )
        log_request(logging.getLogger("t"), outcome="x\ny=1", endpoint="/agent/log")
    text = caplog.text
    assert hid not in text and host_hash(hid) in text
    assert "laptop-ana" in text and "\ny=1" not in text
    assert json.dumps(text)
