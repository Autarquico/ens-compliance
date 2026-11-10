"""Inicialización y desbloqueo de OpenBao.

Uso: `python -I -m ens_evidence.kit.keystore_setup <init|unseal>`.

Solo biblioteca estándar. Proceso interactivo de un solo uso: las partes de desbloqueo y el root
token viven solo en la memoria del proceso. Todo lo que se muestra va directo al TTY
(`/dev/tty`); por stdout solo sale, al terminar `init`, un JSON con el token de la app y la
contraseña del administrador. Nunca se escribe nada en logs ni en stderr.

Si el proceso muere entre `init` y el final de la configuración, el root se pierde con él: la
recuperación es el paso manual del runbook (parar openbao, vaciar el volumen, repetir `init`).
"""

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

DEFAULT_ADDRESS = "http://openbao:8200"
AUDIT_DEVICE = "archivo/"
APP_POLICY = "ens-app-metadata"
ADMIN_POLICY = "claves-admin"
APP_ROLE = "ens-app"
ADMIN_USER = "admin"
CONFIRMATION = "ENTIENDO"
CLEAR_SCREEN = "\x1b[2J\x1b[H"
TIMEOUT_SECONDS = 15
RUNBOOK_HINT = "Consulta el runbook kit-claves (recuperación si init se corta)."

WARNING = (
    "ATENCIÓN: por debajo del umbral de partes los secretos son irrecuperables.\n"
    "Sin {threshold} de las {shares} partes de desbloqueo nadie, tampoco nosotros, puede\n"
    "abrir el almacén ni recuperar lo que guarda. Las partes se muestran una sola vez.\n"
)
NO_RECORDING = (
    "Esta sesión no debe registrarse: sin script, tmux/screen con log ni grabación de terminal.\n"
    "Cierra la ventana al terminar.\n"
)


class SetupError(Exception):
    """Fallo esperado; el mensaje se muestra al operador y nunca contiene secretos."""


class ApiError(SetupError):
    def __init__(self, status: int, method: str, path: str) -> None:
        super().__init__(f"OpenBao respondió {status} a {method} /v1/{path}")
        self.status = status


@dataclass(frozen=True)
class Terminal:
    write: Callable[[str], None]
    readline: Callable[[str], str]
    secret: Callable[[str], str]


def open_tty() -> Terminal:
    """Terminal real: exige TTY y escribe en /dev/tty, no en stdout ni stderr."""
    import getpass

    if not sys.stdin.isatty():
        _refuse_without_tty()
    try:
        out = open("/dev/tty", "w")  # noqa: SIM115 - vive lo que el proceso
        inp = open("/dev/tty")  # noqa: SIM115
    except OSError:
        _refuse_without_tty()

    def write(text: str) -> None:
        out.write(text)
        out.flush()

    def readline(prompt: str) -> str:
        write(prompt)
        return inp.readline().rstrip("\n")

    return Terminal(write, readline, lambda prompt: getpass.getpass(prompt, stream=out))


def _refuse_without_tty() -> None:
    # Único mensaje fuera del TTY: no existe TTY donde escribirlo y no lleva datos.
    sys.stderr.write("Se necesita un terminal interactivo (docker run -it).\n")
    raise SystemExit(2)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Client:
    def __init__(self, address: str) -> None:
        if not address.startswith(("http://", "https://")):
            raise SetupError("--address debe empezar por http:// o https://")
        self._base = address.rstrip("/")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, token: str | None = None
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Vault-Token"] = token
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self._base}/v1/{path}", data=data, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, method, path) from None
        except (urllib.error.URLError, OSError):
            raise SetupError(f"no se pudo contactar con OpenBao en {self._base}") from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise SetupError(f"respuesta no válida a {method} /v1/{path}") from None


@dataclass(frozen=True)
class InitConfig:
    shares: int
    threshold: int


def load_init_config(path: Path) -> InitConfig:
    try:
        loaded = json.loads(path.read_text())
        shares, threshold = loaded["key_shares"], loaded["key_threshold"]
    except (OSError, ValueError, KeyError, TypeError):
        raise SetupError("init.json debe ser un JSON con key_shares y key_threshold") from None
    valid = all(type(n) is int for n in (shares, threshold)) and 1 <= threshold <= shares <= 255
    if not valid:
        raise SetupError("init.json: se exige 1 <= key_threshold <= key_shares <= 255")
    return InitConfig(shares, threshold)


def load_policies(directory: Traversable | None = None) -> dict[str, str]:
    base = directory or resources.files("ens_evidence.kit") / "openbao"
    try:
        return {name: (base / f"{name}.hcl").read_text() for name in (APP_POLICY, ADMIN_POLICY)}
    except OSError:
        raise SetupError("faltan las políticas HCL del paquete") from None


def _seal_status(client: Client) -> dict[str, Any]:
    return client.request("GET", "sys/seal-status")


def _submit_shares(client: Client, term: Terminal, threshold: int) -> None:
    """Pide partes con getpass hasta desbloquear o agotar `threshold` intentos."""
    for _ in range(threshold):
        part = term.secret("Parte de desbloqueo (oculta, vacío para cancelar): ").strip()
        if not part:
            break
        status = client.request("PUT", "sys/unseal", {"key": part})
        if not status.get("sealed", True):
            term.write("Almacén desbloqueado.\n")
            return
    client.request("PUT", "sys/unseal", {"reset": True})
    raise SetupError("el almacén sigue sellado; comprueba las partes e inténtalo de nuevo")


