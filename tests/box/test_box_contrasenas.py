"""Kit de contraseñas en deploy/box.sh, compose.yaml y el Caddyfile: contrato sin Docker real."""

import os
import re
import stat
import subprocess

import pytest
import yaml

from .test_box_claves import box_text, run_box, run_sh, text_of, write_stub
from .test_gate import REPO

COMPOSE = yaml.safe_load((REPO / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
TOKEN = "T0kenDeAltaDePrueba0123456789abcd"  # noqa: S105 - valor de prueba
PHC = "$argon2id$v=19$m=19456,t=2,p=1$c2FsdA$aGFzaA"
EXPORT_UID = "10001"


# --- compose.yaml ---------------------------------------------------------------------------


def test_vaultwarden_va_fijado_por_digest_y_sin_privilegios_ni_puertos():
    vw = SERVICES["vaultwarden"]

    assert vw["image"] == (
        "vaultwarden/server:1.37.5-alpine@sha256:"
        "bbe684a02d6b0db54256ff88eac9754f4519284a7b26314afc241fd7d0918191"
    )
    assert vw["profiles"] == ["contrasenas"]
    assert vw["cap_drop"] == ["ALL"] and vw["security_opt"] == ["no-new-privileges:true"]
    assert vw["read_only"] is True
    assert "ports" not in vw and "env_file" not in vw
    assert vw["networks"] == ["contrasenas-net"]
    assert COMPOSE["networks"]["contrasenas-net"] == {"internal": True}


def test_vaultwarden_cierra_el_registro_y_no_lleva_token_en_compose():
    env = SERVICES["vaultwarden"]["environment"]

    assert env["SIGNUPS_ALLOWED"] == "false" and env["INVITATIONS_ALLOWED"] == "true"
    assert env["DISABLE_ICON_DOWNLOAD"] == "true"
    assert env["DOMAIN"] == "https://${ENS_CONTRASENAS_HOST:-contrasenas.invalid}"
    assert not [key for key in env if "TOKEN" in key]


def test_solo_caddy_y_vaultwarden_estan_en_contrasenas_net():
    members = {n for n, s in SERVICES.items() if "contrasenas-net" in s.get("networks", [])}

    assert members == {"caddy", "vaultwarden"}


def test_el_exportador_esta_aislado_y_no_comparte_nada_con_la_app():
    export = SERVICES["contrasenas-export"]

    assert export["image"] == SERVICES["app"]["image"]
    assert export["command"][:5] == ["ens-evidence", "kit", "contrasenas", "export", "--data"]
    assert export["command"][5:] == ["/source", "--out", "/export", "--snapshot", "/snapshot"] + [
        "--every",
        "15m",
    ]
    assert export["network_mode"] == "none" and "networks" not in export
    assert export["read_only"] is True and export["cap_drop"] == ["ALL"]
    assert export["security_opt"] == ["no-new-privileges:true"]
    assert export["user"] == f"{EXPORT_UID}:{EXPORT_UID}"
    assert "/tmp" in export["tmpfs"]
    assert "env_file" not in export and "environment" not in export and "ports" not in export
    assert sorted(export["volumes"]) == [
        "contrasenas-export:/export",
        "contrasenas-snapshot:/snapshot",
        "vaultwarden-data:/source:ro",
    ]
    assert export["profiles"] == ["contrasenas"]


def test_la_app_solo_ve_el_export_y_no_la_red_ni_los_datos_de_vaultwarden():
    app = SERVICES["app"]

    assert "contrasenas-export:/run/kit/contrasenas:ro" in app["volumes"]
    assert not [v for v in app["volumes"] if v.startswith("vaultwarden-data")]
    assert "contrasenas-net" not in app["networks"]


def test_backrest_lee_la_instantanea_con_el_uid_que_la_escribe():
    backrest = SERVICES["backrest"]

    assert "contrasenas-snapshot:/source/contrasenas:ro" in backrest["volumes"]
    assert not [v for v in backrest["volumes"] if v.startswith("vaultwarden-data")]
    assert (
        backrest["user"] == f"{EXPORT_UID}:{EXPORT_UID}" == SERVICES["contrasenas-export"]["user"]
    )
    assert "contrasenas-net" not in backrest["networks"]


def test_los_volumenes_de_contrasenas_llevan_el_prefijo_del_proyecto():
    for name in ("vaultwarden-data", "contrasenas-export", "contrasenas-snapshot"):
        assert COMPOSE["volumes"][name] == {"name": f"${{ENS_BOX_PROJECT:-ens-box}}_{name}"}


# --- Caddyfile ------------------------------------------------------------------------------


def test_caddy_enruta_contrasenas_por_host_antes_del_403_final_y_sin_cookies_de_cloudflare():
    text = (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    assert "@contrasenas host {$ENS_CONTRASENAS_HOST:contrasenas.invalid}" in text
    block = text[text.index("handle @contrasenas {") :]
    block = block[: block.index("\n\t}\n") + 3]
    assert "reverse_proxy vaultwarden:80" in block
    assert "header_up -Cf-Access-*" in block and "header_up -X-Forwarded-*" in block
    # solo se quitan las cookies CF_*; VW_ADMIN y las del cliente web pasan
    assert "header_up -Cookie" not in block and "CF_" in block
    assert text.index("handle @contrasenas {") < text.index("\n\thandle {\n")
    assert 'respond "Forbidden" 403' in text[text.index("\n\thandle {\n") :]


# --- uso y gitignore ------------------------------------------------------------------------


@pytest.fixture
def clone_with_docker_stub(tmp_path):
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "box.sh").write_text(box_text())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write_stub(tmp_path / "bin", "docker", "exit 0\n")
    return root, {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}


def test_usage_lista_los_comandos_de_contrasenas(clone_with_docker_stub):
    root, env = clone_with_docker_stub

    result = run_box(root, env)

    for command in (
        "kit enable contrasenas <paquete>",
        "kit disable contrasenas",
        "kit contrasenas alta",
        "kit contrasenas cerrar-alta",
    ):
        assert command in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["kit", "enable", "contrasenas"],
        ["kit", "contrasenas", "otra"],
        ["kit", "contrasenas", "alta", "sobra"],
        ["kit", "borra", "contrasenas"],
    ],
)
def test_kit_contrasenas_rechaza_usos_incorrectos(clone_with_docker_stub, args):
    root, env = clone_with_docker_stub

    result = run_box(root, env, *args)

    assert result.returncode != 0
    assert "uso:" in result.stderr or "desconocida" in result.stderr


