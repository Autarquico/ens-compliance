"""Contrato de la caja que se comprueba sin Docker: compose, Caddyfile, Dockerfile y box.sh."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BOX_SH = REPO / "deploy" / "box.sh"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((REPO / "compose.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def services(compose):
    return compose["services"]


@pytest.fixture(scope="module")
def dockerfile():
    return (REPO / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def caddyfile():
    return (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")


def external_services(services):
    return {n: s for n, s in services.items() if not s["image"].startswith("ens-evidence:")}


def test_las_imagenes_externas_estan_fijadas_por_digest(services):
    external = external_services(services)

    assert {"caddy", "cloudflared"} <= set(external)
    unpinned = [s["image"] for s in external.values() if not DIGEST.search(s["image"])]
    assert unpinned == []


def test_las_imagenes_base_del_dockerfile_estan_fijadas_por_digest(dockerfile):
    args = dict(re.findall(r"^ARG (\w+)=(\S+)$", dockerfile, re.MULTILINE))
    stages = set(re.findall(r"^FROM .+ AS (\w+)$", dockerfile, re.MULTILINE))
    bases = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)

    external = [
        re.sub(r"\$\{(\w+)\}", lambda m: args[m.group(1)], base)
        for base in bases
        if base not in stages
    ]

    assert external
    assert [image for image in external if not DIGEST.search(image)] == []


def test_ningun_servicio_publica_puertos(services):
    assert [n for n, s in services.items() if "ports" in s] == []


def test_ningun_servicio_usa_la_red_ni_el_pid_del_host(services):
    host = [n for n, s in services.items() if "host" in (s.get("network_mode"), s.get("pid"))]
    assert host == []


def test_todos_los_servicios_sin_capacidades_ni_privilegios_nuevos(services):
    for name, service in services.items():
        assert service.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in service.get("security_opt", []), name
        assert not service.get("privileged", False), name


def test_ningun_servicio_monta_el_socket_de_docker(services):
    mounts = [str(v) for s in services.values() for v in s.get("volumes", [])]
    assert [m for m in mounts if "docker.sock" in m] == []


def test_el_unico_montaje_del_host_es_el_caddyfile_en_solo_lectura(services):
    mounts = [str(v) for s in services.values() for v in s.get("volumes", [])]
    host_mounts = [m for m in mounts if m.startswith((".", "/", "~"))]

    assert host_mounts == ["./deploy/Caddyfile:/etc/caddy/Caddyfile:ro"]


def test_las_credenciales_solo_llegan_a_quien_las_necesita(services):
    env_files = {n: s["env_file"] for n, s in services.items() if "env_file" in s}

    assert env_files == {
        "app": "${ENS_ENV_FILE:-deploy/.env}",
        "agent": "${ENS_AGENT_ENV_FILE:-deploy/agent.env}",
        "ui": "${ENS_UI_ENV_FILE:-deploy/ui.env}",
        "cloudflared": "${ENS_TUNNEL_ENV_FILE:-deploy/tunnel.env}",
    }


def test_cloudflared_solo_arranca_con_el_perfil_tunnel(services):
    assert services["cloudflared"]["profiles"] == ["tunnel"]
    assert "profiles" not in services["app"]
    assert "profiles" not in services["caddy"]


def test_el_scheduler_tiene_init_reinicio_y_parada_con_margen(services):
    app = services["app"]

    assert app["init"] is True
    assert app["restart"] == "unless-stopped"
    assert app["stop_grace_period"] == "120s"
    assert "healthcheck" in app


def test_todos_los_servicios_rotan_sus_logs(services):
    for name, service in services.items():
        options = service.get("logging", {}).get("options", {})
        assert options.get("max-size") and options.get("max-file"), name


def test_caddy_no_corre_como_root_ni_necesita_volumenes_persistentes(compose, services):
    caddy = services["caddy"]

    assert caddy["user"].split(":")[0] not in ("0", "root")
    assert "caddy-data" not in compose["volumes"] and "caddy-config" not in compose["volumes"]


def test_el_latido_del_scheduler_es_de_cada_contenedor_no_del_volumen(services):
    app = services["app"]

    assert "/tmp" in app["tmpfs"]
    assert app["environment"]["ENS_HEARTBEAT_FILE"].startswith("/tmp/")
    assert not any(
        "/data" in str(part) and "heartbeat" in str(part) for part in app["healthcheck"]["test"]
    )


def test_caddy_no_tiene_salida_a_internet(compose, services):
    networks = services["caddy"]["networks"]

    assert set(networks) == {"edge", "agent", "ui-net"}
    assert all(compose["networks"][n].get("internal") is True for n in networks)


def test_agent_solo_arranca_con_el_perfil_agents(services):
    assert services["agent"]["profiles"] == ["agents"]


def test_agent_nunca_recibe_las_credenciales_de_los_conectores(services):
    agent = services["agent"]

    assert agent["env_file"] == "${ENS_AGENT_ENV_FILE:-deploy/agent.env}"
    assert "ENS_ENV_FILE" not in str(agent)
    assert "environment" not in agent


def test_agent_corre_endurecido_y_solo_en_redes_internas(compose, services):
    agent = services["agent"]

    assert agent["read_only"] is True
    assert "/tmp" in agent["tmpfs"]
    assert agent["init"] is True
    assert agent["restart"] == "unless-stopped"
    assert "ports" not in agent
    assert agent["networks"] == ["agent"]
    assert compose["networks"]["agent"]["internal"] is True
    assert agent["volumes"] == ["data:/data"]
    assert agent["command"][:3] == ["ens-evidence", "agent", "serve"]


def test_agent_se_sondea_en_healthz_sin_curl_ni_wget(services):
    test = services["agent"]["healthcheck"]["test"]

    assert test[:3] == ["CMD", "python", "-c"]
    assert "127.0.0.1:8000/healthz" in test[3]


def test_la_imagen_instala_los_extras_server_y_ui(dockerfile):
    lines = re.findall(r"^RUN uv sync .*$", dockerfile, re.M)

    assert lines
    assert all("--extra server" in line and "--extra ui" in line for line in lines)


def test_ui_solo_arranca_con_el_perfil_ui(services):
    assert services["ui"]["profiles"] == ["ui"]


def test_ui_nunca_recibe_las_credenciales_de_los_conectores(services):
    ui = services["ui"]

    assert ui["env_file"] == "${ENS_UI_ENV_FILE:-deploy/ui.env}"
    assert "ENS_ENV_FILE" not in str(ui)
    assert "environment" not in ui


def test_ui_corre_endurecido_sin_puertos_y_solo_en_una_red_interna(compose, services):
    ui = services["ui"]

    assert ui["read_only"] is True
    assert "/tmp" in ui["tmpfs"]
    assert ui["init"] is True
    assert ui["restart"] == "unless-stopped"
    assert "ports" not in ui
    assert ui["networks"] == ["ui-net"]
    assert compose["networks"]["ui-net"]["internal"] is True
    assert ui["volumes"] == ["data:/data"]
    assert ui["command"][:3] == ["ens-evidence", "ui", "serve"]


def test_ui_se_sondea_en_healthz_sin_curl_ni_wget(services):
    test = services["ui"]["healthcheck"]["test"]

    assert test[:3] == ["CMD", "python", "-c"]
    assert "127.0.0.1:8000/healthz" in test[3]


def test_todas_las_redes_a_las_que_llega_un_servicio_con_datos_son_internas(compose, services):
    for name in ("agent", "ui"):
        assert all(
            compose["networks"][n].get("internal") is True for n in services[name]["networks"]
        )


def test_solo_app_y_cloudflared_tienen_salida_a_internet(compose, services):
    outbound = {
        name
        for name, service in services.items()
        if any(not compose["networks"][n].get("internal") for n in service["networks"])
    }

    assert outbound == {"app", "cloudflared"}


AGENT_PATHS = [
    "/agent/enroll",
    "/agent/config",
    "/agent/log",
    "/agent/distributed/read",
    "/agent/distributed/write",
]


def block(caddyfile, opening):
    """El cuerpo de `opening { ... }` al nivel de indentación del Caddyfile (una tabulación)."""
    found = re.search(rf"\t{re.escape(opening)} \{{\n(.*?)\n\t\}}", caddyfile, re.S)
    assert found, opening
    return found.group(1)


def test_caddy_enruta_la_ui_por_nombre_de_host(caddyfile):
    assert re.search(r"@ui host \{\$ENS_UI_HOST:ui\.invalid\}", caddyfile)
    ui = block(caddyfile, "handle @ui")
    assert "reverse_proxy ui:8000" in ui
    assert re.search(r"request_body \{\s*max_size 1MB", ui)
    assert caddyfile.count("reverse_proxy") == 2


def test_caddy_enruta_los_agentes_por_nombre_de_host(caddyfile):
    for matcher in ("@agent_any", "@encoded"):
        assert "host {$ENS_AGENT_HOST:agent.invalid}" in block(caddyfile, matcher)


def test_caddy_pasa_el_jwt_de_access_a_la_ui_y_lo_quita_hacia_el_agente(caddyfile):
    ui = block(caddyfile, "handle @ui")
    agent = block(caddyfile, "handle @agent_any")

    assert "header_up -Cf-Access" not in ui
    assert "header_up -Cf-Access-*" in agent
    assert "header_up -Cookie" in ui and "header_up -Cookie" in agent


def test_caddy_descarta_x_forwarded_del_cliente_hacia_la_ui(caddyfile):
    assert "header_up -X-Forwarded-*" in block(caddyfile, "handle @ui")


def test_caddy_solo_enruta_los_cinco_post_del_protocolo(caddyfile):
    matcher = re.search(r"@agent_any \{(.*?)\n\t\}", caddyfile, re.S)

    assert matcher
    assert "method POST" in matcher.group(1)
    paths = re.search(r"path (.+)", matcher.group(1)).group(1).split()
    assert paths == AGENT_PATHS
    assert "reverse_proxy agent:8000" in caddyfile


def test_caddy_rechaza_content_encoding_con_415(caddyfile):
    assert re.search(r"@encoded \{.*?header Content-Encoding \*.*?\}", caddyfile, re.S)
    assert re.search(r"handle @encoded \{\s*respond .* 415", caddyfile)
    assert caddyfile.index("handle @encoded") < caddyfile.index("handle @agent_any")
    assert caddyfile.index("handle @ui") < caddyfile.index("handle {")


def test_caddy_limita_el_cuerpo_por_ruta(caddyfile):
    small = re.search(r"@small_agent \{.*?path (.+?)\n", caddyfile, re.S).group(1).split()
    large = re.search(r"@large_agent \{.*?path (.+?)\n", caddyfile, re.S).group(1).split()

    assert small == ["/agent/enroll", "/agent/config", "/agent/distributed/read"]
    assert large == ["/agent/log", "/agent/distributed/write"]
    assert re.search(r"request_body @small_agent \{\s*max_size 64KB", caddyfile)
    assert re.search(r"request_body @large_agent \{\s*max_size 10MB", caddyfile)


def test_caddy_no_reenvia_cookies_ni_cabeceras_de_access(caddyfile):
    assert "header_up -Cookie" in caddyfile
    assert "header_up -Cf-Access-*" in caddyfile


def test_caddy_mantiene_403_por_defecto_y_503_si_el_agente_cae(caddyfile):
    assert re.search(r"handle \{\s*respond \"Forbidden\" 403\s*\}", caddyfile)
    assert re.search(r"handle_errors \{.*?502, 503, 504.*?503", caddyfile, re.S)


def test_caddy_sigue_recortando_query_y_cabeceras_en_el_log(caddyfile):
    assert 'request>uri regexp \\?.*$ ""' in caddyfile
    assert "request>headers delete" in caddyfile


SECRET_NAME = re.compile(r"token|secret|password|passwd|api_?key|credential", re.IGNORECASE)
TOKEN_SHAPES = re.compile(r"ghp_\w{20,}|github_pat_\w+|AKIA[0-9A-Z]{16}|eyJ[\w-]{20,}\.[\w-]{10,}")


def test_no_hay_credenciales_literales_en_la_caja(services):
    literal = []
    for name, service in services.items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment if "=" in item)
        literal += [
            f"{name}.{key}"
            for key, value in environment.items()
            if SECRET_NAME.search(key) and not str(value).startswith("${")
        ]
    assert literal == []

    for path in ("compose.yaml", "Dockerfile", "deploy/Caddyfile", "deploy/box.sh"):
        assert not TOKEN_SHAPES.search((REPO / path).read_text(encoding="utf-8")), path


@pytest.mark.parametrize(
    "example",
    [
        "deploy/.env.example",
        "deploy/tunnel.env.example",
        "deploy/agent.env.example",
        "deploy/ui.env.example",
    ],
)
def test_los_ejemplos_de_entorno_no_traen_valores(example):
    lines = (REPO / example).read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]

    assert [line for line in assignments if line.partition("=")[2].strip()] == []


def test_los_ficheros_env_reales_no_se_suben_a_git_pero_los_ejemplos_si():
    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO).returncode == 0

    assert ignored("deploy/.env")
    assert ignored("deploy/tunnel.env")
    assert ignored("deploy/agent.env")
    assert ignored("deploy/ui.env")
    assert not ignored("deploy/.env.example")
    assert not ignored("deploy/tunnel.env.example")
    assert not ignored("deploy/agent.env.example")
    assert not ignored("deploy/ui.env.example")


def test_restic_se_verifica_por_sha256_antes_de_entrar_en_la_imagen(dockerfile):
    hashes = re.findall(r"RESTIC_SHA256_\w+=([0-9a-f]{64})", dockerfile)

    assert len(hashes) >= 2
    verification = re.search(r"hexdigest\(\)\s*!=\s*expected.*?SystemExit", dockerfile, re.DOTALL)
    assert verification, "el build debe abortar si el sha256 no coincide"


def test_la_imagen_corre_sin_root_con_uid_fijo(dockerfile):
    assert re.search(r"^ARG ENS_UID=10001$", dockerfile, re.MULTILINE)
    assert re.search(r"^USER ens$", dockerfile, re.MULTILINE)


def valid_tag(tag):
    function = re.search(
        r"^valid_tag\(\) \{.*?^\}", BOX_SH.read_text(encoding="utf-8"), re.M | re.S
    )
    assert function
    script = f'{function.group(0)}\nvalid_tag "$1"'
    return subprocess.run(["sh", "-c", script, "sh", tag], capture_output=True).returncode == 0


@pytest.mark.parametrize("tag", ["v0.1.0", "v1.22.333", "v0.1.0-rc1", "v0.1.0-rc2"])
def test_valid_tag_acepta_versiones(tag):
    assert valid_tag(tag)


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "main",
        "0.1.0",
        "v0.1",
        "v0.1.0.0",
        "v0.1.0-beta",
        "v0.1.0;id",
        "v0.1.0 ",
        "v0.1.0\nmain",
        "$(id)",
        "-v0.1.0",
        "../v0.1.0",
    ],
)
def test_valid_tag_rechaza_lo_que_no_es_una_version(tag):
    assert not valid_tag(tag)


@pytest.fixture
def clone_with_docker_stub(tmp_path):
    """Un clon mínimo con box.sh y un `docker` de mentira: solo importa el chequeo de modo."""
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)
    shutil.copy(BOX_SH, root / "deploy" / "box.sh")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return root, env


def run_status(root, env):
    return subprocess.run(
        ["sh", str(root / "deploy" / "box.sh"), "status"], env=env, capture_output=True, text=True
    )


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666])
@pytest.mark.parametrize("name", [".env", "tunnel.env", "agent.env", "ui.env"])
def test_box_sh_se_niega_a_usar_un_env_con_modo_distinto_de_600(clone_with_docker_stub, name, mode):
    root, env = clone_with_docker_stub
    secrets = root / "deploy" / name
    secrets.write_text("X=1\n")
    secrets.chmod(mode)

    result = run_status(root, env)

    assert result.returncode != 0
    assert "chmod 600" in result.stderr


def test_box_sh_acepta_un_env_con_modo_600(clone_with_docker_stub):
    root, env = clone_with_docker_stub
    secrets = root / "deploy" / ".env"
    secrets.write_text("X=1\n")
    secrets.chmod(0o600)
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600

    result = run_status(root, env)

    assert "chmod 600" not in result.stderr
    assert "legible por otros" not in result.stderr


def test_box_sh_se_niega_a_usar_un_env_que_es_un_enlace(clone_with_docker_stub, tmp_path):
    root, env = clone_with_docker_stub
    target = tmp_path / "elsewhere.env"
    target.write_text("X=1\n")
    target.chmod(0o600)
    (root / "deploy" / ".env").symlink_to(target)

    result = run_status(root, env)

    assert result.returncode != 0
    assert "enlace simbólico" in result.stderr


def test_box_sh_se_niega_a_usar_un_env_que_es_un_enlace_colgante(clone_with_docker_stub, tmp_path):
    root, env = clone_with_docker_stub
    (root / "deploy" / ".env").symlink_to(tmp_path / "no-existe")

    result = run_status(root, env)

    assert result.returncode != 0
    assert "enlace simbólico" in result.stderr


def registry_stub(tmp_path, commit):
    """Un `docker` que contesta como el volumen de datos, con `current.commit` dado."""
    stub = tmp_path / "bin" / "docker"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  run) for last; do :; done\n"
        '    case "$last" in\n'
        "      current.tag) echo v1.0.0 ;;\n"
        f"      current.commit) printf '%s\\n' '{commit}' ;;\n"
        "      *) echo ;;\n"
        "    esac ;;\n"
        "esac\n"
        "exit 0\n"
    )
    stub.chmod(0o755)


@pytest.mark.parametrize(
    ("commit", "message"),
    [
        ("--output=/tmp/x", "no es un hash de git"),
        ("../../etc", "no es un hash de git"),
        ("ABCDEF" + "0" * 34, "no es un hash de git"),
        ("abc123", "no es un hash de git"),
        ("0" * 40, "no existe"),
    ],
)
def test_box_sh_no_pasa_a_git_un_commit_del_registro_que_no_es_un_hash(
    clone_with_docker_stub, tmp_path, commit, message
):
    root, env = clone_with_docker_stub
    registry_stub(tmp_path, commit)

    result = run_status(root, env)

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize("service", ["postgres", "app caddy"])
def test_box_sh_restart_rechaza_un_servicio_desconocido(clone_with_docker_stub, service):
    root, env = clone_with_docker_stub

    result = subprocess.run(
        ["sh", str(root / "deploy" / "box.sh"), "restart", *service.split()],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "uso:" in result.stderr


def test_box_sh_no_activa_la_traza_de_shell():
    text = BOX_SH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*set\s+-\w*x", text, re.MULTILINE)
    assert "set -x" not in text


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck no está instalado")
def test_box_sh_pasa_shellcheck():
    result = subprocess.run(["shellcheck", "-s", "sh", str(BOX_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


def test_box_sh_activa_el_perfil_ui_si_existe_ui_env_y_le_pasa_su_fichero():
    text = BOX_SH.read_text(encoding="utf-8")

    assert re.search(r'\[ ! -e "\$UI_ENV_FILE" \] \|\| profiles=.*ui', text)
    assert "ENS_UI_ENV_FILE=$UI_ENV_FILE" in text
    assert "ENS_ENV_FILE=$UI_ENV_FILE" not in text


@pytest.mark.parametrize(
    "value", ["https://ui.example.org", "ui.example.org:443", "a b", ".x", "-x"]
)
def test_box_sh_rechaza_un_nombre_de_host_que_no_es_un_host(clone_with_docker_stub, value):
    root, env = clone_with_docker_stub
    ui_env = root / "deploy" / "ui.env"
    ui_env.write_text(f"ENS_UI_HOST={value}\n")
    ui_env.chmod(0o600)

    result = run_status(root, env)

    assert result.returncode != 0
    assert "nombre de host" in result.stderr


def test_box_sh_acepta_un_nombre_de_host_valido(clone_with_docker_stub):
    root, env = clone_with_docker_stub
    ui_env = root / "deploy" / "ui.env"
    ui_env.write_text("ENS_UI_HOST=ui.example.org\n")
    ui_env.chmod(0o600)

    result = run_status(root, env)

    assert "nombre de host" not in result.stderr
