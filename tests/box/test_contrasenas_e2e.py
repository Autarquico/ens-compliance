"""Kit de contraseñas de extremo a extremo (`pytest -m docker`, fuera del gate por defecto).

`render_bundle` -> `box.sh kit enable contrasenas` -> `kit contrasenas alta` -> Vaultwarden
1.37.5 real (la imagen fijada por digest en compose.yaml) con un usuario con TOTP, otro sin
doble factor y un invitado -> exportador -> restauración -> `cerrar-alta`. Vaultwarden solo
es alcanzable por la red interna `contrasenas-net`: las peticiones salen de un contenedor de un
solo uso en esa red. Todos los datos son ficticios.
"""

# ruff: noqa: F401, F811  (fixtures de la caja importadas de test_integration)
import base64
import hashlib
import hmac
import json
import re
import struct
import subprocess
import time
import uuid
from datetime import UTC, datetime

import pytest

from box.test_integration import box, installed, run
from ens_evidence.kit.passwords_setup import PasswordsInputs, render_bundle

pytestmark = pytest.mark.docker
HOST = "contrasenas.test.example"
UID = "10001"
EXPORT_PATH = "/export/usuarios.json"
PASSWORD_HASH = base64.b64encode(b"hash-ficticio-0123456789abcdef0123456789").decode()

REQUEST_SCRIPT = """
import json, sys, urllib.request, urllib.error
spec = json.load(sys.stdin)
data = spec["body"].encode() if spec["body"] else None
request = urllib.request.Request(
    spec["url"], data=data, headers=spec["headers"], method=spec["method"]
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    reply = opener.open(request, timeout=20)
except urllib.error.HTTPError as exc:
    reply = exc
json.dump(
    {"status": reply.status if hasattr(reply, "status") else reply.code,
     "body": reply.read().decode(), "cookie": reply.headers.get("Set-Cookie", "")},
    sys.stdout,
)
"""


def python_image(box):
    text = (box.clone / "Dockerfile").read_text()
    return re.search(r"^ARG PYTHON_IMAGE=(.*)$", text, re.MULTILINE).group(1)


