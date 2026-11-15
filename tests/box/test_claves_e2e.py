"""Kit de claves de extremo a extremo (`pytest -m docker`, fuera del gate por defecto).

`render_bundle` -> `box.sh kit enable claves` -> `kit claves init` (pseudo-terminal real: el
contenedor abre /dev/tty y las partes se teclean como las teclearía un custodio) -> OpenBao 2.7.2
real (la imagen fijada por digest en compose.yaml) -> el conector `keystore` lee con el token de la
app. OpenBao solo es alcanzable por la red interna `claves-app`: las peticiones salen del
contenedor `app` (que está en esa red) con `docker exec`, igual que las haría la app.
"""

# ruff: noqa: F401, F811  (fixtures de la caja importadas de test_integration)
import json
import os
import pty
import re
import secrets
import select
import subprocess
import time
from datetime import UTC, datetime

import httpx
import pytest

from box.test_integration import box, commit_all, installed, run
from ens_evidence.connectors.keystore import KeystoreConnector, KeystoreRun
from ens_evidence.connectors.keystore import checks as keystore_checks
from ens_evidence.connectors.keystore.connector import TOKEN_ENV
from ens_evidence.kit.keystore import KeystoreInputs, render_bundle
from ens_evidence.models.enums import CheckStatus

pytestmark = pytest.mark.docker
CLAVES_HOST = "claves.test.example"
UID = "10001"
CONFIRMATION = "ENTIENDO"

REQUEST_SCRIPT = """
import json, sys, urllib.request, urllib.error
spec = json.load(sys.stdin)
data = spec["body"].encode() if spec["body"] else None
request = urllib.request.Request(
    spec["url"], data=data, headers=spec["headers"], method=spec["method"]
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=20) as reply:
        status, body = reply.status, reply.read().decode()
except urllib.error.HTTPError as exc:
    status, body = exc.code, exc.read().decode()
json.dump({"status": status, "body": body}, sys.stdout)
"""


def run_with_input(args, input_text):
    return subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=60)


class ViaApp(httpx.BaseTransport):
    """Transporte httpx que hace cada petición desde el contenedor `app` (red claves-app)."""

    def __init__(self, container):
        self.container = container

    def handle_request(self, request):
        spec = {
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": request.content.decode(),
        }
        done = run_with_input(
            ["docker", "exec", "-i", self.container, "python", "-I", "-c", REQUEST_SCRIPT],
            json.dumps(spec),
        )
        assert done.returncode == 0, done.stderr
        reply = json.loads(done.stdout)
        return httpx.Response(reply["status"], content=reply["body"].encode())


class Session:
    """`sh deploy/box.sh ...` con stdin y stdout en un pseudo-terminal, con expect/send."""

    def __init__(self, box, *args):
        master, slave = pty.openpty()
        self.master = master
        self.proc = subprocess.Popen(
            ["sh", "deploy/box.sh", *args],
            cwd=box.clone,
            env=box.env(),
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave)
        self.buffer = ""
        self.position = 0

    def expect(self, pattern, timeout=120):
        found = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = found.search(self.buffer, self.position)
            if match:
                self.position = match.end()
                return match
            ready, _, _ = select.select([self.master], [], [], 1)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            self.buffer += chunk.decode(errors="replace")
        raise AssertionError(f"no apareció {pattern!r}; salida:\n{self.buffer[-1500:]}")

    def send(self, text):
        os.write(self.master, text.encode())

    def finish(self, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 1)
            if ready:
                try:
                    chunk = os.read(self.master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.buffer += chunk.decode(errors="replace")
            elif self.proc.poll() is not None:
                break
        code = self.proc.wait(timeout=30)
        stderr = self.proc.stderr.read()
        os.close(self.master)
        return code, self.buffer, stderr


@pytest.fixture(scope="module")
def claves_box(box, installed):
    assert installed.returncode == 0, installed.stdout + installed.stderr
    ui_env = box.clone / "deploy" / "ui.env"
    ui_env.write_text(
        "ENS_UI_HOST=ui.test.example\nENS_UI_ORIGIN=https://ui.test.example\n"
        f"ENS_UI_ACCESS_AUD=x\nENS_UI_ACCESS_TEAM_DOMAIN=x.example\nENS_CLAVES_HOST={CLAVES_HOST}\n"
        "ENS_UI_ACCESS_JWKS_FILE=/data/state/ui/access-jwks.json\n"
    )
    ui_env.chmod(0o600)
    yield box
    owned = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={box.project}"]
    ).stdout.split()
    if owned:
        run(["docker", "rm", "-f", *owned])
    run(["docker", "rm", "-f", f"{box.project}-claves-init"])
    for network in ("claves-net", "claves-app"):
        run(["docker", "network", "rm", f"{box.project}_{network}"])
    for volume in ("openbao-data", "openbao-audit"):
        run(["docker", "volume", "rm", "-f", f"{box.project}_{volume}"])
    run(["docker", "rmi", "-f", f"ens-evidence:{next_tag(box, 1)}"])  # la del test de K12


