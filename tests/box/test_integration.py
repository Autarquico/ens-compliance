"""La caja con Docker de verdad: `pytest -m docker`. Fuera del gate por defecto.

Todo vive bajo un proyecto Compose con prefijo único (`ENS_BOX_PROJECT`) y etiquetas de imagen
propias, en un clon temporal con sus propias etiquetas git. Al terminar solo se elimina lo que
lleva ese prefijo o esas etiquetas; nunca se toca la caja real (`ens-box`).
"""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UID = "10001"
pytestmark = pytest.mark.docker


def docker_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


if not docker_available():
    pytest.skip("Docker no está disponible", allow_module_level=True)


def run(args, *, cwd=None, env=None, timeout=1200, check=False):
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=check
    )


@dataclass
class Box:
    clone: Path
    project: str
    tags: dict[str, str]

    @property
    def volume(self):
        return f"{self.project}_data"

    def env(self, **extra):
        return {**os.environ, "ENS_BOX_PROJECT": self.project, **extra}

    def sh(self, *args, **extra):
        return run(["sh", "deploy/box.sh", *args], cwd=self.clone, env=self.env(**extra))

    def in_volume(self, image_tag, script, *, env=()):
        """Un contenedor de un solo uso con el volumen de datos y el UID de la app."""
        flags = [f for pair in env for f in ("-e", pair)]
        return run(
            ["docker", "run", "--rm", "--network", "none", "--user", UID, *flags]
            + ["-v", f"{self.volume}:/data", "--entrypoint", "sh"]
            + [f"ens-evidence:{image_tag}", "-c", script]
        )

    def report_files(self, image_tag):
        """sha256 de cada fichero de informes y marcadores del workspace."""
        found = self.in_volume(
            image_tag,
            "cd /data/workspace/evidence/reports && find . -type f | sort | xargs sha256sum",
        )
        assert found.returncode == 0, found.stderr
        return dict(line.split("  ", 1)[::-1] for line in found.stdout.splitlines())

    def registry(self, image_tag):
        out = self.in_volume(image_tag, "cat /data/state/box-deploy.json")
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    def container(self, service):
        ids = run(
            ["docker", "ps", "-q"]
            + ["--filter", f"label=com.docker.compose.project={self.project}"]
            + ["--filter", f"label=com.docker.compose.service={service}"]
        )
        return ids.stdout.split()[0]

    def inspect(self, service, template):
        return run(
            ["docker", "inspect", "--format", template, self.container(service)]
        ).stdout.strip()

    def exec(self, service, *command):
        return run(["docker", "exec", self.container(service), *command])


def commit_all(clone, message, tag):
    git = ["git", "-c", "user.name=t5", "-c", "user.email=t5@example.invalid"]
    run([*git, "add", "-A"], cwd=clone, check=True)
    run([*git, "commit", "-q", "-m", message], cwd=clone, check=True)
    run([*git, "tag", tag], cwd=clone, check=True)