@pytest.mark.parametrize("name", ["admin.env", "alta.yaml", "MANIFEST"])
def test_el_directorio_del_kit_esta_en_gitignore(name):
    path = f"deploy/kit/contrasenas/{name}"

    ignored = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)

    assert ignored.returncode == 0, f"{path} no está en .gitignore"


def test_enable_instala_con_un_directorio_700_y_verifica_el_manifest_antes():
    enable = text_of("kit_contrasenas_enable", "verify_contrasenas_package")

    assert enable.index("verify_contrasenas_package") < enable.index("mkdir -p")
    assert enable.index("validate_contrasenas_fragment") < enable.index("mkdir -p")
    assert 'chmod 700 "$ROOT/deploy/kit" "$CONTRASENAS_DIR"' in enable
    assert not re.search(r"volume (rm|prune)", enable)


def test_la_release_activa_el_perfil_solo_con_el_manifest_y_pasa_el_host():
    dc = text_of("dc")

    assert 'profiles="${profiles:+$profiles,}contrasenas"' in dc
    assert "ENS_CONTRASENAS_HOST=$CONTRASENAS_HOST" in dc
    assert re.search(
        r"contrasenas_enabled\(\) \{\n.*CONTRASENAS_MANIFEST.*grep -q .\^  vaultwarden:",
        box_text(),
    )


@pytest.mark.parametrize("target", ["/source/contrasenas", "/source/contrasenas/x"])
def test_el_override_de_copias_reserva_source_contrasenas(target):
    from .test_gate import override, run_validator

    result = run_validator("validate_copias_override", override(f"/srv/datos:{target}:ro"))

    assert result.returncode != 0 and "contrasenas" in result.stderr