@pytest.fixture(scope="module")
def package(claves_box, tmp_path_factory):
    inputs = KeystoreInputs(asset_id="openbao", key_shares=3, key_threshold=2, max_key_age_days=90)
    path = tmp_path_factory.mktemp("paquete") / "claves.tar.gz"
    path.write_bytes(render_bundle(inputs, datetime.now(UTC)))
    return path


@pytest.fixture(scope="module")
def enabled(claves_box, package):
    result = claves_box.sh("kit", "enable", "claves", str(package), ENS_BOX_HEALTH_TIMEOUT="120")
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def initialized(claves_box, enabled):
    session = Session(claves_box, "kit", "claves", "init")
    session.expect(f"Escribe {CONFIRMATION} para continuar: ")
    session.send(CONFIRMATION + "\n")
    shares = []
    for number in (1, 2, 3):
        session.expect(rf"parte {number} de 3 a su custodio\. Intro para verla: ")
        session.send("\n")
        part = session.expect(rf"Parte {number} de 3:\s+(\S+)\s").group(1)
        shares.append(part)
        session.expect("Intro para borrar la pantalla: ")
        session.send("\n")
    for part in shares[:2]:
        session.expect(r"Parte de desbloqueo \(oculta, vacío para cancelar\): ")
        session.send(part + "\n")
    code, output, stderr = session.finish()
    assert code == 0, output + stderr
    return {"shares": shares, "output": output, "stderr": stderr}


def env_token(box):
    for line in (box.clone / "deploy" / ".env").read_text().splitlines():
        if line.startswith("ENS_KEYSTORE_TOKEN="):
            return line.split("=", 1)[1]
    raise AssertionError("falta ENS_KEYSTORE_TOKEN en deploy/.env")


def api(box, method, path, token=None, body=None):
    """(estado, cuerpo JSON) de una petición a OpenBao desde el contenedor app."""
    headers = {"X-Vault-Token": token} if token else {}
    done = ViaApp(box.container("app")).handle_request(
        httpx.Request(
            method,
            f"http://openbao:8200/v1/{path}",
            headers=headers,
            content=json.dumps(body) if body is not None else b"",
        )
    )
    return done.status_code, json.loads(done.text) if done.text else {}


REASONS = {}  # último motivo de cada check, para los mensajes de aserción


def collect_statuses(box):
    config = KeystoreRun(asset_id="openbao", max_key_age_days=90)
    connector = KeystoreConnector(
        transport=ViaApp(box.container("app")),
        env={TOKEN_ENV: env_token(box)},
    )
    result = connector.collect(config)
    now = datetime.now(UTC)
    statuses = {}
    for check in (
        keystore_checks.unsealed,
        keystore_checks.audit_enabled,
        keystore_checks.keys_rotated,
        keystore_checks.no_root_token,
        keystore_checks.app_token_scoped,
    ):
        (outcome,) = check(result, "ev-1", now)
        statuses[outcome.check_id] = outcome.status
        REASONS[outcome.check_id] = outcome.reason
    return statuses


