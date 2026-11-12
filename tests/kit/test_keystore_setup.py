import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ens_evidence.kit import keystore_setup as setup

ROOT = "s.ROOTTOKEN-secreto"
SHARES = ["parteAAA111", "parteBBB222", "parteCCC333"]
APP = "s.APPTOKEN-valor"


class FakeBao:
    def __init__(self, audit_devices=("archivo/",), initialized=False):
        self.initialized = initialized
        self.sealed = initialized
        self.audit = {d: {"type": "file"} for d in audit_devices}
        self.progress: list[str] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.root_revoked = False
        self.created_orphan = True
        self.fail_path: str | None = None  # responde 500 con el root a esta ruta
        self.revoke_fails = False
        self.health_codes: list[int] = []  # respuestas previas de sys/health (500 = aún no activo)

    def handle(self, method, path, body, token):
        self.calls.append((method, path, body))
        if path == "sys/seal-status":
            return 200, {"initialized": self.initialized, "sealed": self.sealed, "t": 2, "n": 3}
        if path == "sys/health":
            return (self.health_codes.pop(0), {}) if self.health_codes else (200, {})
        if path == "sys/init":
            self.initialized = self.sealed = True
            return 200, {"keys": SHARES, "root_token": ROOT}
        if path == "sys/unseal":
            if body.get("reset"):
                self.progress = []
                return 200, {"sealed": True}
            self.progress.append(body["key"])
            if len(self.progress) >= 2 and all(k in SHARES for k in self.progress):
                self.sealed = False
            return 200, {"sealed": self.sealed}
        if token == ROOT and self.root_revoked:
            return 403, {}
        if token != ROOT:
            return 403, {}
        if path == self.fail_path:
            return 500, {}
        if path == "auth/token/revoke-self" and self.revoke_fails:
            return 500, {}
        if path == "sys/audit":
            return 200, {"data": self.audit, **self.audit}
        if path == "auth/token/revoke-self":
            self.root_revoked = True
            return 204, None
        if path == "auth/token/lookup-self":
            return 200, {"data": {}}
        if path == "auth/token/create/ens-app":
            return 200, {
                "auth": {
                    "client_token": APP,
                    "policies": ["ens-app-metadata"],
                    "orphan": self.created_orphan,
                }
            }
        return 204, None


@pytest.fixture
def bao(request):
    state = FakeBao(**getattr(request, "param", {}))

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            status, payload = state.handle(
                self.command,
                self.path.removeprefix("/v1/"),
                body,
                self.headers.get("X-Vault-Token"),
            )
            raw = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_PUT = do_POST = _serve

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.address = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()


class Script:
    """Terminal inyectado: entrada guiada, salida acumulada."""

    def __init__(self, lines, secrets_):
        self.lines, self.secret_inputs, self.out = list(lines), list(secrets_), []

    def terminal(self):
        return setup.Terminal(
            self.out.append,
            lambda _p: self.lines.pop(0) if self.lines else "",
            lambda _p: self.secret_inputs.pop(0) if self.secret_inputs else "",
        )

    @property
    def text(self):
        return "".join(self.out)


@pytest.fixture
def package(tmp_path):
    (tmp_path / "init.json").write_text(json.dumps({"key_shares": 3, "key_threshold": 2}))
    policies = tmp_path / "pol"
    policies.mkdir()
    for name in ("ens-app-metadata", "claves-admin"):
        (policies / f"{name}.hcl").write_text(
            f'path "kv/metadata/*" {{ capabilities = ["list"] }} # {name}'
        )
    return tmp_path


def run_init(bao, package, script):
    return setup.main(
        [
            "init",
            "--address",
            bao.address,
            "--init-file",
            str(package / "init.json"),
            "--policies-dir",
            str(package / "pol"),
        ],
        terminal=script.terminal(),
    )


def good_script():
    return Script(["ENTIENDO"] + [""] * 6, SHARES[:2])


def paths(bao):
    return [(m, p) for m, p, _ in bao.calls]