def run_unseal(client: Client, term: Terminal) -> None:
    status = _seal_status(client)
    if not status.get("initialized"):
        raise SetupError("el almacén no está inicializado; usa init")
    if not status.get("sealed"):
        term.write("El almacén ya está desbloqueado.\n")
        return
    _submit_shares(client, term, int(status.get("t") or 1))


def _show_shares(term: Terminal, shares: list[str]) -> None:
    term.write(NO_RECORDING)
    total = len(shares)
    for number, part in enumerate(shares, start=1):
        term.readline(f"\nEntrega la parte {number} de {total} a su custodio. Intro para verla: ")
        term.write(f"\nParte {number} de {total}:\n\n    {part}\n\n")
        term.readline("Cuando esté anotada y entregada, Intro para borrar la pantalla: ")
        term.write(CLEAR_SCREEN)


def _require_audit(client: Client, root: str) -> None:
    listing = client.request("GET", "sys/audit", token=root)
    devices = listing.get("data", listing)
    if AUDIT_DEVICE not in devices:
        raise SetupError(
            f"el dispositivo de auditoría {AUDIT_DEVICE} no está activo; no se ha configurado nada"
        )


def _configure(client: Client, root: str, policies: dict[str, str]) -> dict[str, str]:
    _require_audit(client, root)
    client.request("POST", "sys/mounts/kv", {"type": "kv", "options": {"version": "2"}}, root)
    client.request("POST", "sys/mounts/transit", {"type": "transit"}, root)
    for name, hcl in policies.items():
        client.request("PUT", f"sys/policies/acl/{name}", {"policy": hcl}, root)
    role = {
        "orphan": True,
        "allowed_policies": [APP_POLICY],
        "token_no_default_policy": True,
        "token_period": "72h",
        "renewable": True,
    }
    client.request("POST", f"auth/token/roles/{APP_ROLE}", role, root)
    created = client.request("POST", f"auth/token/create/{APP_ROLE}", {}, root).get("auth") or {}
    if created.get("policies") != [APP_POLICY] or created.get("orphan") is not True:
        raise SetupError("el token de la app no salió huérfano y solo con la política de la app")
    password = secrets.token_urlsafe(24)
    client.request("POST", "sys/auth/userpass", {"type": "userpass"}, root)
    client.request(
        "POST",
        f"auth/userpass/users/{ADMIN_USER}",
        {"password": password, "token_policies": [ADMIN_POLICY]},
        root,
    )
    return {"app_token": created["client_token"], "admin_password": password}


def _revoke_root(client: Client, root: str) -> None:
    client.request("POST", "auth/token/revoke-self", token=root)
    try:
        client.request("GET", "auth/token/lookup-self", token=root)
    except ApiError as exc:
        if exc.status == 403:
            return
        raise
    raise SetupError("el root token sigue activo tras revocarlo; revócalo a mano")


def run_init(
    client: Client, term: Terminal, config: InitConfig, policies: dict[str, str]
) -> dict[str, str]:
    if _seal_status(client).get("initialized") is not False:
        raise SetupError("el almacén ya está inicializado; init no se repite")
    term.write("\n" + WARNING.format(threshold=config.threshold, shares=config.shares))
    if term.readline(f"Escribe {CONFIRMATION} para continuar: ").strip() != CONFIRMATION:
        raise SetupError("sin confirmación escrita no se inicializa nada")
    created = client.request(
        "PUT", "sys/init", {"secret_shares": config.shares, "secret_threshold": config.threshold}
    )
    root, shares = created["root_token"], list(created["keys"])
    try:
        _show_shares(term, shares)
        term.write(f"Ahora introduce {config.threshold} partes para desbloquear.\n")
        _submit_shares(client, term, config.threshold)
        result = _configure(client, root, policies)
        _revoke_root(client, root)
    except SetupError as exc:
        raise SetupError(f"{exc}. {RUNBOOK_HINT}") from None
    term.write("\nListo. El root token se ha revocado y no existe.\n")
    return result


def main(argv: list[str] | None = None, *, terminal: Terminal | None = None) -> int:
    parser = argparse.ArgumentParser(prog="keystore_setup")
    parser.add_argument("command", choices=("init", "unseal"))
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--init-file", type=Path, help="init.json del paquete (solo init)")
    parser.add_argument(
        "--policies-dir", type=Path, help="directorio con los HCL (por defecto, el del paquete)"
    )
    args = parser.parse_args(argv)
    term = terminal or open_tty()
    try:
        client = Client(args.address)
        if args.command == "unseal":
            run_unseal(client, term)
            return 0
        if args.init_file is None:
            raise SetupError("init necesita --init-file")
        result = run_init(
            client, term, load_init_config(args.init_file), load_policies(args.policies_dir)
        )
    except SetupError as exc:
        term.write(f"\nError: {exc}\n")
        return 1
    except Exception as exc:  # noqa: BLE001 - solo el tipo: un traceback podría arrastrar datos
        term.write(f"\nError inesperado ({type(exc).__name__}). {RUNBOOK_HINT}\n")
        return 1
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