ORIGINAL_CMD = 'CMD ["ens-evidence", "scheduler", "run"]'
# Una versión que escribe un informe y un marcador y arranca sin scheduler: el proceso vive,
# nadie late.
NO_SCHEDULER_CMD = 'CMD ["sh", "-c", "ens-evidence report daily --cycle; exec sleep 3600"]'
CRASH_CMD = 'CMD ["sh", "-c", "exit 1"]'


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    suffix = secrets.token_hex(3)
    project = f"ensbxt{suffix}"
    serial = int(suffix, 16) % 900000 + 100000
    tags = {
        name: f"v9{serial}.0.{index}"
        for index, name in enumerate(["good", "no_scheduler", "crash", "labeled", "next"])
    }
    clone = tmp_path_factory.mktemp("box-clone")

    tracked = run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=REPO, check=True
    ).stdout.split("\0")
    for name in filter(None, tracked):
        if (REPO / name).is_file():
            (clone / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, clone / name)
    run(["git", "init", "-q"], cwd=clone, check=True)
    commit_all(clone, "good", tags["good"])

    dockerfile = clone / "Dockerfile"
    original = dockerfile.read_text(encoding="utf-8")
    assert ORIGINAL_CMD in original
    for name, cmd in (("no_scheduler", NO_SCHEDULER_CMD), ("crash", CRASH_CMD)):
        dockerfile.write_text(original.replace(ORIGINAL_CMD, cmd), encoding="utf-8")
        commit_all(clone, name, tags[name])
    dockerfile.write_text(original, encoding="utf-8")

    # Una etiqueta cuyo compose difiere del de la que se deja en el árbol de trabajo.
    compose = clone / "compose.yaml"
    text = compose.read_text(encoding="utf-8")
    assert "    init: true\n" in text
    compose.write_text(
        text.replace(
            "    init: true\n", "    init: true\n    labels:\n      ens.test.source: tag\n"
        ),
        encoding="utf-8",
    )
    commit_all(clone, "labeled", tags["labeled"])
    (clone / "NEXT").write_text("otra versión\n", encoding="utf-8")
    commit_all(clone, "next", tags["next"])
    run(["git", "checkout", "-q", tags["good"]], cwd=clone, check=True)

    handle = Box(clone, project, tags)
    yield handle

    # Solo lo que lleva el prefijo del proyecto y las etiquetas de prueba.
    owned = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
    ).stdout.split()
    if owned:
        run(["docker", "rm", "-f", *owned])
    for network in ("backend", "edge", "tunnel"):
        run(["docker", "network", "rm", f"{project}_{network}"])
    run(["docker", "volume", "rm", "-f", f"{project}_data"])
    run(["docker", "rmi", "-f", *[f"ens-evidence:{tag}" for tag in tags.values()]])


@pytest.fixture(scope="module")
def installed(box):
    assert run(["docker", "volume", "inspect", box.volume]).returncode != 0
    return box.sh("install", box.tags["good"])


def test_arranque_limpio_con_volumen_vacio_instala_y_deja_el_primer_ciclo_hecho(box, installed):
    good = box.tags["good"]

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert "primer ciclo" in installed.stdout
    assert "falló" not in installed.stderr  # 0 y 3 son válidos; 2 aborta el install
    assert box.report_files(good), "el primer ciclo debe dejar informes en el workspace"
    assert box.registry(good)["current"]["tag"] == good
    assert box.inspect("app", "{{.Config.Image}}") == f"ens-evidence:{good}"


def test_install_avisa_de_todo_lo_que_falta_para_la_copia_y_de_los_siguientes_pasos(box, installed):
    assert "backup.restic_repository en /data/settings.yaml" in installed.stderr
    assert "ENS_BACKUP_RESTIC_PASSWORD en deploy/.env" in installed.stderr
    assert "Siguientes pasos" in installed.stdout
    assert "alcance" in installed.stdout


def test_install_repetido_se_niega_antes_de_reconstruir_la_imagen(box, installed):
    good = box.tags["good"]
    before = run(["docker", "image", "inspect", "--format", "{{.Id}}", f"ens-evidence:{good}"])

    again = box.sh("install", good)

    assert again.returncode != 0
    assert "usa update" in again.stderr
    assert "Construyendo" not in again.stdout
    after = run(["docker", "image", "inspect", "--format", "{{.Id}}", f"ens-evidence:{good}"])
    assert after.stdout == before.stdout


def test_el_volumen_pertenece_al_uid_de_la_app_no_a_root(box, installed):
    owner = box.in_volume(box.tags["good"], "stat -c %u /data /data/workspace /data/settings.yaml")

    assert owner.stdout.split() == [UID, UID, UID]


def test_ningun_contenedor_publica_puertos(box, installed):
    ids = run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={box.project}"])
    assert ids.stdout.split()

    for container in ids.stdout.split():
        out = run(["docker", "inspect", "--format", "{{json .HostConfig.PortBindings}}", container])
        assert out.stdout.strip() in ("{}", "null")


def test_toda_ruta_da_403_desde_la_red_interna_y_el_log_no_guarda_query_ni_cabeceras(
    box, installed
):
    caddy_image = box.inspect("caddy", "{{.Config.Image}}")

    for path in ("/", "/admin", "/agent/enroll?token=abc-query"):
        result = run(
            ["docker", "run", "--rm", "--network", f"{box.project}_edge", caddy_image]
            + ["wget", "-S", "-O", "/dev/null", "--header", "Authorization: abc-header"]
            + [f"http://caddy:8080{path}"]
        )
        assert "403" in result.stderr, (path, result.stderr)

    logs = run(["docker", "logs", box.container("caddy")])
    log = logs.stdout + logs.stderr
    assert '"status":403' in log
    assert "abc-query" not in log and "abc-header" not in log


