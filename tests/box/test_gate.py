"""Contrato de la caja que se comprueba sin Docker: compose, Caddyfile, Dockerfile y box.sh."""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from ens_evidence.kit.registry import KIT_TOOLS

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

    assert {"caddy", "cloudflared", "backrest", "openbao", "copias-proxy"} <= set(external)
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


CLAVES_CONFIG_MOUNT = "${ENS_CLAVES_CONFIG:-/dev/null}:/openbao/config/config.hcl:ro"


def test_los_unicos_montajes_del_host_son_ficheros_de_config_en_solo_lectura(services):
    host_mounts = {
        n: [str(v) for v in s.get("volumes", []) if str(v).startswith((".", "/", "~", "$"))]
        for n, s in services.items()
    }

    assert {n: m for n, m in host_mounts.items() if m} == {
        "caddy": ["./deploy/Caddyfile:/etc/caddy/Caddyfile:ro"],
        "openbao": [CLAVES_CONFIG_MOUNT],
        "copias-proxy": [
            "./deploy/copias-proxy.conf:/etc/squid/proxy.conf:ro",
            "${ENS_COPIAS_DESTINO_CONF:-/dev/null}:/etc/squid/destino.conf:ro",  # lo fija box.sh
        ],
    }


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

    assert set(networks) == {"edge", "agent", "ui-net", "copias-net", "claves-net"}
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


def test_backrest_solo_arranca_con_el_perfil_copias(services):
    assert services["backrest"]["profiles"] == ["copias"]


def test_backrest_usa_la_variante_scratch_fijada_por_digest_real(services):
    image = services["backrest"]["image"]

    assert image.startswith("ghcr.io/garethgeorge/backrest:v1.14.1-scratch@sha256:")
    digest = image.rpartition(":")[2]
    assert DIGEST.search("@sha256:" + digest) and len(set(digest)) > 4, "digest de relleno"


def test_backrest_corre_endurecido_y_con_uid_explicito(services):
    backrest = services["backrest"]

    assert backrest["user"] == "10001:10001"
    assert backrest["read_only"] is True
    assert "ports" not in backrest and "privileged" not in backrest
    assert backrest["restart"] == "unless-stopped"
    assert backrest["environment"]["BACKREST_RESTIC_COMMAND"] == "/bin/restic"
    assert all(t.split(":")[0] in ("/tmp", "/cache") for t in backrest["tmpfs"])


def test_backrest_ve_los_datos_de_la_caja_solo_en_lectura_y_guarda_sus_secretos_aparte(
    compose, services
):
    volumes = services["backrest"]["volumes"]

    assert volumes == [
        "backrest-config:/config",
        "backrest-data:/data",
        "data:/source/caja:ro",
        "openbao-data:/source/claves:ro",
        "openbao-audit:/source/claves-auditoria:ro",
    ]
    owners = [n for n, s in services.items() if "backrest-config:/config" in s.get("volumes", [])]
    assert owners == ["backrest"]
    assert {"backrest-config", "backrest-data"} <= set(compose["volumes"])


def test_backrest_no_recibe_ficheros_de_entorno_ni_la_red_interna_de_los_datos(compose, services):
    backrest = services["backrest"]

    assert "env_file" not in backrest
    assert backrest["networks"] == ["copias-net", "copias-salida"]
    assert compose["networks"]["copias-net"]["internal"] is True


def test_backrest_solo_esta_en_redes_internas_y_sale_por_el_proxy(compose, services):
    backrest = services["backrest"]

    assert all(compose["networks"][n].get("internal") is True for n in backrest["networks"])
    env = backrest["environment"]
    for name in ("HTTPS_PROXY", "https_proxy"):
        assert env[name] == "http://copias-proxy:3128"
    assert "copias-proxy" in backrest["depends_on"]
    assert "copias-salida" in services["copias-proxy"]["networks"]


