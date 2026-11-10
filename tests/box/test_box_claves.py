"""Kit de claves en deploy/box.sh: contrato que se comprueba sin Docker (docker es un stub)."""

import os
import pty
import re
import stat
import subprocess
from pathlib import Path

import pytest

from .test_gate import BOX_SH, REPO, override, run_validator, shell_function

TOKEN = "hvb.AAAAAQJtokenSECRETdeprueba"  # noqa: S105 - valor de prueba
PASSWORD = "contrasena_admin_de_prueba_0123456789"  # noqa: S105


def text_of(*names):
    return "\n".join(shell_function(name) for name in names)


def box_text():
    return BOX_SH.read_text(encoding="utf-8")


def write_stub(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / name
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)


def run_sh(script, env, tty=False, stdin_text=None):
    """Ejecuta `script` con sh; con tty=True stdin y stdout son un pseudo-terminal."""
    if not tty:
        return subprocess.run(
            ["sh", "-c", script], env=env, capture_output=True, text=True, input=stdin_text or ""
        )
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["sh", "-c", script], stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env, text=True
    )
    os.close(slave)
    if stdin_text:
        os.write(master, stdin_text.encode())
    chunks = []
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    os.close(master)
    _, stderr = proc.communicate()
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, b"".join(chunks).decode(errors="replace"), stderr
    )


# --- uso -----------------------------------------------------------------------------------


@pytest.fixture
def clone_with_docker_stub(tmp_path):
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "box.sh").write_text(box_text())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write_stub(tmp_path / "bin", "docker", "exit 0\n")
    return root, {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}


def run_box(root, env, *args):
    return subprocess.run(
        ["sh", str(root / "deploy" / "box.sh"), *args], env=env, capture_output=True, text=True
    )


def test_usage_lista_los_comandos_de_claves(clone_with_docker_stub):
    root, env = clone_with_docker_stub

    result = run_box(root, env)

    assert result.returncode != 0
    for command in (
        "kit enable claves <paquete>",
        "kit disable claves",
        "kit claves init",
        "kit claves unseal",
        "update [--no-backup] [--seal-ok]",
        "rollback [--seal-ok]",
    ):
        assert command in result.stderr


@pytest.mark.parametrize(
    "args",
    [["kit", "enable", "claves"], ["kit", "claves", "otra"], ["kit", "borra", "claves"]],
)
def test_kit_claves_rechaza_usos_incorrectos(clone_with_docker_stub, args):
    root, env = clone_with_docker_stub

    result = run_box(root, env, *args)

    assert result.returncode != 0
    assert "uso:" in result.stderr or "desconocida" in result.stderr


def test_la_herramienta_desconocida_nombra_las_dos():
    assert "(copias o claves)" in shell_function("cmd_kit")


# --- release y activación ------------------------------------------------------------------


def test_los_ficheros_de_openbao_van_en_la_release_si_el_commit_los_tiene():
    text = box_text()
    optional = re.search(r'^OPTIONAL_RELEASE_FILES="(.*)"$', text, re.MULTILINE).group(1).split()
    on_disk = sorted(
        str(p.relative_to(REPO)) for p in (REPO / "deploy" / "openbao").rglob("*") if p.is_file()
    )

    assert sorted(optional) == on_disk
    # Un commit anterior al kit no los trae: no pueden ser obligatorios para desplegarlo.
    assert "deploy/openbao" not in re.search(r'^RELEASE_FILES="(.*)"$', text, re.M).group(1)
    assert "cat-file -e" in shell_function("use_release")


def test_dc_activa_el_perfil_claves_solo_con_el_manifest_y_pasa_el_hcl_estable():
    dc = shell_function("dc")

    assert 'profiles="${profiles:+$profiles,}claves"' in dc
    assert "check_claves_config" in dc
    assert "ENS_CLAVES_CONFIG=$claves_config" in dc
    assert "ENS_CLAVES_HOST=$CLAVES_HOST" in dc
    assert re.search(r"claves_enabled\(\) \{\n.*CLAVES_MANIFEST.*grep -q .\^  openbao:", box_text())
    assert 'CLAVES_CONFIG="$CLAVES_DIR/openbao.hcl"' in box_text()


@pytest.mark.parametrize(
    "target", ["/source/claves", "/source/claves-auditoria", "/source/claves/x"]
)
def test_el_override_de_copias_reserva_source_claves(target):
    result = run_validator("validate_copias_override", override(f"/srv/datos:{target}:ro"))

    assert result.returncode != 0
    assert "claves" in result.stderr


def test_el_override_de_copias_sigue_aceptando_otros_montajes():
    assert (
        run_validator("validate_copias_override", override("/srv/d:/source/datos:ro")).returncode
        == 0
    )


