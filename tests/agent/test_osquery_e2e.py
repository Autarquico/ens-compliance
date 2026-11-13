"""osquery 5.24.1 real contra el servidor real: `pytest -m docker`. Fuera del gate por defecto.

Un contenedor Debian (digest fijado) instala el .deb oficial con el script que imprime
`ens-evidence agent install` y habla con `ens-evidence agent serve` (la imagen del repo) a través
de Caddy con TLS interno: los plugins TLS de osquery exigen HTTPS. Todo vive en una red interna
(sin salida a Internet) y bajo un prefijo único; el finalizador elimina lo que lleva ese prefijo
aunque el test falle.

Sustituciones respecto a un equipo real (las únicas):
  * `curl`: el .deb sale de una caché fuera del repo (caché de pytest), verificada contra el
    sha256 fijado antes de usarla; el script sigue verificando él mismo el sha256 del fichero que
    instala. Cualquier otra URL falla.
  * `systemctl`: un contenedor no tiene systemd. El sustituto arranca `osqueryd` con la misma
    orden que la unidad del paquete (`--flagfile $FLAG_FILE --config_path $CONFIG_FILE`, de
    /etc/default/osqueryd) bajo un bucle que reintenta como `Restart=on-failure` y cuenta los
    reinicios. No emula `CPUQuota=20%`.
  * `--tls_server_certs=<CA de Caddy>` se añade al flagfile al arrancar (solo aquí): el script no
    lo lleva y un equipo real usa el almacén de certificados público.
  * El contenedor no tiene uuid de hardware (sin DMI): osquery genera uno y lo guarda en su base
    de datos. Se genera una vez, antes del script, con `osqueryi` del .deb extraído (sin
    instalarlo) sobre la misma base que usará `osqueryd`.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ens_evidence.agent.install.release import resolve_package

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.docker

DEBIAN = "debian@sha256:268fb24c39bca92d326dd83ea3abebe4a3f7751c804ffef23bb6d857dd343ff8"
CADDY = "caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5"
# sha256 fijados aquí a propósito: si release.yaml cambia sin revisión, el test lo detecta.
DEB_SHA256 = {
    "amd64": "c6a37aec1156712cce0fafeb01a3697a2b73a1863a2b39fc3473b07600aea68d",
    "arm64": "6a14f55b8be802e1e9b10108295ce7df82437e02161f28030ce71711fbf27a64",
}
DOCKER_ARCH = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
SERVER_NAME = "agentes.test"
ASSET = "lab-linux"
HOSTNAME = "lab-linux.lab.example"
CA_PATH = "/etc/ens-test-ca.pem"
TOKEN_FILE = "/root/enroll.token"
ENROLL_SECRET = "/etc/osquery/enroll_secret"
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
WAIT_SECONDS = 300
OUTAGE_SECONDS = 150


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


if not docker_available():
    pytest.skip("Docker no está disponible", allow_module_level=True)


def run(args, *, env=None, input=None, timeout=600, check=True):
    done = subprocess.run(
        args, env=env, input=input, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and done.returncode != 0:
        raise AssertionError(f"{args[:4]} -> {done.returncode}\n{done.stdout}\n{done.stderr}")
    return done


def docker_arch() -> str:
    reported = run(["docker", "info", "--format", "{{.Architecture}}"]).stdout.strip()
    return DOCKER_ARCH[reported]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cached_deb(cache_dir: Path, arch: str) -> Path:
    """El .deb oficial en la caché; siempre verificado contra el sha256 fijado antes de usarlo."""
    package = resolve_package("linux", "deb", arch)
    assert package.sha256 == DEB_SHA256[arch], "release.yaml cambió el sha256 fijado del .deb"
    path = cache_dir / package.filename
    if not path.exists() or sha256_of(path) != package.sha256:
        part = path.with_suffix(".part")
        try:
            with urllib.request.urlopen(package.url, timeout=120) as response:
                part.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            pytest.skip(f"no se pudo descargar {package.filename}: {exc}")
        part.replace(path)
    assert sha256_of(path) == package.sha256, f"el sha256 de {path.name} no coincide con el fijado"
    return path


CURL_STUB = """#!/bin/sh
# Sustituto de curl: solo sirve el paquete fijado, desde la caché verificada.
for last; do :; done
out=
prev=
for arg; do
  [ "$prev" = --output ] && out=$arg
  prev=$arg
