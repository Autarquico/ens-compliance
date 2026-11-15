import json

import pytest

from .conftest import KID, NOW, auth, b64, jwks_for, mint

PATH = "/whoami"


def test_valid_token_gives_identity(client, token):
    response = client.get(PATH, headers=auth(token))
    assert response.status_code == 200
    assert response.json() == {"email": "ana@example.com"}


def test_aud_may_be_a_plain_string(client, key):
    assert client.get(PATH, headers=auth(mint(key, aud="aud-tag-123"))).status_code == 200


def test_missing_header_is_401(client):
    assert client.get(PATH).status_code == 401


def test_expired_is_401(client, key):
    expired = mint(key, exp=int(NOW.timestamp()) - 3600)
    assert client.get(PATH, headers=auth(expired)).status_code == 401


def test_small_skew_is_tolerated(client, key):
    assert (
        client.get(PATH, headers=auth(mint(key, exp=int(NOW.timestamp()) - 30))).status_code == 200
    )


def test_not_yet_valid_is_401(client, key):
    future = mint(key, nbf=int(NOW.timestamp()) + 3600)
    assert client.get(PATH, headers=auth(future)).status_code == 401


def test_wrong_aud_is_401(client, key):
    assert client.get(PATH, headers=auth(mint(key, aud=["otra"]))).status_code == 401


def test_wrong_issuer_is_401(client, key):
    assert (
        client.get(PATH, headers=auth(mint(key, iss="https://evil.example.com"))).status_code == 401
    )


def test_wrong_key_is_401(client, other_key):
    assert client.get(PATH, headers=auth(mint(other_key))).status_code == 401


def test_unknown_kid_is_401(client, key):
    assert client.get(PATH, headers=auth(mint(key, kid="otro"))).status_code == 401


def test_alg_none_is_401(client, key):
    assert client.get(PATH, headers=auth(mint(key, alg="none"))).status_code == 401


def test_hs256_with_public_key_confusion_is_401(client, key):
    token = mint(key, alg="HS256")
    assert client.get(PATH, headers=auth(token)).status_code == 401


def test_missing_email_is_401(client, key):
    assert client.get(PATH, headers=auth(mint(key, email=None))).status_code == 401


@pytest.mark.parametrize("garbage", ["", "abc", "a.b", "a.b.c.d", "....."])
def test_malformed_is_401(client, garbage):
    assert client.get(PATH, headers=auth(garbage)).status_code == 401


def test_non_object_payload_is_401(client):
    head = b64(json.dumps({"alg": "RS256", "kid": KID}).encode())
    assert client.get(PATH, headers=auth(f"{head}.{b64(b'[1]')}.AAAA")).status_code == 401


def test_jwks_unavailable_fails_closed(config, store, key, token):
    from starlette.testclient import TestClient

    from ens_evidence.ui.access import load_jwks_file
    from ens_evidence.ui.app import create_ui_app

    app = create_ui_app(config, store, lambda: NOW, lambda: load_jwks_file(config.jwks_file))
    c = TestClient(app, base_url=config.origin)
    assert c.get("/", headers=auth(token)).status_code == 401
    config.jwks_file.write_text(json.dumps(jwks_for(key)))
    assert c.get("/", headers=auth(token)).status_code == 404  # autenticado; sin ruta


def test_healthz_needs_no_token(client):
    assert client.get("/healthz").status_code == 200


def test_denied_request_writes_nothing(client, store):
    assert (
        client.post(
            "/write", headers={"Origin": "https://ui.example.com", "Sec-Fetch-Site": "same-origin"}
        ).status_code
        == 401
    )
    assert store.list("") == []