def test_el_fragmento_del_conector_no_admite_credenciales_ni_otra_direccion():
    good = "asset_id: caja\naddress: http://openbao:8200\nmax_key_age_days: 365\n"
    assert run_validator("validate_claves_fragment", good).returncode == 0
    # render_bundle emite max_token_period_hours: el e2e lo vio rechazado por contener "token"
    assert (
        run_validator("validate_claves_fragment", good + "max_token_period_hours: 72\n").returncode
        == 0
    )
    for bad in (
        good.replace("openbao:8200", "evil.example:8200"),
        good + "token: x\n",
        "asset_id: caja\naddress: http://openbao:8200\n",
    ):
        assert run_validator("validate_claves_fragment", bad).returncode != 0


@pytest.mark.parametrize("doc", ['{"key_shares": 5, "key_threshold": 3}'])
def test_init_json_valido(doc):
    assert run_validator("validate_claves_init", doc).returncode == 0


@pytest.mark.parametrize(
    "doc",
    [
        '{"key_shares": 3, "key_threshold": 5}',
        '{"key_shares": "5", "key_threshold": 3}',
        '{"key_shares": 5, "key_threshold": 3, "root": 1}',
        "[]",
    ],
)
def test_init_json_invalido(doc):
    assert run_validator("validate_claves_init", doc).returncode != 0


def test_enable_instala_el_hcl_de_la_release_con_un_directorio_700_y_sin_pisar_volumenes():
    enable = shell_function("kit_claves_enable")

    assert 'cmp -s "$pkg/openbao.hcl" "$REL/deploy/openbao/config.hcl"' in enable
    assert "chmod 700" in enable
    assert "prepare_volume openbao-data" in enable and "prepare_volume openbao-audit" in enable
    assert re.search(r"volume (rm|prune)", enable) is None
    # Idempotente: sin cambio de HCL no se fuerza la recreación (quedaría sellado).
    assert "--force-recreate" in enable and "confirm_seal_loss 0" in enable


# --- secretos: nada en argv ni en pantalla -------------------------------------------------


def test_los_contenedores_de_claves_no_dejan_log_ni_privilegios():
    isolated = shell_function("claves_isolated")

    for flag in (
        "--log-driver none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges:true",
        '--network "${PROJECT}_claves-app"',
    ):
        assert flag in isolated
    assert "claves_isolated" in shell_function("kit_claves_init")
    assert "claves_isolated" in shell_function("kit_claves_unseal")
    assert "--log-driver none" in shell_function("openbao_seal_state")


def test_box_sh_no_usa_read_s_ni_bashismos_de_entrada_de_secretos():
    text = box_text()

    assert not re.search(r"\bread\s+-\w*s", text)
    assert "[[" not in text and "<<<" not in text


def test_solo_la_contrasena_de_admin_se_muestra_y_el_token_solo_va_por_stdin():
    code = text_of("kit_claves_init", "kit_claves_unseal", "kit_claves_enable", "claves_result")

    shown = [line for line in code.splitlines() if re.search(r"\b(info|echo|warn|die)\b", line)]
    assert [line.strip() for line in shown if "$admin_password" in line and "-z" not in line] == [
        'info "  Contraseña: $admin_password"'
    ]
    assert not [line for line in shown if "token_line" in line or "app_token" in line]
    uses = [
        line.strip() for line in code.splitlines() if "$token_line" in line and "-z" not in line
    ]
    assert uses == ["printf '%s\\n' \"$token_line\" | merge_copias_env /dev/stdin ||"]
    # El argv de docker solo lleva el nombre del campo (token|password), nunca su valor.
    assert "result.json" in code


# --- init con un pseudo-terminal y docker de mentira ---------------------------------------


INIT_PRELUDE = """
set -eu
PROJECT=t
ROOT=$1
ENV_FILE=$ROOT/deploy/.env
CLAVES_DIR=$ROOT/deploy/kit/claves
INIT_CONTAINER=
WORK_DIR=
current=v1.0.0
info() { printf '%s\\n' "$*"; }
warn() { printf 'AVISO: %s\\n' "$*" >&2; }
die() { printf 'ERROR: %s\\n' "$*" >&2; exit 1; }
require_tools() { :; }
acquire_lock() { :; }
check_secret_files() { :; }
require_volume() { :; }
use_current_release() { :; }
claves_enabled() { :; }
state_get() { echo sha; }
image_id() { echo sha; }
image_ref() { echo ens-evidence:v1.0.0; }
python_image() { echo python:3; }
recreate_service() { echo "RECREATE $1" >>"$ROOT/calls"; }
openbao_seal_state() { echo "${SEAL:-initialized=false sealed=true}"; }
"""