def volume_listing(box, volume, *args):
    return run(
        ["docker", "run", "--rm", "--network", "none", "-v", f"{box.project}_{volume}:/v:ro"]
        + ["alpine:3", *args]
    )


def test_openbao_arranca_con_el_hcl_del_paquete_sin_root_y_sin_inicializar(claves_box, enabled):
    assert claves_box.inspect("openbao", "{{.State.Status}}") == "running"
    assert claves_box.inspect("openbao", "{{.Config.User}}") == f"{UID}:{UID}"
    assert claves_box.inspect("openbao", "{{.HostConfig.ReadonlyRootfs}}") == "true"
    assert claves_box.inspect("openbao", "{{json .HostConfig.CapDrop}}") == '["ALL"]'
    assert claves_box.inspect("openbao", "{{json .HostConfig.PortBindings}}") in ("{}", "null")
    status, body = api(claves_box, "GET", "sys/seal-status")
    assert status == 200 and body["initialized"] is False and body["sealed"] is True


def test_el_uid_10001_escribe_en_raft_y_el_config_es_el_del_paquete(claves_box, enabled):
    stat = volume_listing(claves_box, "openbao-data", "stat", "-c", "%u", "/v")
    assert stat.stdout.strip() == UID, stat.stderr
    raft = volume_listing(claves_box, "openbao-data", "sh", "-c", "ls -ln /v /v/raft")
    assert UID in raft.stdout, raft.stdout + raft.stderr
    installed_hcl = (claves_box.clone / "deploy" / "kit" / "claves" / "openbao.hcl").read_text()
    release_hcl = (claves_box.clone / "deploy" / "openbao" / "config.hcl").read_text()
    assert installed_hcl == release_hcl


def test_init_por_box_sh_termina_bien_y_deja_el_token_solo_en_el_env(claves_box, initialized):
    output = initialized["output"]
    assert "Contraseña: " in output
    token = env_token(claves_box)
    env_file = claves_box.clone / "deploy" / ".env"
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert token not in output + initialized["stderr"]
    for part in initialized["shares"]:
        assert part not in output.split("Contraseña: ")[-1]
    status, body = api(claves_box, "GET", "sys/seal-status")
    assert (body["initialized"], body["sealed"], body["t"], body["n"]) == (True, False, 2, 3)
    leftovers = run(["docker", "ps", "-aq", "--filter", f"name={claves_box.project}-claves-init"])
    assert leftovers.stdout.strip() == ""


def test_la_politica_hcl_y_los_tres_hcl_se_aceptan_y_el_root_no_existe(claves_box, initialized):
    password = re.search(r"Contraseña: (\S+)", initialized["output"]).group(1)
    status, login = api(
        claves_box,
        "POST",
        "auth/userpass/login/admin",
        body={"password": password},
    )
    assert status == 200, login
    admin = login["auth"]["client_token"]
    assert sorted(login["auth"]["policies"]) == ["claves-admin", "default"]
    for name in ("ens-app-metadata", "claves-admin"):
        status, body = api(claves_box, "GET", f"sys/policies/acl/{name}", admin)
        assert status == 200 and "path" in body["data"]["policy"], name
    # config.hcl ya lo aceptó el servidor al arrancar; sin root, nadie puede reconfigurar
    status, _ = api(claves_box, "PUT", "sys/policies/acl/otra", admin, {"policy": "x"})
    assert status == 403


def test_el_token_de_la_app_no_lee_valores_ni_revoca_tokens(claves_box, initialized):
    token = env_token(claves_box)

    status, _ = api(claves_box, "GET", "kv/data/x", token)
    assert status == 403
    status, _ = api(claves_box, "POST", "auth/token/revoke-accessor", token, {"accessor": "x"})
    assert status == 403
    paths = ["kv/data/x", "kv/metadata/x", "transit/keys/k", "transit/export/signing-key/k"]
    status, body = api(claves_box, "POST", "sys/capabilities-self", token, {"paths": paths})
    assert status == 200
    assert body["data"]["kv/data/x"] == ["deny"]
    assert body["data"]["transit/export/signing-key/k"] == ["deny"]
    assert "read" in body["data"]["kv/metadata/x"]