def test_copias_proxy_es_la_unica_salida_del_kit_y_corre_endurecido(compose, services):
    proxy = services["copias-proxy"]

    assert proxy["profiles"] == ["copias"]
    assert proxy["networks"] == ["copias-salida", "copias"]
    assert not compose["networks"]["copias"].get("internal")
    assert proxy["read_only"] is True and proxy["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in proxy["security_opt"]
    assert "ports" not in proxy and "privileged" not in proxy and "user" not in proxy
    assert proxy["restart"] == "unless-stopped"
    assert all(t.startswith("/") and ":" not in t for t in proxy["tmpfs"])
    assert "env_file" not in proxy and "environment" not in proxy


def test_copias_proxy_usa_squid_fijado_por_digest_real_sin_latest(services):
    image = services["copias-proxy"]["image"]

    assert re.fullmatch(r"ubuntu/squid:7\.2-26\.04_edge@sha256:[0-9a-f]{64}", image)
    digest = image.rpartition(":")[2]
    assert len(set(digest)) > 4, "digest de relleno"
    assert not any("latest" in (s["image"]) for s in services.values() if "image" in s)


PROXY_CONF = (REPO / "deploy" / "copias-proxy.conf").read_text()


def test_la_config_del_proxy_solo_admite_connect_al_destino_y_deniega_lo_demas():
    rules = [row.split() for row in PROXY_CONF.splitlines() if row.startswith("http_access")]

    assert rules == [
        ["http_access", "allow", "CONNECT", "destino_host", "destino_puerto"],
        ["http_access", "deny", "all"],
    ]
    assert "include /etc/squid/destino.conf" in PROXY_CONF
    assert "cache deny all" in PROXY_CONF


def test_la_config_del_proxy_no_lleva_hosts_ni_secretos():
    code = "\n".join(row for row in PROXY_CONF.splitlines() if not row.startswith("#"))
    assert not re.search(r"dstdomain|password|secret|AWS_", code)
    mounts = yaml.safe_load((REPO / "compose.yaml").read_text())["services"]
    assert (
        "./deploy/copias-proxy.conf:/etc/squid/proxy.conf:ro" in mounts["copias-proxy"]["volumes"]
    )


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


def test_solo_app_cloudflared_y_copias_proxy_tienen_salida_a_internet(compose, services):
    outbound = {
        name
        for name, service in services.items()
        if any(not compose["networks"][n].get("internal") for n in service["networks"])
    }

    assert outbound == {"app", "cloudflared", "copias-proxy"}


# --- kit de claves: servicio `openbao` (spec kit-secrets, plan C3, C4, K11) ------------------


def test_openbao_solo_arranca_con_el_perfil_claves(services):
    assert services["openbao"]["profiles"] == ["claves"]


def test_openbao_usa_la_imagen_distroless_fijada_por_digest_real(services):
    image = services["openbao"]["image"]

    assert image.startswith("ghcr.io/openbao/openbao-distroless:2.7.2@sha256:")
    digest = image.rpartition(":")[2]
    assert DIGEST.search("@sha256:" + digest) and len(set(digest)) > 4, "digest de relleno"


def test_openbao_corre_endurecido_con_uid_explicito_y_sin_puertos(services):
    openbao = services["openbao"]

    assert openbao["user"] == "10001:10001"
    assert openbao["read_only"] is True
    assert openbao["tmpfs"] == ["/tmp"]
    assert openbao["cap_drop"] == ["ALL"] and "cap_add" not in openbao
    assert "no-new-privileges:true" in openbao["security_opt"]
    assert "ports" not in openbao and "privileged" not in openbao
    assert openbao["restart"] == "unless-stopped"
    assert openbao["command"] == ["server", "-config=/openbao/config/config.hcl"]


def test_openbao_no_tiene_healthcheck_porque_sellado_es_un_estado_normal(services):
    assert "healthcheck" not in services["openbao"]


def test_openbao_no_recibe_ficheros_de_entorno(services):
    assert "env_file" not in services["openbao"]


def test_openbao_solo_esta_en_redes_internas(compose, services):
    networks = services["openbao"]["networks"]

    assert networks == ["claves-net", "claves-app"]
    assert all(compose["networks"][n].get("internal") is True for n in networks)
    assert {n for n, s in services.items() if "claves-net" in s["networks"]} == {"caddy", "openbao"}
    assert {n for n, s in services.items() if "claves-app" in s["networks"]} == {"app", "openbao"}


def test_openbao_monta_su_hcl_y_sus_dos_volumenes(compose, services):
    assert services["openbao"]["volumes"] == [
        CLAVES_CONFIG_MOUNT,
        "openbao-data:/openbao/data",
        "openbao-audit:/openbao/audit",
    ]
    assert {"openbao-data", "openbao-audit"} <= set(compose["volumes"])


@pytest.mark.parametrize("volume", ["openbao-data", "openbao-audit"])
def test_nadie_monta_los_volumenes_de_openbao_salvo_backrest_en_solo_lectura(services, volume):
    users = {
        n: [str(v) for v in s.get("volumes", []) if str(v).startswith(f"{volume}:")]
        for n, s in services.items()
    }

    assert {n: m for n, m in users.items() if m}.keys() == {"openbao", "backrest"}
    assert all(m.endswith(":ro") for m in users["backrest"])


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
    assert caddyfile.count("reverse_proxy") == 5


def test_el_limite_de_la_subida_en_caddy_cubre_el_de_la_app(caddyfile):
    from ens_evidence.ui.forms import MAX_UPLOAD_BYTES, UPLOAD_ENVELOPE_BYTES

    matcher = block(caddyfile, "@ui_upload")
    assert "method POST" in matcher
    assert re.search(r"^\s*path /evidencias/nueva$", matcher, re.M)
    upload = block(caddyfile, "handle @ui_upload")
    assert "reverse_proxy ui:8000" in upload
    limit = re.search(r"request_body \{\s*max_size (\d+)KiB", upload)
    assert limit
    assert int(limit.group(1)) * 1024 == MAX_UPLOAD_BYTES + UPLOAD_ENVELOPE_BYTES
    assert caddyfile.index("handle @ui_upload") < caddyfile.index("handle @ui {")


def test_caddy_no_publica_healthz_en_ningun_host(caddyfile):
    healthz = block(caddyfile, "@ui_healthz")
    assert "host {$ENS_UI_HOST:ui.invalid}" in healthz
    assert "path /healthz" in healthz
    assert 'respond "Forbidden" 403' in block(caddyfile, "handle @ui_healthz")
    assert caddyfile.index("handle @ui_healthz") < caddyfile.index("handle @ui {")
    assert not any(
        "healthz" in b
        for b in (
            block(caddyfile, "@agent_any"),
            block(caddyfile, "@small_agent"),
            block(caddyfile, "@large_agent"),
        )
    )


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


# Servicio de compose de cada herramienta del registro: el registro no lo guarda.
KIT_COMPOSE_SERVICE = {"copias": "backrest", "claves": "openbao"}


def test_cada_herramienta_del_kit_tiene_su_servicio_de_compose():
    assert set(KIT_COMPOSE_SERVICE) == {tool.id for tool in KIT_TOOLS}


@pytest.mark.parametrize("tool", KIT_TOOLS, ids=lambda t: t.id)
def test_toda_herramienta_del_kit_entra_solo_por_su_host_tras_access(tool, caddyfile, services):
    env = f"ENS_{tool.id.upper()}_HOST"
    assert re.search(rf"@{tool.id} host \{{\${env}:[^}}\s]+\}}", caddyfile), tool.id
    handle = caddyfile.index(f"\thandle @{tool.id} {{")
    deny_all = caddyfile.index("\thandle {\n")
    assert handle < deny_all, "el handle de la herramienta debe ir antes del deny-all final"
    assert "ports" not in services[KIT_COMPOSE_SERVICE[tool.id]]
    example = (REPO / "deploy" / "ui.env.example").read_text(encoding="utf-8")
    assert re.search(rf"^{env}=", example, re.M), tool.id


def test_caddy_enruta_backrest_por_nombre_de_host_y_le_deja_su_authorization(caddyfile):
    copias = block(caddyfile, "handle @copias")

    assert "reverse_proxy backrest:9898" in copias
    assert "header_up -Cookie" in copias and "header_up -Cf-Access-*" in copias
    assert "header_up -X-Forwarded-*" in copias
    assert "header_up -Authorization" not in copias, (
        "Backrest autentica con Authorization (v1.14.1)"
    )
    assert re.search(r"request_body \{\s*max_size 1MB", copias)


def test_caddy_enruta_openbao_por_nombre_de_host_sin_cookies_ni_access(caddyfile):
    # La UI de OpenBao v2.7.2 se autentica con X-Vault-Token (ui/app/services/auth.js:177, K3),
    # no con cookies: se quitan las de Access igual que en @copias.
    claves = block(caddyfile, "handle @claves")

    assert "reverse_proxy openbao:8200" in claves
    assert "header_up -Cookie" in claves and "header_up -Cf-Access-*" in claves
    assert "header_up -X-Forwarded-*" in claves
    assert re.search(r"request_body \{\s*max_size 1MB", claves)


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


def test_lo_que_instala_el_kit_no_se_sube_a_git():
    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO).returncode == 0

    assert ignored("deploy/kit/copias/compose.yaml")
    assert ignored("deploy/kit/copias/MANIFEST")
    assert not ignored("deploy/kit/.gitignore")


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