done
[ "$last" = "{url}" ] || {{ echo "curl de prueba: URL no permitida: $last" >&2; exit 22; }}
cp /cache/{filename} "$out"
"""

SYSTEMCTL_STUB = f"""#!/bin/sh
# Sustituto de systemctl para un contenedor sin systemd (ver la cabecera del test).
SUPERVISOR=/run/osqueryd-stub.pid
RESTARTS=/run/osqueryd-stub.restarts
stop_daemon() {{
  [ -f "$SUPERVISOR" ] || return 0
  kill -TERM -- "-$(cat "$SUPERVISOR")" 2>/dev/null || true
  rm -f "$SUPERVISOR"
}}
start_daemon() {{
  stop_daemon
  . /etc/default/osqueryd
  grep -q '^--tls_server_certs=' "$FLAG_FILE" || {{ echo '--tls_server_certs={CA_PATH}'; echo --verbose; }} >>"$FLAG_FILE"
  FLAG_FILE=$FLAG_FILE CONFIG_FILE=$CONFIG_FILE RESTARTS=$RESTARTS SUPERVISOR=$SUPERVISOR \\
    setsid sh -c '
      echo $$ >"$SUPERVISOR"
      trap "kill \\$child 2>/dev/null; exit 0" TERM
      while :; do
        /opt/osquery/bin/osqueryd --flagfile "$FLAG_FILE" --config_path "$CONFIG_FILE" \\
          >>/var/log/osqueryd-stub.log 2>&1 &
        child=$!
        wait $child && exit 0
        echo "$(date +%s)" >>"$RESTARTS"
        sleep 1
      done' >/dev/null 2>&1 &
  sleep 1
}}
now=no
for arg; do [ "$arg" = --now ] && now=yes; done
case "$1" in
  enable) [ "$now" = yes ] && start_daemon ;;
  disable) [ "$now" = yes ] && stop_daemon ;;
  start | restart) start_daemon ;;
  stop) stop_daemon ;;
esac
exit 0
"""

# utime+stime (ticks) y pids de todos los procesos osqueryd (vigilante y worker).
PROCESS_PROBE = r"""
n=0; t=0; pids=
for d in /proc/[0-9]*; do
  [ "$(cat "$d/comm" 2>/dev/null)" = osqueryd ] || continue
  set -- $(sed 's/^.*) //' "$d/stat")
  t=$((t + ${12} + ${13})); n=$((n + 1)); pids="$pids ${d#/proc/}"