def test_la_auditoria_file_esta_activa_y_ha_registrado_el_primer_desbloqueo(
    claves_box, initialized
):
    status, devices = api(claves_box, "GET", "sys/audit", env_token(claves_box))
    assert status == 200
    assert "archivo/" in devices.get("data", devices)
    log = volume_listing(claves_box, "openbao-audit", "sh", "-c", "ls -ln /v; wc -l /v/audit.log")
    assert UID in log.stdout and "audit.log" in log.stdout, log.stdout + log.stderr
    assert "-rw-------" in volume_listing(claves_box, "openbao-audit", "ls", "-l", "/v").stdout


def test_conector_sin_claves_gestionadas_key_003_falla_y_el_resto_pasa(claves_box, initialized):
    statuses = collect_statuses(claves_box)

    assert statuses["keystore.unsealed"] is CheckStatus.PASS, statuses
    assert statuses["keystore.audit_enabled"] is CheckStatus.PASS, statuses
    assert statuses["keystore.keys_rotated"] is CheckStatus.FAIL, statuses
    assert statuses["keystore.no_root_token"] is CheckStatus.PASS, statuses
    assert statuses["keystore.app_token_scoped"] is CheckStatus.PASS, REASONS[
        "keystore.app_token_scoped"
    ]


def admin_token(box, initialized):
    password = re.search(r"Contraseña: (\S+)", initialized["output"]).group(1)
    status, login = api(box, "POST", "auth/userpass/login/admin", body={"password": password})
    assert status == 200, login
    return login["auth"]["client_token"]


def test_con_un_secreto_y_una_clave_gestionados_key_003_pasa_con_las_formas_reales(
    claves_box, initialized
):
    admin = admin_token(claves_box, initialized)
    assert api(claves_box, "POST", "kv/data/app", admin, {"data": {"clave": "valor-e2e"}})[0] == 200
    assert api(claves_box, "POST", "transit/keys/firma", admin, {"type": "ed25519"})[0] == 200

    statuses = collect_statuses(claves_box)

    assert statuses["keystore.keys_rotated"] is CheckStatus.PASS, REASONS["keystore.keys_rotated"]
    assert statuses["keystore.no_root_token"] is CheckStatus.PASS, REASONS
    # el valor no sale de OpenBao hacia la evidencia
    config = KeystoreRun(asset_id="openbao", max_key_age_days=90)
    connector = KeystoreConnector(
        transport=ViaApp(claves_box.container("app")), env={TOKEN_ENV: env_token(claves_box)}
    )
    assert "valor-e2e" not in connector.collect(config).model_dump_json()


def test_la_ram_en_reposo_de_openbao_se_mide(claves_box, initialized):
    stats = run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}"]
        + [claves_box.container("openbao")]
    )
    print(f"RAM openbao en reposo: {stats.stdout.strip()}")
    assert re.search(r"\d+(\.\d+)?(MiB|GiB|KiB)", stats.stdout), stats.stdout


def release_with(box, tag, replacement):
    """Una etiqueta cuyo compose.yaml difiere solo en el servicio openbao (o en otro)."""
    compose = box.clone / "compose.yaml"
    original = compose.read_text(encoding="utf-8")
    assert replacement[0] in original
    compose.write_text(original.replace(*replacement, 1), encoding="utf-8")
    commit_all(box.clone, tag, tag)
    compose.write_text(original, encoding="utf-8")
    run(["git", "checkout", "-q", "-f", box.tags["good"]], cwd=box.clone, check=True)
    return tag


def test_un_update_que_no_toca_openbao_no_lo_recrea_ni_pide_confirmacion(claves_box, initialized):
    before = claves_box.inspect("openbao", "{{.Id}}")
    label = claves_box.inspect(
        "openbao", '{{index .Config.Labels "com.docker.compose.config-hash"}}'
    )
    assert re.fullmatch(r"[0-9a-f]{64}", label), label

    update = claves_box.sh(
        "update", "--no-backup", claves_box.tags["next"], ENS_BOX_HEALTH_TIMEOUT="120"
    )

    assert update.returncode == 0, update.stdout + update.stderr
    assert "SELLADO" not in update.stderr
    assert claves_box.inspect("openbao", "{{.Id}}") == before