# --- kit de copias: `box.sh kit enable|disable copias <paquete>` -------------------------------


def shell_function(name):
    text = BOX_SH.read_text(encoding="utf-8")
    found = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert found, name
    return found.group(0)


def embedded_python(function):
    """El programa Python que `function` pasa a `in_app python -I -c '...'`."""
    found = re.search(r"-c '\n(.*?)\n'( \"\$1\")?\n", shell_function(function), re.DOTALL)
    assert found, function
    return found.group(1)


def run_validator(function, stdin, *args):
    return subprocess.run(
        [sys.executable, "-I", "-c", embedded_python(function), *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def override(*volumes, extra=""):
    lines = "".join(f"      - {v}\n" for v in volumes)
    return f"services:\n  backrest:\n    volumes:\n{lines}{extra}"


@pytest.mark.parametrize(
    "volume", ["/srv/documentos:/source/documentos:ro", "/home/pyme/datos:/source/datos/pyme:ro"]
)
def test_el_override_acepta_montajes_del_host_en_solo_lectura_bajo_source(volume):
    assert run_validator("validate_copias_override", override(volume)).returncode == 0


@pytest.mark.parametrize(
    "volume",
    [
        "/srv/documentos:/source/documentos",
        "/srv/documentos:/source/documentos:rw",
        "/srv/documentos:/otro:ro",
        "/:/source/raiz:ro",
        "/var/run/docker.sock:/source/sock:ro",
        "/var/run:/source/run:ro",
        "/proc:/source/proc:ro",
        "/srv/../etc:/source/etc:ro",
        "relativa:/source/x:ro",
        "/srv/x:/source/caja:ro",
        "/srv/x:/source/caja/sub:ro",
    ],
)
def test_el_override_rechaza_montajes_fuera_de_lo_permitido(volume):
    assert run_validator("validate_copias_override", override(volume)).returncode != 0


@pytest.mark.parametrize(
    "extra",
    [
        "    privileged: true\n",
        "    cap_add: [SYS_ADMIN]\n",
        "    user: root\n",
        "    environment: {X: y}\n",
        "  app:\n    image: x\n",
    ],
)
def test_el_override_solo_puede_tocar_los_volumenes_de_backrest(extra):
    result = run_validator("validate_copias_override", override("/srv/x:/source/x:ro", extra=extra))

    assert result.returncode != 0


def test_el_override_no_puede_declarar_volumenes_ni_redes_de_nivel_superior():
    text = override("/srv/x:/source/x:ro") + "volumes:\n  v: {}\n"

    assert run_validator("validate_copias_override", text).returncode != 0


def test_el_fragmento_de_backups_acepta_repositorios_sin_secretos():
    text = "repositories:\n  - id: caja_ovh\n    asset_id: a1\n    repository: s3:https://x/b\n"

    assert run_validator("validate_copias_fragment", text).returncode == 0


@pytest.mark.parametrize(
    "text",
    [
        "repositories: []\n",
        "repositories:\n  - {id: 'A b'}\n",
        "repositories:\n  - {id: x, restic_password: p}\n",
        "repositories:\n  - {id: x, aws_access_key_id: k}\n",
        "repositories:\n  - {id: x}\nconnector: {}\n",
    ],
)
def test_el_fragmento_de_backups_rechaza_credenciales_y_formas_ajenas(text):
    assert run_validator("validate_copias_fragment", text).returncode != 0


@pytest.mark.parametrize("identifier", ["1-x", "con-guion", "Caja", "a", "x" * 32])
def test_el_fragmento_de_backups_rechaza_ids_que_el_conector_rechaza(identifier):
    text = f"repositories:\n  - {{id: {identifier}}}\n"

    assert run_validator("validate_copias_fragment", text).returncode != 0


BCRYPT_B64 = base64.b64encode(b"$2a$14$" + b"a" * 53).decode()


def destination(*uris):
    config = json.dumps({"repos": [{"id": f"r{i}", "uri": u} for i, u in enumerate(uris)]})
    return run_validator("copias_destination", config)


def test_el_destino_del_proxy_sale_del_uri_del_repositorio_con_puerto_443_por_defecto():
    result = destination("s3:https://S3.eu-west-1.Amazonaws.com/bucket/caja")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "acl destino_host dstdomain s3.eu-west-1.amazonaws.com",
        "acl destino_puerto port 443",
    ]


def test_el_destino_del_proxy_admite_puerto_explicito_y_repos_del_mismo_destino():
    result = destination("s3:https://minio.local:9000/a", "s3:https://minio.local:9000/b/c")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1] == "acl destino_puerto port 9000"