def test_el_fragmento_del_conector_no_admite_credenciales_ni_otro_export():
    from .test_gate import run_validator

    path = "/run/kit/contrasenas/usuarios.json"
    good = f"asset_id: vaultwarden\nexport_path: {path}\ninactive_days: 90\n"
    assert run_validator("validate_contrasenas_fragment", good).returncode == 0
    for bad in (
        good + "token: x\n",
        good.replace("/run/kit/contrasenas/usuarios.json", "/data/otro.json"),
        "inactive_days: 90\n",
    ):
        assert run_validator("validate_contrasenas_fragment", bad).returncode != 0


# --- alta y cerrar-alta con docker de mentira -----------------------------------------------


PRELUDE = """
set -eu
PROJECT=t
ROOT=$1
CONTRASENAS_DIR=$ROOT/deploy/kit/contrasenas
CONTRASENAS_MANIFEST=$CONTRASENAS_DIR/MANIFEST
CONTRASENAS_ADMIN_ENV=$CONTRASENAS_DIR/admin.env
CONTRASENAS_ALTA=$CONTRASENAS_DIR/alta.yaml
CONTRASENAS_HOST=contrasenas.example.org
current=v1.0.0
info() { printf '%s\\n' "$*"; }
warn() { printf 'AVISO: %s\\n' "$*" >&2; }
die() { printf 'ERROR: %s\\n' "$*" >&2; exit 1; }
usage() { die uso; }
file_owner() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1"; }
file_mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
require_tools() { :; }
acquire_lock() { :; }
check_secret_files() { :; }
require_volume() { :; }
use_current_release() { :; }
contrasenas_enabled() { :; }
app_image_checked() { echo ens-evidence:v1.0.0; }
contrasenas_admin_hash() { cat >"$ROOT/hash-stdin"; printf '%s\\n' "$PHC"; }
dc() { echo "dc $*" >>"$ROOT/calls"; [ -z "${DC_FAIL:-}" ] || return 1; }
wait_healthy() { [ -z "${UNHEALTHY:-}" ]; }
vaultwarden_admin_state() { cat "$ROOT/admin-state"; }
vaultwarden_has_admin_token() { [ -n "${HAS_TOKEN:-}" ]; }
"""


@pytest.fixture
def kit(tmp_path):
    root = tmp_path / "root"
    (root / "deploy" / "kit" / "contrasenas").mkdir(parents=True)
    (root / "admin-state").write_text("enabled")
    return root


def run_kit(root, function, **env):
    script = PRELUDE + text_of(
        "alta_override_text", "check_alta_override", "check_private", function
    )
    return run_sh(
        f"set -- {root}; " + script + f"\n{function}\n",
        {**os.environ, "PHC": PHC, **env},
    )


def kit_files(root):
    directory = root / "deploy" / "kit" / "contrasenas"
    return {p.name for p in directory.iterdir()}


