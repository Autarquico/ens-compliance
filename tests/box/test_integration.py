"""La caja con Docker de verdad: `pytest -m docker`. Fuera del gate por defecto.

Todo vive bajo un proyecto Compose con prefijo único (`ENS_BOX_PROJECT`) y etiquetas de imagen
propias, en un clon temporal con sus propias etiquetas git. Al terminar solo se elimina lo que
lleva ese prefijo o esas etiquetas; nunca se toca la caja real (`ens-box`).
"""

import json
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

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
    good: str
    bad: str

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

    def container_image(self, service):
        compose = ["docker", "compose", "-p", self.project, "-f", "compose.yaml"]
        ids = run([*compose, "ps", "-q", service], cwd=self.clone, env=self.env(ENS_VERSION="x"))
        return run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", ids.stdout.split()[0]]
        ).stdout.strip()


def commit_all(clone, message, tag):
    git = ["git", "-c", "user.name=t5", "-c", "user.email=t5@example.invalid"]
    run([*git, "add", "-A"], cwd=clone, check=True)
    run([*git, "commit", "-q", "-m", message], cwd=clone, check=True)
    run([*git, "tag", tag], cwd=clone, check=True)


# Una versión que escribe un informe y un marcador y luego no late: falla su healthcheck.
BROKEN_CMD = (
    'CMD ["sh", "-c", "ens-evidence report daily --cycle; '
    'rm -f /data/state/scheduler-heartbeat; exec sleep 3600"]'
)


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    suffix = secrets.token_hex(3)
    project = f"ensbxt{suffix}"
    serial = int(suffix, 16) % 900000 + 100000
    good, bad = f"v9{serial}.0.0", f"v9{serial}.0.1"
    clone = tmp_path_factory.mktemp("box-clone")

    tracked = run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=REPO, check=True
    ).stdout.split("\0")
    for name in filter(None, tracked):
        if (REPO / name).is_file():
            (clone / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, clone / name)
    run(["git", "init", "-q"], cwd=clone, check=True)
    commit_all(clone, "good", good)
    dockerfile = clone / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert 'CMD ["ens-evidence", "scheduler", "run"]' in text
    dockerfile.write_text(
        text.replace('CMD ["ens-evidence", "scheduler", "run"]', BROKEN_CMD), encoding="utf-8"
    )
    commit_all(clone, "bad", bad)

    handle = Box(clone, project, good, bad)
    yield handle

    # Solo lo que lleva el prefijo del proyecto y las etiquetas de prueba.
    compose = ["docker", "compose", "-p", project, "-f", "compose.yaml"]
    run([*compose, "down", "-v", "--remove-orphans"], cwd=clone, env=handle.env(ENS_VERSION=good))
    for volume in (f"{project}_data", f"{project}_caddy-data", f"{project}_caddy-config"):
        run(["docker", "volume", "rm", "-f", volume])
    run(["docker", "rmi", "-f", f"ens-evidence:{good}", f"ens-evidence:{bad}"])


def test_arranque_limpio_con_volumen_vacio_instala_y_deja_el_primer_ciclo_hecho(box):
    assert run(["docker", "volume", "inspect", box.volume]).returncode != 0

    result = box.sh("install", box.good)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "primer ciclo" in result.stdout
    assert "falló" not in result.stderr  # 0 y 3 son válidos; 2 aborta el install
    assert box.report_files(box.good), "el primer ciclo debe dejar informes en el workspace"
    assert box.registry(box.good)["current"]["tag"] == box.good
    assert box.container_image("app") == f"ens-evidence:{box.good}"


def test_el_volumen_pertenece_al_uid_de_la_app_no_a_root(box):
    owner = box.in_volume(box.good, "stat -c %u /data /data/workspace /data/settings.yaml")

    assert owner.stdout.split() == [UID, UID, UID]


def test_ningun_contenedor_publica_puertos(box):
    ids = run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={box.project}"])
    assert ids.stdout.split()

    for container in ids.stdout.split():
        out = run(["docker", "inspect", "--format", "{{json .HostConfig.PortBindings}}", container])
        assert out.stdout.strip() in ("{}", "null")


def test_la_ui_no_es_publica_toda_ruta_da_403_desde_la_red_interna(box):
    caddy_image = yaml.safe_load((box.clone / "compose.yaml").read_text())["services"]["caddy"][
        "image"
    ]

    for path in ("/", "/admin", "/agent/enroll?token=abc"):
        result = run(
            ["docker", "run", "--rm", "--network", f"{box.project}_edge", caddy_image]
            + ["wget", "-S", "-O", "/dev/null", f"http://caddy:8080{path}"]
        )
        assert "403" in result.stderr, (path, result.stderr)


def test_rollback_tras_una_version_que_falla_conserva_informes_y_marcadores(box):
    before = box.report_files(box.good)

    update = box.sh("update", "--no-backup", box.bad, ENS_BOX_HEALTH_TIMEOUT="45")

    assert update.returncode != 0
    assert "rollback" in update.stderr
    written = box.report_files(box.bad)
    assert before.items() <= written.items(), "la nueva versión no puede alterar lo anterior"
    assert set(written) - set(before), "la versión rota debe haber escrito un informe o marcador"

    rollback = box.sh("rollback", ENS_BOX_HEALTH_TIMEOUT="120")

    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert box.container_image("app") == f"ens-evidence:{box.good}"
    assert box.report_files(box.good) == written
    registry = box.registry(box.good)
    assert registry["current"]["tag"] == box.good
    assert registry["failed"]["tag"] == box.bad

    compose = ["docker", "compose", "-p", box.project, "-f", "compose.yaml"]
    env = box.env(ENS_VERSION=box.good)
    for command in (["scheduler", "status"], ["evidence", "verify", "--all"]):
        read = run(
            [*compose, "exec", "-T", "app", "ens-evidence", *command], cwd=box.clone, env=env
        )
        assert read.returncode == 0, (command, read.stdout, read.stderr)


def test_copia_run_contra_un_repositorio_restic_local_desde_la_imagen_final(box):
    password = ["ENS_BACKUP_RESTIC_PASSWORD=prueba-t5"]
    configure = box.in_volume(
        box.good,
        "printf 'backup:\\n  restic_repository: /data/restic-repo\\n' >> /data/settings.yaml",
    )
    assert configure.returncode == 0, configure.stderr

    def ens(*args):
        return run(
            ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp"]
            + ["--cap-drop", "ALL"]
            + ["--user", UID, "-e", password[0], "-v", f"{box.volume}:/data"]
            + [f"ens-evidence:{box.good}", "ens-evidence", *args]
        )

    init = ens("backup", "init")
    assert init.returncode == 0, init.stdout + init.stderr

    backup = ens("backup", "run")
    assert backup.returncode == 0, backup.stdout + backup.stderr
    assert "completada" in backup.stdout
    assert "Snapshot" in backup.stdout