@pytest.mark.parametrize(
    "uris",
    [
        (),
        ("s3:http://minio.local/b",),
        ("rest:https://x.example.org/b",),
        ("s3:https://a.example.org/b", "s3:https://b.example.org/b"),
        ("s3:https://a.example.org:9000/b", "s3:https://a.example.org/b"),
        ("s3:https://a.example.org:99999/b",),
        ("s3:https://a.example.org\nhttp_access allow all/b",),
        ("s3:https://[::1]/b",),
        ("s3:https://u:p@a.example.org/b",),
    ],
)
def test_el_destino_del_proxy_rechaza_lo_que_no_es_un_unico_s3_https(uris):
    assert destination(*uris).returncode != 0


def test_kit_enable_instala_el_destino_del_proxy_y_disable_lo_borra():
    script = (REPO / "deploy" / "box.sh").read_text()

    assert "ENS_COPIAS_DESTINO_CONF=$COPIAS_DESTINO" in script
    assert "recreate copias-proxy backrest caddy" in script.replace("--force-", "")
    assert 'rm -f "$COPIAS_OVERRIDE" "$COPIAS_DIR/MANIFEST" "$COPIAS_DESTINO"' in script


def test_el_usuario_inicial_de_backrest_se_inyecta_con_el_bcrypt_y_auth_activada():
    config = json.dumps({"auth": {"disabled": True}, "repos": []})

    result = run_validator("add_backrest_user", config, BCRYPT_B64)

    assert result.returncode == 0, result.stderr
    auth = json.loads(result.stdout)["auth"]
    assert auth == {
        "disabled": False,
        "users": [{"name": "admin", "passwordBcrypt": BCRYPT_B64}],
    }