def test_alta_guarda_solo_el_hash_con_modo_600_y_muestra_el_token_una_vez(kit):
    result = run_kit(kit, "kit_contrasenas_alta")

    assert result.returncode == 0, result.stdout + result.stderr
    directory = kit / "deploy" / "kit" / "contrasenas"
    assert kit_files(kit) == {"admin.env", "alta.yaml"}
    assert stat.S_IMODE((directory / "admin.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((directory / "alta.yaml").stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / "admin.env").read_text() == f"ADMIN_TOKEN='{PHC}'\n"
    shown = re.findall(r"^  (\S{32})$", result.stdout, re.MULTILINE)
    assert len(shown) == 1
    assert shown[0] not in (directory / "admin.env").read_text()
    assert shown[0] not in result.stderr and shown[0] not in (kit / "calls").read_text()
    # el token entra en el hash por stdin, nunca por argv
    assert (kit / "hash-stdin").read_text().strip() == shown[0]
    assert "up -d --no-build --no-deps --force-recreate vaultwarden" in (kit / "calls").read_text()


def test_el_override_del_alta_solo_añade_el_env_file_de_vaultwarden(kit):
    run_kit(kit, "kit_contrasenas_alta")

    doc = yaml.safe_load((kit / "deploy" / "kit" / "contrasenas" / "alta.yaml").read_text())

    assert doc == {
        "services": {
            "vaultwarden": {"env_file": [str(kit / "deploy" / "kit" / "contrasenas" / "admin.env")]}
        }
    }


def test_un_alta_abierta_no_se_pisa_ni_se_repite(kit):
    run_kit(kit, "kit_contrasenas_alta")
    directory = kit / "deploy" / "kit" / "contrasenas"
    before = (directory / "admin.env").read_text()
    (kit / "calls").unlink()

    again = run_kit(kit, "kit_contrasenas_alta", PHC="$argon2id$otro")

    assert again.returncode != 0 and "alta abierta" in again.stderr
    assert (directory / "admin.env").read_text() == before
    assert not (kit / "calls").exists()


def test_alta_interrumpida_no_muestra_token_y_se_recupera_con_cerrar_alta(kit):
    failed = run_kit(kit, "kit_contrasenas_alta", DC_FAIL="1")

    assert failed.returncode != 0 and "cerrar-alta" in failed.stderr
    assert not re.search(r"^  \S{32}$", failed.stdout, re.MULTILINE)

    closing = run_kit(kit, "kit_contrasenas_cerrar_alta")

    assert closing.returncode != 0  # el panel sigue "enabled" en el stub: no se da por cerrada
    (kit / "admin-state").write_text("disabled")
    closed = run_kit(kit, "kit_contrasenas_cerrar_alta")
    assert closed.returncode == 0, closed.stderr
    assert kit_files(kit) == set()
    (kit / "admin-state").write_text("enabled")
    retry = run_kit(kit, "kit_contrasenas_alta")
    assert retry.returncode == 0, retry.stderr


def test_alta_no_muestra_token_si_el_panel_no_queda_activo(kit):
    (kit / "admin-state").write_text("disabled")

    result = run_kit(kit, "kit_contrasenas_alta")

    assert result.returncode != 0 and "no quedó activo" in result.stderr
    assert not re.search(r"^  \S{32}$", result.stdout, re.MULTILINE)


def test_alta_no_muestra_token_si_vaultwarden_no_esta_sano(kit):
    result = run_kit(kit, "kit_contrasenas_alta", UNHEALTHY="1")

    assert result.returncode != 0 and "no está sano" in result.stderr
    assert not re.search(r"^  \S{32}$", result.stdout, re.MULTILINE)


def test_alta_rechaza_hash_que_no_es_argon2id(kit):
    result = run_kit(kit, "kit_contrasenas_alta", PHC="texto-plano")

    assert result.returncode != 0 and "Argon2id" in result.stderr
    assert kit_files(kit) == set()


def test_alta_no_sigue_enlaces_simbolicos(kit, tmp_path):
    directory = kit / "deploy" / "kit" / "contrasenas"
    (tmp_path / "otro").write_text("x")
    (directory / "admin.env").symlink_to(tmp_path / "otro")

    result = run_kit(kit, "kit_contrasenas_alta")

    assert result.returncode != 0 and "simbólicos" in result.stderr
    assert (tmp_path / "otro").read_text() == "x"


def test_alta_no_escribe_a_traves_de_un_new_preexistente(kit, tmp_path):
    directory = kit / "deploy" / "kit" / "contrasenas"
    (tmp_path / "victima").write_text("x")
    (directory / "admin.env.new").symlink_to(tmp_path / "victima")

    result = run_kit(kit, "kit_contrasenas_alta")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "victima").read_text() == "x"


@pytest.mark.parametrize("tamper", ["texto", "enlace", "modo", "sin_admin_env"], ids=lambda t: t)
def test_dc_rechaza_un_override_del_alta_que_no_escribio_alta(kit, tmp_path, tamper):
    run_kit(kit, "kit_contrasenas_alta")
    directory = kit / "deploy" / "kit" / "contrasenas"
    if tamper == "texto":
        (directory / "alta.yaml").write_text("services:\n  app:\n    privileged: true\n")
    elif tamper == "enlace":
        (tmp_path / "x.yaml").write_text((directory / "alta.yaml").read_text())
        (directory / "alta.yaml").unlink()
        (directory / "alta.yaml").symlink_to(tmp_path / "x.yaml")
    elif tamper == "modo":
        (directory / "admin.env").chmod(0o644)
    else:
        (directory / "admin.env").unlink()

    result = run_kit(kit, "check_alta_override")

    assert result.returncode != 0
    assert "ERROR" in result.stderr


def test_cerrar_alta_borra_los_ficheros_y_comprueba_el_entorno_y_el_cuerpo(kit):
    run_kit(kit, "kit_contrasenas_alta")
    (kit / "admin-state").write_text("disabled")

    result = run_kit(kit, "kit_contrasenas_cerrar_alta")

    assert result.returncode == 0, result.stderr
    assert "Alta cerrada" in result.stdout
    assert kit_files(kit) == set()


@pytest.mark.parametrize(
    "env, state, message",
    [
        ({"DC_FAIL": "1"}, "disabled", "NO se ha cerrado el alta: no se pudo recrear"),
        ({"UNHEALTHY": "1"}, "disabled", "NO se ha confirmado el cierre"),
        ({"HAS_TOKEN": "1"}, "disabled", "sigue con ADMIN_TOKEN"),
        ({}, "enabled", "no responde «panel desactivado»"),
    ],
    ids=["recrear", "sano", "token", "cuerpo"],
)
def test_cerrar_alta_dice_cuando_no_ha_podido_cerrar(kit, env, state, message):
    (kit / "admin-state").write_text(state)

    result = run_kit(kit, "kit_contrasenas_cerrar_alta", **env)

    assert result.returncode != 0
    assert message in result.stderr
    assert "Alta cerrada" not in result.stdout


def test_cerrar_alta_borra_antes_de_tocar_docker_para_que_un_override_roto_no_la_bloquee(kit):
    directory = kit / "deploy" / "kit" / "contrasenas"
    (directory / "alta.yaml").write_text("corrupto")
    (kit / "admin-state").write_text("disabled")

    result = run_kit(kit, "kit_contrasenas_cerrar_alta")

    assert result.returncode == 0, result.stderr
    assert not (directory / "alta.yaml").exists()


def test_el_estado_del_panel_se_decide_por_el_cuerpo_y_el_token_va_por_stdin():
    state = text_of("vaultwarden_admin_state")
    hashing = text_of("contrasenas_admin_hash")

    assert "The admin panel is disabled" in state
    assert "--network none" in hashing and "-i" in hashing
    assert "-e " not in hashing and "-v " not in hashing and "$token" not in hashing


def test_disable_para_los_contenedores_cierra_el_alta_y_no_borra_volumenes(kit):
    directory = kit / "deploy" / "kit" / "contrasenas"
    (directory / "MANIFEST").write_text("version: 1\n")
    run_kit(kit, "kit_contrasenas_alta")
    (kit / "calls").unlink()
    bin_dir = kit.parent / "bin"
    write_stub(
        bin_dir,
        "docker",
        'echo "$*" >>"$STUB_LOG"\n[ "$1 $2" = "ps -aq" ] && echo abc123\nexit 0\n',
    )

    result = run_kit(
        kit,
        "kit_contrasenas_disable",
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        STUB_LOG=str(kit / "docker.log"),
    )

    assert result.returncode == 0, result.stderr
    log = (kit / "docker.log").read_text()
    assert "stop abc123" in log and "rm abc123" in log and "volume" not in log
    assert kit_files(kit) == {"MANIFEST.disabled"}


def test_disable_no_contiene_borrados_de_volumenes():
    assert not re.search(
        r"volume (rm|prune|create)|rm -r|rmdir|prune", text_of("kit_contrasenas_disable")
    )


def test_box_sh_no_muestra_el_hash_ni_usa_read_s():
    text = box_text()

    assert not re.search(r"\bread\s+-\w*s", text)
    alta = text_of("kit_contrasenas_alta")
    shown = [line for line in alta.splitlines() if re.match(r"\s*(info|echo|warn|die)\b", line)]
    assert not [line for line in shown if "$phc" in line]
    assert [line.strip() for line in shown if "$token" in line] == ['info "  $token"']