done
echo "$n $t$pids"
"""

CADDYFILE = f"""{{
	auto_https disable_redirects
}}
{SERVER_NAME} {{
	tls internal
	reverse_proxy server:8000
}}
"""


@dataclass
class Lab:
    prefix: str
    arch: str
    deb: Path
    secrets_env: dict[str, str]

    @property
    def network(self) -> str:
        return f"{self.prefix}-net"

    @property
    def volume(self) -> str:
        return f"{self.prefix}-data"

    @property
    def image(self) -> str:
        return f"{self.prefix}-img"

    def name(self, role: str) -> str:
        return f"{self.prefix}-{role}"

    def exec(self, role, *command, env=(), input=None, check=True, timeout=300):
        flags = [flag for pair in env for flag in ("-e", pair)]
        return run(
            ["docker", "exec", *(["-i"] if input is not None else []), *flags]
            + [self.name(role), *command],
            input=input,
            check=check,
            timeout=timeout,
        )

    def osquery(self, script, **kwargs):
        return self.exec("osq", "sh", "-c", script, env=[f"PATH=/stubs:{SYSTEM_PATH}"], **kwargs)

    def ens(self, *args, **kwargs):
        return self.exec("server", "ens-evidence", *args, **kwargs)

    def hosts_line(self) -> str:
        return next(
            line for line in self.ens("agent", "hosts").stdout.splitlines() if ASSET in line
        )

    def last_contact(self) -> datetime | None:
        match = re.search(r"último contacto: (\S+)", self.hosts_line())
        return None if match is None else datetime.fromisoformat(match[1])

    def manifests(self) -> list[dict]:
        script = (
            "import glob, json, yaml; print(json.dumps([yaml.safe_load(open(f)) for f in "
            "sorted(glob.glob('/data/workspace/evidence/manifests/*.yaml'))], default=str))"
        )
        return json.loads(self.exec("server", "python", "-I", "-c", script).stdout)

    def agent_evidence(self) -> list[dict]:
        return [m for m in self.manifests() if m["source"] == "osquery-agent"]

    def processes(self) -> tuple[int, int, set[str]]:
        count, ticks, *pids = self.osquery(PROCESS_PROBE).stdout.split()
        return int(count), int(ticks), set(pids)

    def restarts(self) -> int:
        out = self.osquery("cat /run/osqueryd-stub.restarts 2>/dev/null | wc -l").stdout
        return int(out)

    def osqueryd_log_tail(self) -> str:
        osquery = self.osquery("tail -n 60 /var/log/osqueryd-stub.log 2>&1", check=False).stdout
        server = run(["docker", "logs", "--tail", "30", self.name("server")], check=False)
        return f"{osquery}\n--- servidor ---\n{server.stdout}{server.stderr}"


def wait_for(description, probe, *, seconds=WAIT_SECONDS, lab=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(5)
    tail = lab.osqueryd_log_tail() if lab else ""
    raise AssertionError(f"no se cumplió en {seconds}s: {description}\n--- osqueryd ---\n{tail}")


def labelled(prefix: str, kind: str) -> list[str]:
    listing = {
        "container": ["docker", "ps", "-aq", "--filter", f"name={prefix}-"],
        "volume": ["docker", "volume", "ls", "-q", "--filter", f"name={prefix}-"],
        "network": ["docker", "network", "ls", "-q", "--filter", f"name={prefix}-"],
        "image": ["docker", "images", "-q", f"{prefix}-img"],
    }[kind]
    return run(listing).stdout.split()


def remove_everything(prefix: str) -> None:
    containers = labelled(prefix, "container")
    if containers:
        run(["docker", "rm", "-f", "-v", *containers], check=False)
    for kind, command in (
        ("volume", ["docker", "volume", "rm", "-f"]),
        ("network", ["docker", "network", "rm"]),
        ("image", ["docker", "rmi", "-f"]),
    ):
        found = labelled(prefix, kind)
        if found:
            run([*command, *found], check=False)


def survivors(prefix: str) -> dict[str, list[str]]:
    kinds = ("container", "volume", "network", "image")
    return {kind: found for kind in kinds if (found := labelled(prefix, kind))}


def build_lab(request, tmp_path_factory, stack: ExitStack) -> Lab:
    arch = docker_arch()
    cache_dir = Path(request.config.cache.mkdir("osquery-5.24.1"))
    lab = Lab(
        prefix=f"ensoq{secrets.token_hex(3)}",
        arch=arch,
        deb=cached_deb(cache_dir, arch),
        # Sintéticos y efímeros: nacen aquí, mueren con el contenedor, nunca se escriben a disco.
        secrets_env={
            "ENS_AGENT_ENROLL_KEY": secrets.token_urlsafe(48),
            "ENS_AGENT_NODE_KEY_SECRET": secrets.token_urlsafe(48),
        },
    )
    stack.callback(remove_everything, lab.prefix)

    work = tmp_path_factory.mktemp("osquery-e2e")
    stubs = work / "stubs"
    stubs.mkdir()
    package = resolve_package("linux", "deb", arch)
    (stubs / "curl").write_text(CURL_STUB.format(url=package.url, filename=package.filename))
    (stubs / "systemctl").write_text(SYSTEMCTL_STUB)
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    caddyfile = work / "Caddyfile"
    caddyfile.write_text(CADDYFILE)

    run(["docker", "build", "-q", "-t", lab.image, str(REPO)], timeout=1800)
    run(["docker", "network", "create", "--internal", lab.network])
    run(["docker", "volume", "create", lab.volume])
    run(
        ["docker", "run", "-d", "--name", lab.name("osq"), "--network", lab.network]
        + ["--hostname", HOSTNAME, "-v", f"{lab.deb.parent}:/cache:ro", "-v", f"{stubs}:/stubs:ro"]
        + [DEBIAN, "sleep", "infinity"]
    )
    run(
        ["docker", "run", "-d", "--name", lab.name("caddy"), "--network", lab.network]
        + ["--network-alias", SERVER_NAME, "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro", CADDY]
    )
    return lab


def seed_workspace(lab: Lab, hardware_uuid: str) -> None:
    settings = {"workspace": {"backend": "local", "local": {"path": "/data/workspace"}}}
    endpoint = {
        "max_evidence_age_days": 30,
        "supported_os": [{"platform": "linux", "name": "Debian GNU/Linux", "release": "12"}],
        "hosts": {ASSET: {"hardware_uuid": hardware_uuid, "hostname": HOSTNAME}},
    }
    files = {"/data/settings.yaml": settings, "/data/workspace/connectors/endpoint.yaml": endpoint}
    for path, content in files.items():
        run(
            ["docker", "run", "--rm", "-i", "-v", f"{lab.volume}:/data", "--entrypoint", "sh"]
            + [lab.image, "-c", f'mkdir -p "$(dirname {path})" && cat >{path}'],
            input=yaml.safe_dump(content),
        )


def start_server(lab: Lab) -> None:
    run(
        ["docker", "run", "-d", "--name", lab.name("server"), "--network", lab.network]
        + ["--network-alias", "server", "-v", f"{lab.volume}:/data"]
        + [flag for name in lab.secrets_env for flag in ("-e", name)]
        + [lab.image, "ens-evidence", "agent", "serve", "--host", "0.0.0.0", "--port", "8000"],
        env={**os.environ, **lab.secrets_env},
    )


@pytest.fixture(scope="module")
def lab(request, tmp_path_factory):
    with ExitStack() as stack:
        lab = build_lab(request, tmp_path_factory, stack)
        yield lab
    assert survivors(lab.prefix) == {}, "quedaron recursos del test"


@dataclass
class Enrolled:
    uuid: str
    install_script: str


@pytest.fixture(scope="module")
def enrolled(lab) -> Enrolled:
    deb = f"/cache/{lab.deb.name}"
    lab.osquery(
        f"mkdir -p /tmp/deb /var/osquery && dpkg-deb -x {deb} /tmp/deb && "
        "/tmp/deb/opt/osquery/bin/osqueryd -S --database_path=/var/osquery/osquery.db "
        '--json "select uuid from system_info" >/tmp/uuid.json'
    )
    hardware_uuid = json.loads(lab.osquery("cat /tmp/uuid.json").stdout)[0]["uuid"]
    lab.osquery("rm -rf /tmp/deb /tmp/uuid.json")

    seed_workspace(lab, hardware_uuid)
    start_server(lab)
    wait_for("el servidor responde", lambda: lab.ens("agent", "hosts", check=False).returncode == 0)
    ca = wait_for(
        "Caddy genera su CA",
        lambda: run(
            ["docker", "exec", lab.name("caddy"), "cat"]
            + ["/data/caddy/pki/authorities/local/root.crt"],
            check=False,
        ).stdout,
    )
    lab.osquery(f"cat >{CA_PATH}", input=ca)

    token = lab.ens("agent", "admit", "--asset", ASSET, "--by", "e2e", "--hours", "1").stdout.strip()
    lab.osquery(f"umask 077; cat >{TOKEN_FILE}", input=token)

    script = lab.ens(
        *("agent", "install", "--asset", ASSET, "--os", "linux", "--package", "deb"),
        *("--arch", lab.arch, "--server", SERVER_NAME, "--token-file", TOKEN_FILE),
    ).stdout
    assert token not in script
    lab.osquery("cat >/root/install.sh", input=script)
    installed = lab.osquery("sh /root/install.sh", check=False)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert token not in installed.stdout + installed.stderr
    return Enrolled(uuid=hardware_uuid, install_script=script)


def test_el_equipo_se_inscribe_y_queda_con_contacto_reciente(lab, enrolled):
    contact = wait_for("agent hosts muestra contacto", lab.last_contact, lab=lab)

    assert datetime.now(UTC) - contact < timedelta(minutes=5)
    assert "admitido" in lab.hosts_line()
    # El .deb real quedó instalado y verificado por el script.
    assert "5.24.1" in lab.osquery("osqueryd --version").stdout


def test_la_primera_instantanea_programada_llega_y_se_guarda(lab, enrolled):
    stored = wait_for("una evidencia osquery-agent", lab.agent_evidence, lab=lab)

    assert all(m["asset_id"] == ASSET for m in stored)


def test_el_fichero_del_token_se_retira_y_osquery_sigue_contactando(lab, enrolled):
    wait_for("agent hosts muestra contacto", lab.last_contact, lab=lab)
    before = lab.last_contact()

    lab.osquery(f"rm -f {TOKEN_FILE}")

    assert lab.osquery(f"test ! -e {TOKEN_FILE}", check=False).returncode == 0
    assert (
        lab.osquery(f"stat -c '%a %U' {ENROLL_SECRET}").stdout.strip() == "600 root"
    ), "el token copiado para osquery debe seguir siendo solo de root"
    wait_for("nuevo contacto", lambda: (lab.last_contact() or before) > before, lab=lab)


def test_un_corte_del_servidor_no_tumba_osquery_y_el_snapshot_actual_se_guarda(lab, enrolled):
    wait_for("una evidencia osquery-agent", lab.agent_evidence, lab=lab)
    stored_before = len(lab.agent_evidence())
    restarts_before = lab.restarts()
    count, _, pids_before = lab.processes()
    assert count >= 1

    run(["docker", "stop", "-t", "10", lab.name("server")])
    outage_start = datetime.now(UTC)
    # Un snapshot nuevo durante el corte: reiniciar osqueryd lo fuerza (el calendario corre al
    # arrancar). Si la configuración remota no estuviera en caché, no habría calendario.
    lab.osquery("systemctl restart osqueryd")
    time.sleep(OUTAGE_SECONDS)
    count, ticks_after, pids_after = lab.processes()

    # Medición del corte: CPU de todos los osqueryd, en % de un núcleo, desde el reinicio.
    elapsed = OUTAGE_SECONDS + 1
    ticks_per_second = int(lab.osquery("getconf CLK_TCK").stdout)
    cpu_percent = ticks_after / ticks_per_second / elapsed * 100
    print(
        f"\n[medición] corte {OUTAGE_SECONDS}s: osqueryd vivos={count} "
        f"pids antes={sorted(pids_before)} después={sorted(pids_after)} "
        f"CPU={cpu_percent:.2f}% de un núcleo, reinicios del bucle={lab.restarts() - restarts_before}"
    )
    assert count >= 1, "osqueryd debe seguir corriendo durante el corte"
    assert lab.restarts() == restarts_before, "osqueryd no debe morir por el corte"
    assert cpu_percent < 20, "por encima de la cuota CPUQuota=20% de la unidad del paquete"

    run(["docker", "start", lab.name("server")])
    wait_for("el servidor responde", lambda: lab.ens("agent", "hosts", check=False).returncode == 0)

    def current_snapshot_stored():
        return [
            m
            for m in lab.agent_evidence()
            if datetime.fromisoformat(str(m["collected_at"])) > outage_start
        ]

    stored = wait_for(
        "el snapshot generado durante el corte", current_snapshot_stored, seconds=900, lab=lab
    )
    assert len(lab.agent_evidence()) > stored_before
    assert all(m["asset_id"] == ASSET for m in stored)
    assert lab.restarts() == restarts_before


def test_no_queda_nada_de_la_prueba_en_los_ficheros_del_equipo_ni_en_el_log(lab, enrolled):
    log = lab.osquery("cat /var/log/osqueryd-stub.log")
    token_copy = lab.osquery(f"cat {ENROLL_SECRET}").stdout
    assert token_copy and token_copy not in log.stdout