def test_caddy_corre_sin_root(box, installed):
    assert box.inspect("caddy", "{{.Config.User}}") == "65532:65532"
    assert box.exec("caddy", "id", "-u").stdout.strip() == "65532"


def test_el_latido_del_scheduler_es_del_contenedor_no_del_volumen(box, installed):
    assert box.exec("app", "test", "-f", "/tmp/scheduler-heartbeat").returncode == 0
    assert box.exec("app", "test", "-e", "/data/state/scheduler-heartbeat").returncode != 0


def test_update_a_una_version_que_arranca_sin_scheduler_no_se_da_por_sana(box, installed):
    good, bad = box.tags["good"], box.tags["no_scheduler"]
    before = box.report_files(good)
    # Un latido fresco dejado en el volumen por una versión anterior no debe valer.
    planted = box.in_volume(good, "mkdir -p /data/state && touch /data/state/scheduler-heartbeat")
    assert planted.returncode == 0, planted.stderr

    update = box.sh("update", "--no-backup", bad, ENS_BOX_HEALTH_TIMEOUT="60")

    assert update.returncode != 0
    assert "Esperando a que app esté sano" in update.stdout
    assert "rollback" in update.stderr
    assert "Actualizado" not in update.stdout
    registry = box.registry(good)
    assert registry["current"]["tag"] == good and registry["previous"] is None
    written = box.report_files(good)
    assert before.items() <= written.items(), "la nueva versión no puede alterar lo anterior"
    assert set(written) - set(before), "la versión sin scheduler debe haber escrito un informe"


def test_status_dice_en_palabras_que_hay_una_actualizacion_sin_completar(box, installed):
    status = box.sh("status")

    assert status.returncode == 0, status.stdout + status.stderr
    assert f"Actualización a {box.tags['no_scheduler']} sin completar" in status.stderr
    assert f"la versión registrada es {box.tags['good']}" in status.stderr
    assert "deploy/box.sh rollback" in status.stderr


def test_rollback_tras_una_version_que_falla_conserva_informes_y_marcadores(box, installed):
    good = box.tags["good"]
    written = box.report_files(good)

    rollback = box.sh("rollback", ENS_BOX_HEALTH_TIMEOUT="120")

    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert box.inspect("app", "{{.Config.Image}}") == f"ens-evidence:{good}"
    assert box.report_files(good) == written
    registry = box.registry(good)
    assert registry["current"]["tag"] == good
    assert registry["failed"]["tag"] == box.tags["no_scheduler"]
    for command in (["scheduler", "status"], ["evidence", "verify", "--all"]):
        read = box.exec("app", "ens-evidence", *command)
        assert read.returncode == 0, (command, read.stdout, read.stderr)


def test_una_version_que_se_reinicia_en_bucle_falla_sin_agotar_la_espera(box, installed):
    crash = box.tags["crash"]
    started = time.monotonic()

    update = box.sh("update", "--no-backup", crash, ENS_BOX_HEALTH_TIMEOUT="300")

    assert update.returncode != 0
    assert time.monotonic() - started < 240
    assert "rollback" in update.stderr
    rollback = box.sh("rollback", ENS_BOX_HEALTH_TIMEOUT="120")
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr


def test_update_a_una_etiqueta_con_otro_compose_despliega_el_de_la_etiqueta(box, installed):
    good, labeled = box.tags["good"], box.tags["labeled"]
    tree = (box.clone / "compose.yaml").read_text(encoding="utf-8")
    assert "ens.test.source" not in tree, "el árbol de trabajo está en la etiqueta sin la marca"

    update = box.sh("update", "--no-backup", labeled, ENS_BOX_HEALTH_TIMEOUT="120")

    assert update.returncode == 0, update.stdout + update.stderr
    assert box.inspect("app", '{{index .Config.Labels "ens.test.source"}}') == "tag"
    tagged = run(["git", "show", f"{labeled}:compose.yaml"], cwd=box.clone).stdout
    registry = box.registry(labeled)
    assert registry["current"]["compose_sha256"] == hashlib.sha256(tagged.encode()).hexdigest()
    assert registry["previous"]["tag"] == good
    assert (box.clone / "compose.yaml").read_text(encoding="utf-8") == tree