@pytest.mark.parametrize("value", ["", "texto-plano", "$2a$14$" + "a" * 53, BCRYPT_B64[:-1]])
def test_el_usuario_inicial_de_backrest_rechaza_un_hash_que_no_es_bcrypt_en_base64(value):
    result = run_validator("add_backrest_user", "{}", value)

    assert result.returncode != 0
    assert result.stdout == ""


def test_la_contrasena_de_backrest_solo_va_a_stdin_del_hash_y_a_la_terminal():
    kit = BOX_SH.read_text(encoding="utf-8").partition("# --- kit de herramientas")[2]
    uses = [
        line.strip()
        for line in kit.splitlines()
        if "backrest_password" in line.replace("backrest_password_hash", "")
    ]

    assert len(uses) == 6, uses
    assert uses[0].startswith("# La contraseña solo vive en `backrest_password`")
    assert uses[1].startswith("backrest_password=$(head -c 48 /dev/urandom")
    assert uses[2].startswith('[ "${#backrest_password}" -eq 32 ]')
    assert uses[3].startswith(
        "password_hash=$(printf '%s\\n' \"$backrest_password\" | backrest_password_hash)"
    )
    assert all(u.startswith('info "  Contraseña: $backrest_password"') for u in uses[4:])
    assert "--plaintext" not in kit