def test_docker_restart_deja_openbao_sellado_y_el_conector_falla_key_001(claves_box, initialized):
    container = claves_box.container("openbao")
    restarted = run(["docker", "restart", container])
    assert restarted.returncode == 0, restarted.stderr
    for _ in range(30):
        status, body = api(claves_box, "GET", "sys/seal-status")
        if status == 200:
            break
        time.sleep(1)
    assert body["sealed"] is True

    statuses = collect_statuses(claves_box)
    sealed = run(
        ["docker", "exec", "-e", "BAO_ADDR=http://127.0.0.1:8200", container, "bao", "status"]
    )

    assert statuses["keystore.unsealed"] is CheckStatus.FAIL, statuses
    assert sealed.returncode == 2, sealed.stdout + sealed.stderr
    assert "true" in sealed.stdout.split("Sealed")[1].splitlines()[0]


def test_unseal_por_box_sh_con_dos_partes_vuelve_a_dejarlo_desellado(claves_box, initialized):
    session = Session(claves_box, "kit", "claves", "unseal")
    for part in initialized["shares"][1:]:
        session.expect(r"Parte de desbloqueo \(oculta, vacío para cancelar\): ")
        session.send(part + "\n")
    code, output, stderr = session.finish()

    assert code == 0, output + stderr
    assert "Almacén desbloqueado" in output
    assert collect_statuses(claves_box)["keystore.unsealed"] is CheckStatus.PASS


def next_tag(box, minor):
    prefix = box.tags["good"].rsplit(".", 2)[0]
    return f"{prefix}.{minor}.0"


def test_update_que_recrearia_openbao_se_para_sin_terminal_y_con_seal_ok_lo_sella(
    claves_box, initialized
):
    box = claves_box
    tag = release_with(
        box,
        next_tag(box, 1),
        (
            "    command: [server, -config=/openbao/config/config.hcl]\n",
            "    command: [server, -config=/openbao/config/config.hcl]\n"
            "    labels:\n      ens.test.k12: nueva\n",
        ),
    )
    before = box.inspect("openbao", "{{.Id}}")
    registered = box.registry(box.tags["next"])["current"]["tag"]

    refused = box.sh("update", "--no-backup", tag, ENS_BOX_HEALTH_TIMEOUT="120")

    assert refused.returncode != 0
    assert "No hay terminal" in refused.stderr and "SELLADO" in refused.stderr
    assert box.inspect("openbao", "{{.Id}}") == before
    assert box.registry(box.tags["next"])["current"]["tag"] == registered

    accepted = box.sh("update", "--no-backup", "--seal-ok", tag, ENS_BOX_HEALTH_TIMEOUT="120")

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert box.inspect("openbao", "{{.Id}}") != before
    assert box.inspect("openbao", '{{index .Config.Labels "ens.test.k12"}}') == "nueva"
    assert api(box, "GET", "sys/seal-status")[1]["sealed"] is True


def test_rollback_que_recrearia_openbao_pide_lo_mismo_que_update(claves_box, initialized):
    box = claves_box
    for _ in range(30):  # recién recreado: la API tarda en escuchar
        if api(box, "GET", "sys/seal-status")[0] == 200:
            break
        time.sleep(1)
    before = box.inspect("openbao", "{{.Id}}")

    refused = box.sh("rollback", ENS_BOX_HEALTH_TIMEOUT="120")

    assert refused.returncode != 0
    assert "No hay terminal" in refused.stderr and "SELLADO" in refused.stderr
    assert box.inspect("openbao", "{{.Id}}") == before

    accepted = box.sh("rollback", "--seal-ok", ENS_BOX_HEALTH_TIMEOUT="120")

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert box.inspect("openbao", "{{.Id}}") != before
    assert box.inspect("openbao", '{{index .Config.Labels "ens.test.k12"}}') == ""