def test_rollback_vuelve_a_la_release_registrada_no_al_arbol_de_trabajo(box, installed):
    rollback = box.sh("rollback", ENS_BOX_HEALTH_TIMEOUT="120")

    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert box.inspect("app", '{{index .Config.Labels "ens.test.source"}}') == ""
    assert box.inspect("app", "{{.Config.Image}}") == f"ens-evidence:{box.tags['good']}"
    update = box.sh("update", "--no-backup", box.tags["labeled"], ENS_BOX_HEALTH_TIMEOUT="120")
    assert update.returncode == 0, update.stdout + update.stderr


CONFIG_FILES = '{{index .Config.Labels "com.docker.compose.project.config_files"}}'


def release_compose(box, tag):
    """El compose.yaml guardado de la release registrada para `tag`."""
    commit = box.registry(tag)["current"]["commit"]
    return box.clone / ".git" / "ens-box" / "releases" / commit / "compose.yaml"


def config_files(box, service):
    return Path(box.inspect(service, CONFIG_FILES)).resolve()


def test_restart_recrea_app_con_el_compose_de_la_release_no_con_el_del_clon(box, installed):
    labeled = box.tags["labeled"]
    before = box.container("app")

    restart = box.sh("restart", "app", ENS_BOX_HEALTH_TIMEOUT="120")

    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert box.container("app") != before, "restart debe recrear el contenedor"
    assert config_files(box, "app") == release_compose(box, labeled).resolve()
    assert box.inspect("app", '{{index .Config.Labels "ens.test.source"}}') == "tag"
    assert "no se creó con" not in box.sh("status").stderr


def test_restart_cloudflared_se_niega_si_no_hay_tunel_configurado(box, installed):
    tunnel = box.sh("restart", "cloudflared")

    assert tunnel.returncode != 0
    assert "tunnel.env" in tunnel.stderr


def test_status_avisa_si_un_contenedor_no_se_creo_con_el_compose_de_la_release(box, installed):
    labeled = box.tags["labeled"]
    clone_compose = (box.clone / "compose.yaml").resolve()
    assert clone_compose != release_compose(box, labeled).resolve()
    recreated = run(
        ["docker", "compose", "-p", box.project, "-f", str(clone_compose)]
        + ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "app"],
        cwd=box.clone,
        env=box.env(ENS_VERSION=labeled, ENS_ENV_FILE=str(box.clone / "deploy" / ".env")),
    )
    assert recreated.returncode == 0, recreated.stdout + recreated.stderr
    assert config_files(box, "app") == clone_compose

    status = box.sh("status")

    assert status.returncode == 0, status.stdout + status.stderr
    assert "no se creó con" in status.stderr
    assert f"{box.project}-app" in status.stderr
    assert "deploy/box.sh restart" in status.stderr
    assert box.sh("restart", "app", ENS_BOX_HEALTH_TIMEOUT="120").returncode == 0
    assert config_files(box, "app") == release_compose(box, labeled).resolve()
    assert "no se creó con" not in box.sh("status").stderr


def test_update_se_niega_si_el_script_no_es_el_de_la_etiqueta(box, installed):
    script = box.clone / "deploy" / "box.sh"
    original = script.read_text(encoding="utf-8")
    script.write_text(original + "\n# editado\n", encoding="utf-8")
    try:
        update = box.sh("update", "--no-backup", box.tags["next"])
    finally:
        script.write_text(original, encoding="utf-8")

    assert update.returncode != 0
    assert f"git checkout {box.tags['next']}" in update.stderr
    assert box.registry(box.tags["labeled"])["current"]["tag"] == box.tags["labeled"]