def http(box, network, host, method, path, *, json_body=None, form=None, headers=None):
    """(estado, cuerpo, cookie) de una petición hecha desde un contenedor en `network`."""
    headers = dict(headers or {})
    body = ""
    if json_body is not None:
        body = json.dumps(json_body)
        headers["Content-Type"] = "application/json"
    if form is not None:
        body = "&".join(f"{k}={v}" for k, v in form.items())
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    spec = {"method": method, "url": f"http://{host}{path}", "headers": headers, "body": body}
    done = subprocess.run(
        ["docker", "run", "--rm", "-i", "--log-driver", "none", "--network", network]
        + ["--read-only", "--cap-drop", "ALL", "--user", UID, python_image(box)]
        + ["python", "-I", "-c", REQUEST_SCRIPT],
        input=json.dumps(spec),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    reply = json.loads(done.stdout)
    return reply["status"], reply["body"], reply["cookie"]


def vw(box, method, path, **kwargs):
    return http(box, f"{box.project}_contrasenas-net", "vaultwarden:80", method, path, **kwargs)


def totp(secret_base32, at=None):
    key = base64.b32decode(secret_base32 + "=" * (-len(secret_base32) % 8))
    counter = int((at or time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


@pytest.fixture(scope="module")
def contrasenas_box(box, installed):
    assert installed.returncode == 0, installed.stdout + installed.stderr
    ui_env = box.clone / "deploy" / "ui.env"
    ui_env.write_text(
        "ENS_UI_HOST=ui.test.example\nENS_UI_ORIGIN=https://ui.test.example\n"
        f"ENS_UI_ACCESS_AUD=x\nENS_UI_ACCESS_TEAM_DOMAIN=x.example\nENS_CONTRASENAS_HOST={HOST}\n"
        "ENS_UI_ACCESS_JWKS_FILE=/data/state/ui/access-jwks.json\n"
    )
    ui_env.chmod(0o600)
    yield box
    owned = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={box.project}"]
    ).stdout.split()
    if owned:
        run(["docker", "rm", "-f", *owned])
    run(["docker", "rm", "-f", f"{box.project}-vwrestore"])
    for network in ("contrasenas-net", "vwrestore-net"):
        run(["docker", "network", "rm", f"{box.project}_{network}"])
    for volume in ("vaultwarden-data", "contrasenas-export", "contrasenas-snapshot", "vwrestore"):
        run(["docker", "volume", "rm", "-f", f"{box.project}_{volume}"])


@pytest.fixture(scope="module")
def package(contrasenas_box, tmp_path_factory):
    inputs = PasswordsInputs(asset_id="vaultwarden", inactive_days=90, host=HOST)
    path = tmp_path_factory.mktemp("paquete") / "contrasenas.tar.gz"
    path.write_bytes(render_bundle(inputs, datetime.now(UTC)))
    return path


@pytest.fixture(scope="module")
def enabled(contrasenas_box, package):
    result = contrasenas_box.sh(
        "kit", "enable", "contrasenas", str(package), ENS_BOX_HEALTH_TIMEOUT="120"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def kit_dir(box):
    return box.clone / "deploy" / "kit" / "contrasenas"


def container_env(box, service):
    out = box.inspect(service, "{{range .Config.Env}}{{println .}}{{end}}")
    return out.split()


def volume_run(box, volume, script, *, user=UID, mode="ro"):
    return run(
        ["docker", "run", "--rm", "--network", "none", "--user", user]
        + ["-v", f"{box.project}_{volume}:/v:{mode}", "alpine:3", "sh", "-c", script]
    )


# --- arranque -------------------------------------------------------------------------------


def test_vaultwarden_arranca_endurecido_sin_puertos_y_sin_panel(contrasenas_box, enabled):
    box = contrasenas_box
    assert box.inspect("vaultwarden", "{{.State.Health.Status}}") == "healthy"
    assert box.inspect("vaultwarden", "{{.HostConfig.ReadonlyRootfs}}") == "true"
    assert box.inspect("vaultwarden", "{{json .HostConfig.CapDrop}}") == '["ALL"]'
    assert "no-new-privileges:true" in box.inspect(
        "vaultwarden", "{{json .HostConfig.SecurityOpt}}"
    )
    assert box.inspect("vaultwarden", "{{json .HostConfig.PortBindings}}") in ("{}", "null")
    env = container_env(box, "vaultwarden")
    assert "SIGNUPS_ALLOWED=false" in env and "INVITATIONS_ALLOWED=true" in env
    assert f"DOMAIN=https://{HOST}" in env
    assert not [line for line in env if line.startswith("ADMIN_TOKEN")]
    networks = json.loads(box.inspect("vaultwarden", "{{json .NetworkSettings.Networks}}"))
    assert list(networks) == [f"{box.project}_contrasenas-net"]
    status, body, _ = vw(box, "GET", "/admin")
    assert status == 200 and "The admin panel is disabled" in body


def test_el_exportador_esta_aislado_y_ve_la_base_sin_ser_root(contrasenas_box, enabled):
    box = contrasenas_box
    assert box.inspect("contrasenas-export", "{{.State.Status}}") == "running"
    assert box.inspect("contrasenas-export", "{{.Config.User}}") == f"{UID}:{UID}"
    assert box.inspect("contrasenas-export", "{{.HostConfig.NetworkMode}}") == "none"
    assert box.inspect("contrasenas-export", "{{.HostConfig.ReadonlyRootfs}}") == "true"
    assert box.inspect("contrasenas-export", "{{json .HostConfig.CapDrop}}") == '["ALL"]'
    assert "no-new-privileges:true" in box.inspect(
        "contrasenas-export", "{{json .HostConfig.SecurityOpt}}"
    )
    mounts = {
        m["Destination"]: m["RW"]
        for m in json.loads(box.inspect("contrasenas-export", "{{json .Mounts}}"))
        if m["Type"] == "volume"
    }
    assert mounts == {"/source": False, "/export": True, "/snapshot": True}, mounts
    # Sin env_file ni environment: el entorno es exactamente el de la imagen.
    image_env = run(
        ["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}"]
        + [box.inspect("contrasenas-export", "{{.Image}}")]
    ).stdout.split()
    assert sorted(container_env(box, "contrasenas-export")) == sorted(image_env)
    read = box.exec("contrasenas-export", "python", "-I", "-c", READ_DB)
    assert read.returncode == 0, read.stderr
    write = box.exec("contrasenas-export", "sh", "-c", "touch /source/x")
    assert write.returncode != 0


READ_DB = (
    "import sqlite3;c=sqlite3.connect('file:/source/db.sqlite3?mode=ro',uri=True);"
    "print(c.execute('select count(*) from users').fetchone())"
)


def test_la_app_solo_monta_el_export_y_no_alcanza_vaultwarden(contrasenas_box, enabled):
    box = contrasenas_box
    mounts = {
        m["Destination"]: (m["Name"].removeprefix(f"{box.project}_"), m["RW"])
        for m in json.loads(box.inspect("app", "{{json .Mounts}}"))
        if m["Type"] == "volume"
    }
    assert mounts["/run/kit/contrasenas"] == ("contrasenas-export", False)
    assert "vaultwarden-data" not in {name for name, _ in mounts.values()}
    networks = json.loads(box.inspect("app", "{{json .NetworkSettings.Networks}}"))
    assert f"{box.project}_contrasenas-net" not in networks


def test_la_ram_en_reposo_de_vaultwarden_y_del_exportador_se_mide(contrasenas_box, enabled):
    time.sleep(20)
    for service in ("vaultwarden", "contrasenas-export"):
        stats = run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}"]
            + [contrasenas_box.container(service)]
        )
        print(f"RAM {service} en reposo: {stats.stdout.strip()}")
        assert re.search(r"\d+(\.\d+)?(MiB|GiB|KiB)", stats.stdout), stats.stdout


# --- alta -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def alta(contrasenas_box, enabled):
    result = contrasenas_box.sh("kit", "contrasenas", "alta", ENS_BOX_HEALTH_TIMEOUT="120")
    assert result.returncode == 0, result.stdout + result.stderr
    token = re.search(r"^\s+(\S{32})$", result.stdout + result.stderr, re.MULTILINE).group(1)
    return {"token": token, "output": result.stdout + result.stderr}


def test_alta_abre_el_panel_con_un_token_que_solo_esta_en_disco_como_hash(contrasenas_box, alta):
    box = contrasenas_box
    admin_env = kit_dir(box) / "admin.env"
    assert admin_env.stat().st_mode & 0o777 == 0o600
    assert (kit_dir(box) / "alta.yaml").stat().st_mode & 0o777 == 0o600
    text = admin_env.read_text()
    assert text.startswith("ADMIN_TOKEN='$argon2id$") and alta["token"] not in text
    assert [line for line in container_env(box, "vaultwarden") if line.startswith("ADMIN_TOKEN=")]
    assert alta["token"] not in alta["output"].replace(f"  {alta['token']}", "")
    status, body, cookie = vw(box, "GET", "/admin")
    assert status == 200 and "The admin panel is disabled" not in body
    ok = vw(box, "POST", "/admin", form={"token": alta["token"]})
    assert ok[0] == 200 and "VW_ADMIN" in ok[2]
    bad = vw(box, "POST", "/admin", form={"token": "no-es-el-token"})
    assert "VW_ADMIN" not in bad[2]


def test_alta_no_se_repite_ni_cambia_el_token_vigente(contrasenas_box, alta):
    box = contrasenas_box
    before = (kit_dir(box) / "admin.env").read_text()

    again = box.sh("kit", "contrasenas", "alta")

    assert again.returncode != 0 and "alta abierta" in again.stderr
    assert (kit_dir(box) / "admin.env").read_text() == before
    assert alta["token"] not in again.stdout + again.stderr
    status = box.sh("status")
    assert "alta ABIERTA" in status.stdout + status.stderr


# --- usuarios reales ------------------------------------------------------------------------


def register(box, admin_cookie, email):
    """Invita por el panel y completa el registro, como lo haría el cliente web."""
    invited = vw(
        box, "POST", "/admin/invite", json_body={"email": email}, headers={"Cookie": admin_cookie}
    )
    assert invited[0] == 200, invited
    status, body, _ = vw(
        box,
        "POST",
        "/identity/accounts/register/send-verification-email",
        json_body={"email": email, "name": "Ficticio", "receiveMarketingEmails": False},
    )
    assert status == 200, body
    finished = vw(
        box,
        "POST",
        "/identity/accounts/register/finish",
        json_body={
            "email": email,
            "emailVerificationToken": body.strip().strip('"'),
            "masterPasswordHash": PASSWORD_HASH,
            "masterPasswordHint": None,
            "userSymmetricKey": "2.AAAAAAAAAAAAAAAAAAAAAA==|AAAA|AAAA",
            "kdf": 1,
            "kdfIterations": 3,
            "kdfMemory": 64,
            "kdfParallelism": 4,
            "userAsymmetricKeys": {"publicKey": "AAAA", "encryptedPrivateKey": "2.AAAA|AAAA|AAAA"},
        },
    )
    assert finished[0] == 200, finished


def login(box, email, network=None, host="vaultwarden:80"):
    status, body, _ = http(
        box,
        network or f"{box.project}_contrasenas-net",
        host,
        "POST",
        "/identity/connect/token",
        form={
            "grant_type": "password",
            "username": email.replace("@", "%40"),
            "password": PASSWORD_HASH.replace("+", "%2B").replace("=", "%3D"),
            "scope": "api%20offline_access",
            "client_id": "web",
            "deviceType": "9",
            "deviceIdentifier": str(uuid.uuid4()),
            "deviceName": "e2e",
        },
    )
    return status, body


@pytest.fixture(scope="module")
def users(contrasenas_box, alta):
    box = contrasenas_box
    admin = vw(box, "POST", "/admin", form={"token": alta["token"]})
    cookie = admin[2].split(";")[0]
    emails = {
        "con2fa": "con2fa@example.test",
        "sin2fa": "sin2fa@example.test",
        "invitado": "invitado@example.test",
    }
    register(box, cookie, emails["con2fa"])
    register(box, cookie, emails["sin2fa"])
    invited = vw(
        box,
        "POST",
        "/admin/invite",
        json_body={"email": emails["invitado"]},
        headers={"Cookie": cookie},
    )
    assert invited[0] == 200
    status, body = login(box, emails["con2fa"])
    assert status == 200, body
    bearer = {"Authorization": f"Bearer {json.loads(body)['access_token']}"}
    got = vw(
        box,
        "POST",
        "/api/two-factor/get-authenticator",
        json_body={"masterPasswordHash": PASSWORD_HASH},
        headers=bearer,
    )
    assert got[0] == 200, got
    data = json.loads(got[1])
    key = data.get("key") or data["authenticator"]["key"]
    token = data.get("userVerificationToken")
    put = vw(
        box,
        "PUT",
        "/api/two-factor/authenticator",
        json_body={"userVerificationToken": token, "key": key, "token": totp(key)},
        headers=bearer,
    )
    assert put[0] == 200, put
    assert login(box, emails["sin2fa"])[0] == 200
    return emails


def export_once(box):
    done = box.exec(
        "contrasenas-export",
        *["ens-evidence", "kit", "contrasenas", "export"],
        *["--data", "/source", "--out", "/export", "--snapshot", "/snapshot"],
    )
    assert done.returncode == 0, done.stdout + done.stderr
    read = volume_run(box, "contrasenas-export", "cat /v/usuarios.json")
    assert read.returncode == 0, read.stderr
    return read.stdout


def test_el_exportador_publica_doble_factor_e_invitados_sin_emails(contrasenas_box, users):
    box = contrasenas_box

    raw = export_once(box)

    doc = json.loads(raw)
    assert doc["format"] == 1 and doc["snapshot_ok"] is True
    assert len(doc["users"]) == 3
    assert sorted((u["second_factor"], u["invited"]) for u in doc["users"]) == [
        (False, False),
        (False, True),
        (True, False),
    ]
    assert not [e for e in users.values() if e in raw or e.split("@")[0] in raw]
    assert not set(doc["users"][0]) & {"email", "name", "password_hash"}


def test_los_permisos_efectivos_de_las_salidas_los_lee_backrest_y_la_app(contrasenas_box, users):
    box = contrasenas_box
    export_once(box)

    listing = volume_run(box, "contrasenas-snapshot", "ls -ln /v", user=UID)
    assert listing.returncode == 0, listing.stderr
    assert "db-" in listing.stdout and "rsa_key" in listing.stdout, listing.stdout
    for line in listing.stdout.splitlines()[1:]:
        assert line.split()[0] == "-rw-------" and line.split()[2] == UID, line
    # backrest: UID 10001, volumen :ro
    assert (
        volume_run(
            box, "contrasenas-snapshot", "cat /v/db-* >/dev/null && cat /v/rsa_key* >/dev/null"
        ).returncode
        == 0
    )
    # otro UID no lee la instantánea
    assert volume_run(box, "contrasenas-snapshot", "cat /v/db-*", user="1234").returncode != 0
    # la app ve el export y no puede escribirlo
    app = box.exec(
        "app", "python", "-I", "-c", "print(open('/run/kit/contrasenas/usuarios.json').read()[:20])"
    )
    assert app.returncode == 0, app.stderr
    denied = box.exec("app", "sh", "-c", "touch /run/kit/contrasenas/x")
    assert denied.returncode != 0
    assert box.exec("app", "ls", "/source").returncode != 0


def test_restaurar_la_instantanea_en_otra_vaultwarden_permite_entrar_con_la_misma_cuenta(
    contrasenas_box, users
):
    box = contrasenas_box
    export_once(box)
    volume = f"{box.project}_vwrestore"
    network = f"{box.project}_vwrestore-net"
    assert run(["docker", "volume", "create", volume]).returncode == 0
    assert run(["docker", "network", "create", "--internal", network]).returncode == 0
    copy = run(
        ["docker", "run", "--rm", "--network", "none", "--user", "0:0"]
        + ["-v", f"{box.project}_contrasenas-snapshot:/s:ro", "-v", f"{volume}:/d", "alpine:3"]
        + ["sh", "-c", "cp /s/db-*.sqlite3 /d/db.sqlite3 && cp /s/rsa_key.pem /d/rsa_key.pem"]
    )
    assert copy.returncode == 0, copy.stderr
    image = run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", box.container("vaultwarden")]
    ).stdout.strip()
    started = run(
        ["docker", "run", "-d", "--name", f"{box.project}-vwrestore", "--network", network]
        + ["--network-alias", "vwrestore", "-v", f"{volume}:/data", "-e", "SIGNUPS_ALLOWED=false"]
        + ["-e", "DISABLE_ICON_DOWNLOAD=true", image]
    )
    assert started.returncode == 0, started.stderr
    for _ in range(40):
        if http(box, network, "vwrestore:80", "GET", "/alive")[0] == 200:
            break
        time.sleep(1)
    status, body = login(box, users["sin2fa"], network=network, host="vwrestore:80")
    assert status == 200, body