INIT_DOCKER = f"""
echo "$*" >>"$STUB_LOG"
case "$1" in
  exec)
    for last; do :; done
    case "$last" in
      token) printf '%s' 'ENS_KEYSTORE_TOKEN={TOKEN}' ;;
      password) printf '%s' '{PASSWORD}' ;;
    esac ;;
esac
exit 0
"""


@pytest.fixture
def box_dir(tmp_path):
    (tmp_path / "root" / "deploy" / "kit" / "claves").mkdir(parents=True)
    root = tmp_path / "root"
    (root / "deploy" / "kit" / "claves" / "init.json").write_text("{}")
    (root / "deploy" / ".env.example").write_text("ENS_OTRA=\n")
    write_stub(tmp_path / "bin", "docker", INIT_DOCKER)
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "STUB_LOG": str(tmp_path / "docker.log"),
    }
    (tmp_path / "docker.log").write_text("")
    return root, env, tmp_path / "docker.log"


def init_script(root, *functions):
    return INIT_PRELUDE + text_of(*functions) + "\nkit_claves_init\n", str(root)


def run_init(box_dir, *extra, tty=True):
    root, env, _ = box_dir
    names = (
        "cleanup",
        "ensure_env_file",
        "merge_copias_env",
        "claves_isolated",
        "app_image_checked",
    )
    script, _ = init_script(root, *names, "claves_result", "kit_claves_init")
    return run_sh(f"set -- {root}; " + script, env, tty=tty)