def test_update_no_sigue_sin_copia_si_no_puede_leer_los_ajustes(box, installed):
    labeled = box.tags["labeled"]
    saved = box.in_volume(labeled, "cp -p /data/settings.yaml /data/settings.saved")
    assert saved.returncode == 0, saved.stderr
    broken = box.in_volume(labeled, "printf 'workspace: [sin cerrar\\n' > /data/settings.yaml")
    assert broken.returncode == 0, broken.stderr
    try:
        update = box.sh("update", box.tags["next"])
    finally:
        restored = box.in_volume(labeled, "mv /data/settings.saved /data/settings.yaml")
        assert restored.returncode == 0, restored.stderr

    assert update.returncode != 0
    assert "No se pudo leer /data/settings.yaml" in update.stderr
    assert "sin copia previa" not in update.stderr
    assert box.registry(labeled)["current"]["tag"] == labeled


def configure_backup(box):
    image = box.tags["labeled"]
    done = box.in_volume(image, "grep -q '^backup:' /data/settings.yaml")
    if done.returncode != 0:
        configure = box.in_volume(
            image,
            "printf 'backup:\\n  restic_repository: /data/restic-repo\\n' >> /data/settings.yaml",
        )
        assert configure.returncode == 0, configure.stderr


def test_update_aborta_si_la_copia_obligatoria_falla(box, installed):
    labeled = box.tags["labeled"]
    configure_backup(box)

    update = box.sh("update", box.tags["next"])

    assert update.returncode != 0
    assert "La copia previa falló" in update.stderr
    assert box.registry(labeled)["current"]["tag"] == labeled
    assert box.registry(labeled)["attempt"] is None


def test_copia_run_contra_un_repositorio_restic_local_desde_la_imagen_final(box, installed):
    image = box.tags["labeled"]
    password = "ENS_BACKUP_RESTIC_PASSWORD=prueba-t5"
    configure_backup(box)

    def ens(*args):
        return run(
            ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp"]
            + ["--cap-drop", "ALL"]
            + ["--user", UID, "-e", password, "-v", f"{box.volume}:/data"]
            + [f"ens-evidence:{image}", "ens-evidence", *args]
        )

    init = ens("backup", "init")
    assert init.returncode == 0, init.stdout + init.stderr

    backup = ens("backup", "run")
    assert backup.returncode == 0, backup.stdout + backup.stderr
    assert "completada" in backup.stdout
    assert "Snapshot" in backup.stdout


def test_restart_app_se_niega_si_la_etiqueta_de_la_imagen_ya_no_es_la_registrada(box, installed):
    labeled, good = box.tags["labeled"], box.tags["good"]
    registered = run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", f"ens-evidence:{labeled}"]
    )
    before = box.container("app")
    try:
        run(["docker", "tag", f"ens-evidence:{good}", f"ens-evidence:{labeled}"], check=True)

        restart = box.sh("restart", "app")

        assert restart.returncode != 0
        assert "no es la registrada" in restart.stderr
        assert box.container("app") == before, "no debe recrearse con otra imagen"
    finally:
        run(["docker", "tag", registered.stdout.strip(), f"ens-evidence:{labeled}"], check=True)


def test_dos_operaciones_a_la_vez_la_segunda_se_niega_sin_cambiar_nada(box, installed):
    labeled, following = box.tags["labeled"], box.tags["next"]
    lock = box.clone / ".git" / "ens-box" / f"{box.project}.lock"
    first = subprocess.Popen(
        ["sh", "deploy/box.sh", "update", "--no-backup", following],
        cwd=box.clone,
        env=box.env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 120
        while not lock.exists():
            assert first.poll() is None, "la primera operación terminó sin tomar el lock"
            assert time.monotonic() < deadline, "la primera operación no tomó el lock"
            time.sleep(0.05)

        for args in (
            ("update", "--no-backup", box.tags["crash"]),
            ("restart", "app"),
            ("rollback",),
        ):
            second = box.sh(*args)
            assert second.returncode != 0, args
            assert "otra operación de despliegue" in second.stderr, args
    finally:
        out, err = first.communicate(timeout=600)

    assert first.returncode == 0, out + err
    assert not lock.exists(), "el lock debe soltarse al terminar"
    registry = box.registry(following)
    assert registry["current"]["tag"] == following
    assert registry["previous"]["tag"] == labeled
    assert registry["attempt"] is None
