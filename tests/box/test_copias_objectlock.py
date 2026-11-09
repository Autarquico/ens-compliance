"""Las credenciales de lectura de la app no pueden borrar copias (kit-backups, C7).

Un MinIO con bloqueo de objetos hace de S3 (solo en tests; no entra en el producto) y el restic
que lleva Backrest hace de cliente. `pytest -m docker`; fuera del gate por defecto. Prueba
manual equivalente en R2: docs/runbooks/kit-copias.md, sección 8.
"""

import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.docker
MINIO = "cgr.dev/chainguard/minio:latest"
MC = "cgr.dev/chainguard/minio-client:latest"
BUCKET = "copias"
ROOT_USER, ROOT_PASSWORD = "root-user", "root-password-1"
WRITER, WRITER_KEY = "escritor", "escritor-clave-1"
READER, READER_KEY = "lector", "lector-clave-1"
READ_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"],
        }
    ],
}


def docker_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


if not docker_available():
    pytest.skip("Docker no está disponible", allow_module_level=True)


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=300, **kwargs)


def backrest_image():
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    return re.search(r"image: (ghcr\.io/garethgeorge/backrest\S+)", compose).group(1)


@pytest.fixture(scope="module")
def s3(tmp_path_factory):
    suffix = secrets.token_hex(3)
    network, server = f"ensobjlock{suffix}", f"ensobjlock{suffix}-minio"
    for image in (MINIO, MC, backrest_image()):
        if run(["docker", "pull", "-q", image]).returncode != 0:
            pytest.skip(f"no se puede descargar {image}")
    policy = tmp_path_factory.mktemp("policy")
    (policy / "lectura.json").write_text(json.dumps(READ_POLICY))
    run(["docker", "network", "create", network], check=True)
    try:
        run(
            ["docker", "run", "-d", "--rm", "--name", server, "--network", network]
            + ["-e", f"MINIO_ROOT_USER={ROOT_USER}", "-e", f"MINIO_ROOT_PASSWORD={ROOT_PASSWORD}"]
            + [MINIO, "server", "/data"],
            check=True,
        )

        def mc(*args):
            return run(
                ["docker", "run", "--rm", "--network", network, "-v", f"{policy}:/p:ro"]
                + ["-e", f"MC_HOST_local=http://{ROOT_USER}:{ROOT_PASSWORD}@{server}:9000"]
                + [MC, *args]
            )

        for _ in range(30):
            if mc("mb", "--with-lock", f"local/{BUCKET}").returncode == 0:
                break
            run(["sleep", "1"])
        else:
            pytest.fail("MinIO no arranca")
        steps = [
            ("retention", "set", "--default", "GOVERNANCE", "1d", f"local/{BUCKET}"),
            ("admin", "user", "add", "local", WRITER, WRITER_KEY),
            ("admin", "policy", "attach", "local", "readwrite", "--user", WRITER),
            ("admin", "user", "add", "local", READER, READER_KEY),
            ("admin", "policy", "create", "local", "lectura", "/p/lectura.json"),
            ("admin", "policy", "attach", "local", "lectura", "--user", READER),
        ]
        for step in steps:
            done = mc(*step)
            assert done.returncode == 0, (step, done.stderr)

        def restic(user, key, *args):
            return run(
                ["docker", "run", "--rm", "--network", network, "--read-only"]
                + ["--tmpfs", "/tmp", "--tmpfs", "/cache", "-e", "XDG_CACHE_HOME=/cache"]
                + ["-e", "RESTIC_PASSWORD=contrasena-del-repo"]
                + ["-e", f"AWS_ACCESS_KEY_ID={user}", "-e", f"AWS_SECRET_ACCESS_KEY={key}"]
                + ["--entrypoint", "/bin/restic", backrest_image()]
                + ["-r", f"s3:http://{server}:9000/{BUCKET}", *args]
            )

        assert restic(WRITER, WRITER_KEY, "init").returncode == 0
        backup = restic(WRITER, WRITER_KEY, "backup", "/etc/ssl")
        assert backup.returncode == 0, backup.stderr
        yield restic, mc
    finally:
        run(["docker", "rm", "-f", server])
        run(["docker", "network", "rm", network])


def snapshot_id(restic):
    listing = restic(READER, READER_KEY, "snapshots", "--no-lock", "--json")
    assert listing.returncode == 0, listing.stderr
    return json.loads(listing.stdout)[0]["id"]


def test_con_las_credenciales_de_lectura_se_listan_las_copias_sin_bloquear(s3):
    restic, _ = s3

    listing = restic(READER, READER_KEY, "snapshots", "--no-lock")

    assert listing.returncode == 0, listing.stderr
    assert "1 snapshots" in listing.stdout


def test_sin_no_lock_las_credenciales_de_lectura_no_pueden_ni_listar(s3):
    restic, _ = s3

    result = restic(READER, READER_KEY, "snapshots")

    assert result.returncode != 0
    assert "Access Denied" in result.stderr


def test_con_las_credenciales_de_lectura_forget_y_backup_fallan_y_la_copia_sigue(s3):
    restic, _ = s3
    before = snapshot_id(restic)

    forget = restic(READER, READER_KEY, "forget", before)
    backup = restic(READER, READER_KEY, "backup", "/etc/ssl")

    assert forget.returncode != 0 and "Access Denied" in forget.stderr
    assert backup.returncode != 0
    assert snapshot_id(restic) == before


def test_el_bloqueo_de_objetos_retiene_la_version_aunque_se_borre_con_la_clave_de_escritura(s3):
    """Con versionado, `restic forget` solo pone una marca de borrado: el dato sigue retenido.

    Lo que impide borrarlo es el bloqueo (no la contraseña ni restic) y lo que impide que la app
    lo intente es la política de solo lectura de arriba. El runbook lo explica.
    """
    restic, mc = s3
    snapshot = snapshot_id(restic)

    forget = restic(WRITER, WRITER_KEY, "forget", snapshot)
    versions = mc("ls", "--versions", f"local/{BUCKET}/snapshots/{snapshot}")
    version = re.search(r"([0-9a-f-]{36}) v1 PUT", versions.stdout).group(1)
    removed = mc("rm", "--version-id", version, f"local/{BUCKET}/snapshots/{snapshot}")

    assert forget.returncode == 0, forget.stderr
    assert " DEL " in versions.stdout, "forget deja una marca de borrado, no borra el dato"
    assert removed.returncode != 0
    assert "worm" in (removed.stdout + removed.stderr).lower()