def test_init_guarda_el_token_en_env_600_sin_mostrarlo_y_muestra_la_contrasena_una_vez(box_dir):
    root, _, log = box_dir

    result = run_init(box_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    env_file = root / "deploy" / ".env"
    assert f"ENS_KEYSTORE_TOKEN={TOKEN}" in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert TOKEN not in result.stdout + result.stderr
    assert result.stdout.count(PASSWORD) == 1
    assert TOKEN not in log.read_text() and PASSWORD not in log.read_text()
    assert "RECREATE app" in (root / "calls").read_text()


def test_init_sustituye_el_token_que_ya_hubiera_y_conserva_lo_demas(box_dir):
    root, _, _ = box_dir
    env_file = root / "deploy" / ".env"
    env_file.write_text("ENS_OTRA=1\nENS_KEYSTORE_TOKEN=viejo\n")
    env_file.chmod(0o600)

    result = run_init(box_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    lines = env_file.read_text().splitlines()
    assert lines == ["ENS_OTRA=1", f"ENS_KEYSTORE_TOKEN={TOKEN}"]


def test_init_usa_exec_con_tty_y_un_tmpfs_sin_log_y_destruye_el_contenedor(box_dir):
    _, _, log = box_dir

    run_init(box_dir)

    calls = log.read_text().splitlines()
    run_call = next(c for c in calls if c.startswith("run "))
    assert "--log-driver none" in run_call and "--tmpfs /out:" in run_call
    assert "--network t_claves-app" in run_call
    assert any(
        c.startswith("exec -it t-claves-init sh -c") and "init --init-file" in c for c in calls
    )
    assert calls[-1].startswith("rm -f t-claves-init")


def test_init_sin_json_valido_remite_al_runbook_y_no_toca_env(box_dir):
    root, env, _ = box_dir
    write_stub(
        Path(env["PATH"].split(":")[0]),
        "docker",
        'case "$1" in exec) for l; do :; done; [ "$l" = password ] && exit 1;; esac\nexit 0\n',
    )

    result = run_init(box_dir)

    assert result.returncode != 0
    assert "kit-claves.md" in result.stderr
    assert not (root / "deploy" / ".env").exists()


def test_init_exige_terminal(box_dir):
    result = run_init(box_dir, tty=False)

    assert result.returncode != 0
    assert "terminal interactivo" in result.stderr


def test_init_no_se_repite_si_ya_esta_inicializado(box_dir):
    root, env, _ = box_dir
    env = {**env, "SEAL": "initialized=true sealed=false"}

    script, _ = init_script(
        root, "cleanup", "claves_isolated", "app_image_checked", "kit_claves_init"
    )
    result = run_sh(f"set -- {root}; " + script, env, tty=True)

    assert result.returncode != 0
    assert "ya está inicializado" in result.stderr


# --- disable -------------------------------------------------------------------------------


def test_disable_para_el_contenedor_y_no_borra_volumenes_ni_el_directorio(box_dir):
    root, env, log = box_dir
    claves = root / "deploy" / "kit" / "claves"
    (claves / "MANIFEST").write_text("version: 1\n")
    (claves / "openbao.hcl").write_text("ui = true\n")
    write_stub(
        Path(env["PATH"].split(":")[0]),
        "docker",
        'echo "$*" >>"$STUB_LOG"\n[ "$1 $2" = "ps -aq" ] && echo abc123\nexit 0\n',
    )
    script = INIT_PRELUDE + text_of("kit_claves_disable") + "\nkit_claves_disable\n"
    script = script.replace(
        "CLAVES_DIR=$ROOT/deploy/kit/claves",
        "CLAVES_DIR=$ROOT/deploy/kit/claves\nCLAVES_MANIFEST=$CLAVES_DIR/MANIFEST\nCLAVES_HOST=claves.example.org",
    )

    result = run_sh(f"set -- {root}; " + script, env)

    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "stop abc123" in calls and "rm abc123" in calls
    assert "volume" not in calls
    assert (claves / "openbao.hcl").exists() and (claves / "MANIFEST.disabled").exists()
    assert not (claves / "MANIFEST").exists()


def test_disable_no_contiene_borrados_de_datos():
    disable = shell_function("kit_claves_disable")

    assert not re.search(r"volume (rm|prune|create)|rm -r|rm -f|rmdir|prune", disable)


# --- update (K12) --------------------------------------------------------------------------


def test_update_acepta_seal_ok_y_pregunta_antes_de_construir_o_tocar_nada():
    update = shell_function("cmd_update")

    assert "--seal-ok) seal_ok=1" in update
    assert update.index("openbao_would_recreate") < update.index("build_image")
    assert update.index("openbao_would_recreate") < update.index("dc up")
    assert 'confirm_seal_loss "$seal_ok"' in update


def test_rollback_acepta_seal_ok_y_pregunta_antes_de_levantar_nada():
    rollback = shell_function("cmd_rollback")

    assert "--seal-ok) seal_ok=1" in rollback
    assert rollback.index("openbao_would_recreate") < rollback.index("dc up")
    assert rollback.index("use_release") < rollback.index("openbao_would_recreate")
    assert 'confirm_seal_loss "$seal_ok"' in rollback


def test_restart_no_puede_recrear_openbao():
    # `restart openbao` no existe y recrea con --no-deps: ningún restart toca OpenBao (K12)
    restart = shell_function("cmd_restart")

    assert "openbao" not in restart


def run_confirm(flag, stdin_text="", tty=False):
    script = (
        "die() { printf 'ERROR: %s\\n' \"$*\" >&2; exit 1; }\n"
        "warn() { printf 'AVISO: %s\\n' \"$*\" >&2; }\n"
        + shell_function("confirm_seal_loss")
        + f"confirm_seal_loss {flag}; echo CONTINUA\n"
    )
    return run_sh(script, os.environ, tty=tty, stdin_text=stdin_text)


def test_update_sin_terminal_ni_flag_se_para():
    result = run_confirm(0)

    assert result.returncode != 0
    assert "CONTINUA" not in result.stdout
    assert "--seal-ok" in result.stderr and "SELLADO" in result.stderr


def test_update_con_seal_ok_continua_sin_terminal():
    result = run_confirm(1)

    assert result.returncode == 0 and "CONTINUA" in result.stdout


def test_update_con_terminal_exige_la_palabra_sellar():
    refused = run_confirm(0, "NO\n", tty=True)
    accepted = run_confirm(0, "SELLAR\n", tty=True)

    assert refused.returncode != 0 and "CONTINUA" not in refused.stdout
    assert accepted.returncode == 0 and "CONTINUA" in accepted.stdout


@pytest.mark.parametrize(
    ("running_hash", "new_hash", "recreates"),
    [("abc", "abc", False), ("abc", "def", True), ("abc", "", True)],
)
def test_openbao_would_recreate_compara_el_hash_de_compose(
    tmp_path, running_hash, new_hash, recreates
):
    write_stub(tmp_path / "bin", "docker", f"echo {running_hash}\n")
    script = (
        "claves_enabled() { :; }\n"
        'dc() { case "$1" in ps) echo cid ;; config) '
        + (f"echo 'openbao {new_hash}'" if new_hash else "true")
        + " ;; esac; }\n"
        + shell_function("openbao_would_recreate")
        + "openbao_would_recreate v2.0.0\n"
    )
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}

    result = run_sh(script, env)

    assert (result.returncode == 0) is recreates


def test_openbao_would_recreate_no_pregunta_si_openbao_no_corre(tmp_path):
    write_stub(tmp_path / "bin", "docker", "exit 0\n")
    script = (
        "claves_enabled() { :; }\ndc() { :; }\n"
        + shell_function("openbao_would_recreate")
        + "openbao_would_recreate v2.0.0\n"
    )
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}

    assert run_sh(script, env).returncode == 1


# --- status --------------------------------------------------------------------------------


def test_status_muestra_el_sellado_sin_token():
    assert "openbao_seal_state" in shell_function("cmd_status")
    seal = shell_function("openbao_seal_state")
    assert "seal-status" in seal
    assert "X-Vault-Token" not in seal and "ENS_KEYSTORE_TOKEN" not in seal