def test_el_usuario_inicial_conserva_el_resto_de_la_configuracion_de_backrest():
    config = json.dumps({"repos": [{"id": "bk-caja"}], "plans": [{"id": "p"}]})

    result = run_validator("add_backrest_user", config, BCRYPT_B64)

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert (out["repos"], out["plans"]) == ([{"id": "bk-caja"}], [{"id": "p"}])


def test_si_docker_no_calcula_el_bcrypt_se_aborta_con_un_mensaje_claro(tmp_path):
    (tmp_path / "compose.yaml").write_text("services:\n  caddy:\n    image: caddy:2\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\ncase "$*" in *hash-password*) exit 1 ;; esac\nexit 0\n')
    docker.chmod(0o755)
    script = "set -eu\nREL=$1\n" + "".join(
        shell_function(n) for n in ("die", "backrest_password_hash")
    )
    script += shell_function("new_backrest_credentials") + "new_backrest_credentials\necho llegó\n"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = subprocess.run(
        ["sh", "-c", script, "sh", str(tmp_path)], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "No se pudo calcular el bcrypt" in result.stderr
    assert "llegó" not in result.stdout


def test_el_uso_de_box_sh_lista_el_restablecimiento_de_la_contrasena(clone_with_docker_stub):
    root, env = clone_with_docker_stub

    result = subprocess.run(
        ["sh", str(root / "deploy" / "box.sh"), "kit"], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "kit copias reset-password" in result.stderr


def test_restablecer_la_contrasena_sin_las_copias_activadas_falla_con_un_error_claro(tmp_path):
    stubs = "".join(
        f"{name}() {{ :; }}\n"
        for name in (
            "require_tools",
            "acquire_lock",
            "check_secret_files",
            "require_volume",
            "use_current_release",
        )
    )
    script = (
        f"set -eu\nCOPIAS_OVERRIDE={tmp_path / 'no-existe.yaml'}\n"
        + stubs
        + "in_backrest_config() { echo llegó; }\n"
        + shell_function("die")
        + shell_function("kit_copias_reset_password")
        + "kit_copias_reset_password\n"
    )

    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)

    assert result.returncode != 0
    assert "Las copias no están activadas" in result.stderr
    assert "llegó" not in result.stdout


FILES = ("config.json", "compose.yaml", "app.env", "backups.yaml")


def build_package(tmp_path, *, tamper=None, extra=None, manifest=None, drop=None):
    """Un paquete del contrato C2; `tamper` altera un fichero después de firmar el MANIFEST."""
    contents = {name: f"contenido de {name}\n".encode() for name in FILES}
    lines = ["version: 1"] + [
        f"{hashlib.sha256(contents[name]).hexdigest()}  copias/{name}" for name in FILES
    ]
    if tamper:
        contents[tamper] += b"x"
    members = {**contents, "MANIFEST": ("\n".join(manifest or lines) + "\n").encode()}
    members.update(extra or {})
    for name in drop or ():
        members.pop(name)
    path = tmp_path / "paquete.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(f"copias/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def verify_package(tmp_path, package):
    script = "\n".join(
        [
            shell_function("die"),
            shell_function("sha256_of"),
            shell_function("extract_copias_package"),
            shell_function("verify_copias_package"),
            f'extract_copias_package "{package}" "{tmp_path}/out" && '
            f'verify_copias_package "{tmp_path}/out/copias"',
        ]
    )
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True)


def test_un_paquete_integro_se_acepta(tmp_path):
    result = verify_package(tmp_path, build_package(tmp_path))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", FILES)
def test_un_fichero_alterado_hace_fallar_el_sha256_del_manifest(tmp_path, name):
    result = verify_package(tmp_path, build_package(tmp_path, tamper=name))

    assert result.returncode != 0
    assert "sha256" in result.stderr


def test_un_paquete_con_un_fichero_de_mas_se_rechaza_sin_extraer_nada(tmp_path):
    result = verify_package(tmp_path, build_package(tmp_path, extra={"../evil": b"x"}))

    assert result.returncode != 0
    assert not (tmp_path / "out").exists()


def test_un_paquete_sin_un_fichero_se_rechaza(tmp_path):
    result = verify_package(tmp_path, build_package(tmp_path, drop=["app.env"]))

    assert result.returncode != 0


def test_un_manifest_con_otra_version_o_sin_una_linea_se_rechaza(tmp_path):
    good = build_package(tmp_path).parent
    lines = ["version: 2"] + [f"{'0' * 64}  copias/{n}" for n in FILES]
    assert verify_package(tmp_path, build_package(tmp_path, manifest=lines)).returncode != 0
    assert good.exists()


def test_un_miembro_que_no_es_fichero_ni_directorio_se_rechaza_antes_de_extraer(tmp_path):
    package = tmp_path / "fifo.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        for name in (*FILES, "MANIFEST"):
            info = tarfile.TarInfo(f"copias/{name}")
            if name == "app.env":
                info.type = tarfile.FIFOTYPE
            else:
                info.size = 1
            archive.addfile(info, None if info.type == tarfile.FIFOTYPE else io.BytesIO(b"x"))

    result = verify_package(tmp_path, package)

    assert result.returncode != 0
    assert "tipos de fichero" in result.stderr
    assert not (tmp_path / "out").exists()


def test_un_enlace_simbolico_en_el_paquete_se_rechaza(tmp_path):
    package = tmp_path / "link.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        for name in (*FILES, "MANIFEST"):
            info = tarfile.TarInfo(f"copias/{name}")
            if name == "config.json":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            else:
                info.size = 1
            archive.addfile(info, None if info.type == tarfile.SYMTYPE else io.BytesIO(b"x"))

    result = verify_package(tmp_path, package)

    assert result.returncode != 0


def test_box_sh_no_escribe_secretos_del_paquete_en_pantalla_ni_en_argumentos():
    kit = BOX_SH.read_text(encoding="utf-8").partition("# --- kit de herramientas")[2]

    assert not re.search(r'(?<!<)"\$pkg/config\.json"', kit)
    assert '<"$pkg/config.json"' in kit, "config.json llega al volumen por stdin"
    assert "docker compose" not in kit.replace("docker compose -p $PROJECT logs", "")


def test_box_sh_activa_el_perfil_copias_solo_con_el_override_validado_y_una_release_que_lo_define():
    text = BOX_SH.read_text(encoding="utf-8")

    assert "ENS_COPIAS_HOST=$COPIAS_HOST" in text
    assert re.search(r"copias_enabled\(\) \{\n.*COPIAS_OVERRIDE.*grep -q .\^  backrest:", text)
    assert 'profiles="${profiles:+$profiles,}copias"' in text
    assert "check_copias_override" in shell_function("dc")


@pytest.mark.parametrize(
    "args",
    [["kit"], ["kit", "enable"], ["kit", "enable", "otra", "p.tgz"], ["kit", "borra", "copias"]],
)
def test_box_sh_kit_rechaza_usos_incorrectos(clone_with_docker_stub, args):
    root, env = clone_with_docker_stub

    result = subprocess.run(
        ["sh", str(root / "deploy" / "box.sh"), *args], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "uso:" in result.stderr or "desconocida" in result.stderr
