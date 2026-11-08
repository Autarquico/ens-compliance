from .conftest import auth

EXPECTED = {
    "content-security-policy": (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}


def _check(response):
    for name, value in EXPECTED.items():
        assert response.headers[name] == value
    assert "unsafe" not in response.headers["content-security-policy"]


def test_headers_on_ok(client, token):
    _check(client.get("/whoami", headers=auth(token)))


def test_headers_on_401(client):
    response = client.get("/whoami")
    assert response.status_code == 401
    _check(response)


def test_headers_on_403_and_404(client, token):
    _check(client.post("/write", headers=auth(token)))
    _check(client.get("/nope", headers=auth(token)))


def test_headers_on_healthz(client):
    _check(client.get("/healthz"))
