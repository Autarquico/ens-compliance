import pytest

from .conftest import auth

OK = {"Origin": "https://ui.example.com", "Sec-Fetch-Site": "same-origin"}


def test_same_origin_post_passes(client, token, store):
    assert client.post("/write", headers={**auth(token), **OK}).status_code == 200
    assert store.read_text("scope/x.yaml") == "a: 1\n"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"Origin": "https://evil.example.com", "Sec-Fetch-Site": "same-origin"},
        {"Origin": "https://ui.example.com", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "https://ui.example.com"},
        {"Sec-Fetch-Site": "same-origin"},
        {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
    ],
)
def test_cross_origin_post_is_403_and_writes_nothing(client, token, store, extra):
    response = client.post("/write", headers={**auth(token), **extra})
    assert response.status_code == 403
    assert store.list("") == []


def test_get_needs_no_origin(client, token):
    assert client.get("/whoami", headers=auth(token)).status_code == 200