def test_init_completo_emite_solo_el_json_final(bao, package, capsys):
    script = good_script()

    assert run_init(bao, package, script) == 0

    out, err = capsys.readouterr()
    result = json.loads(out)
    assert set(result) == {"app_token", "admin_password"} and result["app_token"] == APP
    assert err == ""
    assert bao.root_revoked


def test_partes_y_root_nunca_en_stdout_ni_stderr(bao, package, capsys):
    script = good_script()
    run_init(bao, package, script)

    out, err = capsys.readouterr()
    for secret in (*SHARES, ROOT):
        assert secret not in out and secret not in err
    assert all(share in script.text for share in SHARES)
    assert ROOT not in script.text


def test_avisa_de_irrecuperabilidad_y_limpia_la_pantalla_entre_partes(bao, package):
    script = good_script()
    run_init(bao, package, script)

    assert "irrecuperables" in script.text
    assert script.text.count(setup.CLEAR_SCREEN) == 3
    assert "no debe registrarse" in script.text


def test_sin_confirmacion_escrita_no_inicializa(bao, package):
    script = Script(["si"], [])

    assert run_init(bao, package, script) == 1
    assert ("PUT", "sys/init") not in paths(bao)


@pytest.mark.parametrize("bao", [{"initialized": True}], indirect=True)
def test_rechaza_si_ya_esta_inicializado(bao, package):
    script = good_script()

    assert run_init(bao, package, script) == 1
    assert "ya está inicializado" in script.text
    assert ("PUT", "sys/init") not in paths(bao)


@pytest.mark.parametrize("bao", [{"audit_devices": ()}], indirect=True)
def test_aborta_sin_tocar_si_no_hay_auditoria(bao, package, capsys):
    script = good_script()

    assert run_init(bao, package, script) == 1

    assert capsys.readouterr().out == ""
    written = [
        c
        for c in paths(bao)
        if c[0] != "GET" and not c[1].startswith(("sys/", "auth/token/revoke"))
    ]
    assert written == [] and not any("mounts" in p or "policies" in p for _, p in paths(bao))
    assert "kit-claves" in script.text and ROOT not in script.text


def test_token_desde_el_rol_y_huerfano(bao, package):
    run_init(bao, package, good_script())

    role = next(b for m, p, b in bao.calls if p == "auth/token/roles/ens-app")
    assert role["orphan"] is True and role["allowed_policies"] == ["ens-app-metadata"]
    assert role["token_no_default_policy"] is True and role["token_period"] == "72h"
    assert ("POST", "auth/token/create/ens-app") in paths(bao)


def test_rechaza_un_token_que_no_sale_huerfano(bao, package, capsys):
    bao.created_orphan = False

    assert run_init(bao, package, good_script()) == 1
    assert capsys.readouterr().out == ""


def test_root_revocado_y_verificado_con_403(bao, package):
    run_init(bao, package, good_script())

    order = [p for _, p in paths(bao)]
    assert order.index("auth/token/revoke-self") < len(order) - 1
    assert order[-1] == "auth/token/lookup-self"
    assert bao.root_revoked


def test_admin_con_userpass_y_politica_claves_admin(bao, package):
    run_init(bao, package, good_script())

    user = next(b for m, p, b in bao.calls if p == "auth/userpass/users/admin")
    assert user["token_policies"] == ["claves-admin"] and len(user["password"]) >= 24


def test_partes_incorrectas_no_configuran_nada(bao, package, capsys):
    script = Script(["ENTIENDO"] + [""] * 6, ["mala1", "mala2"])

    assert run_init(bao, package, script) == 1
    assert capsys.readouterr().out == ""
    assert "sys/audit" not in [p for _, p in paths(bao)]


def test_init_json_invalido(bao, package):
    (package / "init.json").write_text(json.dumps({"key_shares": 2, "key_threshold": 3}))

    assert run_init(bao, package, good_script()) == 1
    assert ("PUT", "sys/init") not in paths(bao)