# --- cierre ---------------------------------------------------------------------------------


def test_cerrar_alta_quita_el_token_y_comprueba_las_dos_cosas(contrasenas_box, users, alta):
    box = contrasenas_box

    closed = box.sh("kit", "contrasenas", "cerrar-alta", ENS_BOX_HEALTH_TIMEOUT="120")

    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert "Alta cerrada" in closed.stdout + closed.stderr
    assert not (kit_dir(box) / "admin.env").exists() and not (kit_dir(box) / "alta.yaml").exists()
    assert not [
        line for line in container_env(box, "vaultwarden") if line.startswith("ADMIN_TOKEN")
    ]
    status, body, _ = vw(box, "GET", "/admin")
    assert status == 200 and "The admin panel is disabled" in body
    # los usuarios siguen ahí tras recrear (volumen) y el registro libre sigue cerrado
    assert login(box, users["sin2fa"])[0] == 200
    again = box.sh("kit", "contrasenas", "cerrar-alta", ENS_BOX_HEALTH_TIMEOUT="120")
    assert again.returncode == 0, again.stdout + again.stderr


def test_un_update_conserva_el_alta_cerrada_y_el_exportador_sigue(contrasenas_box, users, alta):
    box = contrasenas_box
    before = box.inspect("vaultwarden", "{{.Id}}")

    update = box.sh("update", "--no-backup", box.tags["next"], ENS_BOX_HEALTH_TIMEOUT="120")

    assert update.returncode == 0, update.stdout + update.stderr
    assert box.inspect("vaultwarden", "{{.Id}}") == before
    assert box.inspect("contrasenas-export", "{{.State.Status}}") == "running"
    assert json.loads(export_once(box))["snapshot_ok"] is True


def test_disable_para_los_contenedores_y_conserva_volumenes(contrasenas_box, users, alta):
    box = contrasenas_box

    done = box.sh("kit", "disable", "contrasenas")

    assert done.returncode == 0, done.stdout + done.stderr
    containers = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={box.project}"]
        + ["--filter", "label=com.docker.compose.service=vaultwarden"]
    )
    assert containers.stdout.strip() == ""
    for volume in ("vaultwarden-data", "contrasenas-export", "contrasenas-snapshot"):
        assert run(["docker", "volume", "inspect", f"{box.project}_{volume}"]).returncode == 0
