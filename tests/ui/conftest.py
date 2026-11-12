import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from applicability.helpers import LATER, decide_all_undetermined, prepared_workspace, uniform
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from matrix.helpers import SIGNED_AT
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.config import UiConfig
from ens_evidence.ui.pages import identity_of
from ens_evidence.ui.views.matrix import MATRIX_ROUTES
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ORIGIN = "https://ui.example.com"
AUD = "aud-tag-123"
TEAM = "acme.cloudflareaccess.com"
KID = "kid-1"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def int_b64(value: int) -> str:
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def make_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_for(key: rsa.RSAPrivateKey, kid: str = KID) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": kid,
                "alg": "RS256",
                "n": int_b64(numbers.n),
                "e": int_b64(numbers.e),
            }
        ]
    }


def mint(key: rsa.RSAPrivateKey, *, alg: str = "RS256", kid: str = KID, **overrides) -> str:
    claims = {
        "iss": f"https://{TEAM}",
        "aud": [AUD],
        "email": "ana@example.com",
        "exp": int(NOW.timestamp()) + 600,
        "nbf": int(NOW.timestamp()) - 10,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    head = b64(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    signature = b""
    if alg == "RS256":
        signature = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(signature)}"


@pytest.fixture(scope="session")
def key() -> rsa.RSAPrivateKey:
    return make_key()


@pytest.fixture(scope="session")
def other_key() -> rsa.RSAPrivateKey:
    return make_key()


@pytest.fixture
def config(tmp_path: Path) -> UiConfig:
    return UiConfig(
        origin=ORIGIN, access_aud=AUD, team_domain=TEAM, jwks_file=tmp_path / "jwks.json"
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    root = tmp_path / "ws"
    root.mkdir()
    return LocalStore(root)


async def _whoami(request: Request) -> JSONResponse:
    return JSONResponse({"email": identity_of(request).email})


async def _write(request: Request) -> JSONResponse:
    request.app.state.store.write_text("scope/x.yaml", "a: 1\n")
    return JSONResponse({"ok": True})


@pytest.fixture
def client(config, store, key) -> TestClient:
    app = create_ui_app(
        config,
        store,
        lambda: NOW,
        lambda: jwks_for(key),
        routes=[
            Route("/whoami", _whoami, methods=["GET"]),
            Route("/write", _write, methods=["POST"]),
        ],
    )
    return TestClient(app, base_url=ORIGIN)


@pytest.fixture
def token(key) -> str:
    return mint(key)


def auth(token: str) -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": token}


# --- asistente y vistas (T4b) ------------------------------------------------------------------

EXAMPLE_SCOPE = Path(__file__).resolve().parents[2] / "examples" / "workspace" / "scope"
SCOPE_NAMES = ("assets", "services", "dependencies", "system")
WRITE_HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}


def seed_example_scope(store: LocalStore) -> LocalStore:
    for name in SCOPE_NAMES:
        store.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    return store


class Session:
    """Un cliente autenticado con un JWT de Access válido."""

    def __init__(self, client: TestClient, token: str) -> None:
        self._client = client
        self._token = token

    def get(self, path: str, **kwargs):
        return self._client.get(path, headers=auth(self._token), follow_redirects=False, **kwargs)

    def post(self, path: str, data: dict, *, htmx: bool = False):
        headers = {**auth(self._token), **WRITE_HEADERS}
        if htmx:
            headers["HX-Request"] = "true"
        return self._client.post(path, data=data, headers=headers, follow_redirects=False)


def ui_client(config: UiConfig, store: LocalStore, key) -> TestClient:
    from ens_evidence.ui.routes import ui_routes

    app = create_ui_app(config, store, lambda: NOW, lambda: jwks_for(key), routes=ui_routes())
    return TestClient(app, base_url=ORIGIN)


@pytest.fixture
def example_store(store: LocalStore) -> LocalStore:
    return seed_example_scope(store)


@pytest.fixture
def raw_client(config, example_store, key) -> TestClient:
    return ui_client(config, example_store, key)


@pytest.fixture
def session(raw_client, token) -> Session:
    return Session(raw_client, token)


def snapshot_of(store: LocalStore) -> dict[str, bytes]:
    return {k: store.read_bytes(k) for k in store.list("")}


@pytest.fixture
def workspaces(tmp_path):
    cli_store = seed_example_scope(LocalStore(tmp_path / "cli-ws"))
    ui_store = seed_example_scope(LocalStore(tmp_path / "ui-ws"))
    return cli_store, ui_store


@pytest.fixture
def completed(session) -> Session:
    """El guion completo ya aplicado, para que cada vista se pinte en su estado final."""
    from .test_equivalence import ui_script

    ui_script(session)
    return session


@pytest.fixture
def signed(tmp_path):
    store = prepared_workspace(tmp_path / "signed", uniform("MEDIO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    return store


@pytest.fixture
def web(config, signed, key, token) -> Session:
    app = create_ui_app(config, signed, lambda: NOW, lambda: jwks_for(key), routes=MATRIX_ROUTES)
    return Session(TestClient(app, base_url=ORIGIN), token)