@pytest.mark.parametrize("bao", [{"initialized": True}], indirect=True)
def test_unseal_desbloquea_y_no_imprime_las_partes(bao, capsys):
    script = Script([], SHARES[:2])

    assert setup.main(["unseal", "--address", bao.address], terminal=script.terminal()) == 0

    out, err = capsys.readouterr()
    assert not bao.sealed and out == "" and err == ""
    assert not any(share in script.text for share in SHARES)


@pytest.mark.parametrize("bao", [{"initialized": True}], indirect=True)
def test_unseal_agota_los_intentos_con_partes_malas(bao):
    script = Script([], ["x", "y", "z"])

    assert setup.main(["unseal", "--address", bao.address], terminal=script.terminal()) == 1
    assert bao.sealed


def test_sin_tty_no_hay_modo_real(monkeypatch):
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit) as exit_:
        setup.open_tty()

    assert exit_.value.code == 2


def test_politicas_se_leen_de_un_directorio(package):
    policies = setup.load_policies(package / "pol")

    assert set(policies) == {"ens-app-metadata", "claves-admin"}
    assert "kv/metadata" in policies["claves-admin"]


def test_espera_a_que_el_nodo_sea_activo_antes_de_configurar(bao, package, monkeypatch):
    # OpenBao 2.7.2 real: justo tras desbloquear, la API da 500 hasta que raft elige activo
    monkeypatch.setattr(setup, "ACTIVE_POLL_SECONDS", 0)
    bao.health_codes = [500, 429, 500]

    assert run_init(bao, package, good_script()) == 0

    sequence = [p for _, p in paths(bao)]
    assert sequence.count("sys/health") == 4
    assert sequence.index("sys/health") < sequence.index("sys/audit")


def test_si_el_nodo_no_llega_a_ser_activo_no_configura_nada(bao, package, monkeypatch):
    monkeypatch.setattr(setup, "ACTIVE_POLL_SECONDS", 0)
    monkeypatch.setattr(setup, "ACTIVE_WAIT_SECONDS", 0)
    bao.health_codes = [500] * 50
    script = good_script()

    assert run_init(bao, package, script) == 1

    assert "no llegó a estar activo" in script.text and "kit-claves" in script.text
    assert "sys/audit" not in [p for _, p in paths(bao)]


CONFIGURE_STEPS = [
    "sys/audit",
    "sys/mounts/kv",
    "sys/mounts/transit",
    "sys/policies/acl/ens-app-metadata",
    "auth/token/roles/ens-app",
    "auth/token/create/ens-app",
    "sys/auth/userpass",
    "auth/userpass/users/admin",
]


@pytest.mark.parametrize("step", CONFIGURE_STEPS)
def test_fallo_en_cualquier_paso_de_configurar_revoca_y_verifica_el_root(bao, package, step):
    bao.fail_path = step
    script = good_script()

    assert run_init(bao, package, script) == 1

    order = [p for _, p in paths(bao)]
    assert bao.root_revoked
    assert order[-2:] == ["auth/token/revoke-self", "auth/token/lookup-self"]
    assert "se ha revocado" in script.text and "500" in script.text
    assert ROOT not in script.text


def test_si_falla_configurar_y_tambien_revocar_el_mensaje_dice_ambos(bao, package):
    bao.fail_path = "sys/mounts/transit"
    bao.revoke_fails = True
    script = good_script()

    assert run_init(bao, package, script) == 1

    assert "500 a POST /v1/sys/mounts/transit" in script.text
    assert "no se pudo revocar el root token" in script.text and "kit-claves" in script.text
    assert not bao.root_revoked


def test_una_excepcion_de_red_al_configurar_tambien_revoca(bao, package, monkeypatch):
    def boom(*_a, **_k):
        raise TimeoutError("secreto interno")

    monkeypatch.setattr(setup, "_require_audit", boom)
    script = good_script()

    assert run_init(bao, package, script) == 1

    assert bao.root_revoked
    assert "TimeoutError" in script.text and "secreto interno" not in script.text
