"""Kit de copias de extremo a extremo (`pytest -m docker`, fuera del gate por defecto).

`render_bundle` -> `box.sh kit enable copias` -> Backrest v1.14.1 real copia a un MinIO con
bloqueo de objetos -> el conector `backups` lee con las credenciales de lectura del paquete.

Única desviación del paquete: el destino de prueba es un MinIO con certificado autofirmado
(el producto exige https), así que se añade al `config.json` la CA de prueba (`RESTIC_CACERT`)
y se vuelve a firmar el MANIFEST. El restic del conector corre en un contenedor (el MinIO solo
es alcanzable por la red de Docker), con la misma imagen que Backrest.
"""

# ruff: noqa: F401, F811  (fixtures de la caja importadas de test_integration)
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import time
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import SecretStr

from box.test_copias_objectlock import MC, MINIO, backrest_image
from box.test_integration import (
    box,
    installed,
    run,
    ui_env_with_copias_host,
)
from ens_evidence.connectors.backups import BackupsConnector, BackupsRun
from ens_evidence.connectors.backups import checks as backups_checks
from ens_evidence.kit.backups import BackupInputs, BundleSecrets, render_bundle
from ens_evidence.models.enums import CheckStatus

pytestmark = pytest.mark.docker
BUCKET = "copias"
ROOT = ("root-user", "root-password-1")
WRITER = ("escritor-id-01", "escritor-clave-1")
READER = ("lector-id-001", "lector-clave-1")
REPO_PASSWORD = "contrasena-del-repo-0123"
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


class S3:
    def __init__(self, network, server, certs, ca_dir):
        self.network, self.server, self.certs, self.ca_dir = network, server, certs, ca_dir

    @property
    def endpoint(self):
        return f"https://{self.server}:9000"

    def mc(self, *args):
        user, key = ROOT
        return run(
            ["docker", "run", "--rm", "--network", self.network, "-v", f"{self.certs}:/p:ro"]
            + ["-e", f"MC_HOST_local=https://{user}:{key}@{self.server}:9000"]
            + [MC, "--insecure", *args]
        )

    def restic(self, credentials, *args, repo_prefix="caja"):
        """restic de la imagen de Backrest, en la red del MinIO y confiando en su CA."""
        user, key = credentials
        return run(
            ["docker", "run", "--rm", "--network", self.network, "--read-only"]
            + ["--tmpfs", "/tmp", "--tmpfs", "/cache", "-e", "XDG_CACHE_HOME=/cache"]
            + ["-v", f"{self.ca_dir}:/ca:ro", "-e", "RESTIC_CACERT=/ca/public.crt"]
            + ["-e", f"RESTIC_PASSWORD={REPO_PASSWORD}"]
            + ["-e", f"AWS_ACCESS_KEY_ID={user}", "-e", f"AWS_SECRET_ACCESS_KEY={key}"]
            + ["--entrypoint", "/bin/restic", backrest_image()]
            + ["-r", f"s3:{self.endpoint}/{BUCKET}/{repo_prefix}", *args]
        )


@pytest.fixture(scope="module")
def s3(tmp_path_factory):
    suffix = secrets.token_hex(3)
    network, server = f"ensk2e{suffix}", f"ensk2e{suffix}-minio"
    for image in (MINIO, MC, backrest_image()):
        if run(["docker", "pull", "-q", image]).returncode != 0:
            pytest.skip(f"no se puede descargar {image}")
    if shutil.which("openssl") is None:
        pytest.skip("falta openssl")
    certs = tmp_path_factory.mktemp("certs")
    ca_dir = tmp_path_factory.mktemp("ca")
    run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2"]
        + ["-keyout", str(certs / "private.key"), "-out", str(certs / "public.crt")]
        + ["-subj", f"/CN={server}", "-addext", f"subjectAltName=DNS:{server}"],
        check=True,
    )
    shutil.copy(certs / "public.crt", ca_dir / "public.crt")
    for path in (certs, ca_dir):
        path.chmod(0o755)
    for path in (certs / "private.key", certs / "public.crt", ca_dir / "public.crt"):
        path.chmod(0o644)
    (certs / "lectura.json").write_text(json.dumps(READ_POLICY))
    handle = S3(network, server, certs, ca_dir)
    run(["docker", "network", "create", network], check=True)
    try:
        run(
            ["docker", "run", "-d", "--rm", "--name", server, "--network", network]
            + ["-e", f"MINIO_ROOT_USER={ROOT[0]}", "-e", f"MINIO_ROOT_PASSWORD={ROOT[1]}"]
            + ["-v", f"{certs}:/certs:ro", MINIO, "server", "/data", "--certs-dir", "/certs"],
            check=True,
        )
        for _ in range(40):
            if handle.mc("mb", "--with-lock", f"local/{BUCKET}").returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("MinIO no arranca")
        steps = [
            ("retention", "set", "--default", "GOVERNANCE", "1d", f"local/{BUCKET}"),
            ("admin", "user", "add", "local", *WRITER),
            ("admin", "policy", "attach", "local", "readwrite", "--user", WRITER[0]),
            ("admin", "user", "add", "local", *READER),
            ("admin", "policy", "create", "local", "lectura", "/p/lectura.json"),
            ("admin", "policy", "attach", "local", "lectura", "--user", READER[0]),
        ]
        for step in steps:
            done = handle.mc(*step)
            assert done.returncode == 0, (step, done.stderr)
        yield handle
    finally:
        run(["docker", "rm", "-f", server])
        run(["docker", "network", "rm", network])


def build_package(s3, directory):
    """El paquete real de `render_bundle` + la CA de prueba (única desviación, ver el módulo)."""
    inputs = BackupInputs.model_validate(
        {
            "id": "caja",
            "asset_id": "bk-caja",
            "destination": {
                "endpoint": s3.endpoint,
                "bucket": BUCKET,
                "prefix": "caja",
                "lock_days": 1,
            },
            "schedule": {
                "cron": "* * * * *",
                "retention_days": 7,
                "max_backup_age_hours": 26,
                "max_check_age_days": 35,
                "restore_test_validity_days": 365,
            },
            "volumes": ["data"],
            "host_paths": [str(s3.ca_dir)],
        }
    )
    secrets_ = BundleSecrets(
        repo_password=SecretStr(REPO_PASSWORD),
        write_access_key_id=SecretStr(WRITER[0]),
        write_secret_access_key=SecretStr(WRITER[1]),
        read_access_key_id=SecretStr(READER[0]),
        read_secret_access_key=SecretStr(READER[1]),
    )
    blob = render_bundle(inputs, secrets_, datetime.now(UTC))
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    config = json.loads(files["copias/config.json"])
    config["repos"][0]["env"].append(f"RESTIC_CACERT=/source/host{s3.ca_dir}/public.crt")
    files["copias/config.json"] = (json.dumps(config, indent=2) + "\n").encode()
    listed = [n for n in files if n != "copias/MANIFEST"]
    files["copias/MANIFEST"] = (
        "version: 1\n" + "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in listed)
    ).encode()
    path = directory / "paquete.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path, files


@pytest.fixture(scope="module")
def enable_result(box, ui_env_with_copias_host, s3, tmp_path_factory):
    path, files = build_package(s3, tmp_path_factory.mktemp("paquete"))
    result = box.sh("kit", "enable", "copias", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    run(["docker", "network", "connect", f"{box.project}_copias", s3.server], check=True)
    return result, files


@pytest.fixture(scope="module")
def enabled(enable_result):
    return enable_result[1]


@pytest.fixture(scope="module")
def backrest_password(enable_result):
    found = re.search(r"^  Contraseña: (\S{32})$", enable_result[0].stdout, re.MULTILINE)
    assert found, "box.sh no muestra la contraseña inicial de Backrest"
    return found.group(1)


def wait_for_snapshot(s3, minutes=5):
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        listing = s3.restic(READER, "snapshots", "--no-lock", "--json")
        if listing.returncode == 0 and json.loads(listing.stdout or "[]"):
            return json.loads(listing.stdout)
        time.sleep(10)
    return []


@pytest.fixture(scope="module")
def snapshots(box, s3, enabled):
    found = wait_for_snapshot(s3)
    logs = run(["docker", "logs", "--tail", "40", box.container("backrest")])
    assert found, "Backrest no hizo la copia a tiempo:\n" + logs.stdout + logs.stderr
    return found


def test_backrest_arranca_con_el_config_json_del_paquete_y_no_deja_restos_de_claves(
    box, enabled, s3
):
    assert box.inspect("backrest", "{{.State.Status}}") == "running"
    listing = run(
        ["docker", "run", "--rm", "-v", f"{box.project}_backrest-config:/c", "alpine:3", "ls", "/c"]
    )
    assert "config.json.prev" not in listing.stdout
    assert "config.json.pre-update" not in listing.stdout


def installed_config(box):
    shown = run(
        ["docker", "run", "--rm", "-v", f"{box.project}_backrest-config:/c", "alpine:3"]
        + ["cat", "/c/config.json"]
    )
    assert shown.returncode == 0, shown.stderr
    return shown.stdout


def backrest_rpc(box, method, body, *, password=None):
    """Una llamada a Backrest por la red interna; devuelve (código HTTP, cuerpo)."""
    headers = ["--header", "Content-Type: application/json"]
    if password:
        token = base64.b64encode(f"admin:{password}".encode()).decode()
        headers += ["--header", f"Authorization: Basic {token}"]
    command = (
        ["docker", "run", "--rm", "--network", f"{box.project}_copias-net", "alpine:3"]
        + ["wget", "-S", "-q", "-O-", *headers, "--post-data", body]
        + [f"http://backrest:9898/{method}"]
    )
    deadline = time.monotonic() + 60
    while True:  # Backrest tarda unos segundos en escuchar tras arrancar
        shown = run(command)
        code = re.search(r"HTTP/1\.\d (\d{3})", shown.stderr)
        if code or time.monotonic() > deadline:
            break
        time.sleep(3)
    assert code, shown.stderr
    return code.group(1), shown.stdout


def test_el_config_json_instalado_lleva_el_usuario_inicial_con_bcrypt(
    box, enabled, backrest_password
):
    text = installed_config(box)
    auth = json.loads(text)["auth"]

    assert (
        auth.get("disabled", False) is False
    )  # Backrest reescribe el JSON sin los valores por defecto
    assert auth["users"][0]["name"] == "admin"
    hashed = base64.b64decode(auth["users"][0]["passwordBcrypt"]).decode()
    assert re.fullmatch(r"\$2[aby]\$\d\d\$\S{53}", hashed)
    assert backrest_password not in text


def test_backrest_publicado_exige_la_contrasena_mostrada(box, enabled, backrest_password):
    anonymous, _ = backrest_rpc(box, "v1.Backrest/GetConfig", "{}")
    wrong, _ = backrest_rpc(box, "v1.Backrest/GetConfig", "{}", password="x" * 32)
    right, _ = backrest_rpc(box, "v1.Backrest/GetConfig", "{}", password=backrest_password)

    assert (anonymous, wrong, right) == ("401", "401", "200")


def test_la_contrasena_de_backrest_solo_sale_por_la_terminal(box, enable_result, backrest_password):
    result, _ = enable_result
    assert result.stdout.count(backrest_password) == 1
    assert backrest_password not in result.stderr
    for path in (box.clone / "deploy").rglob("*"):
        if path.is_file():
            assert backrest_password not in path.read_text(errors="ignore"), path
    for service in ("backrest", "caddy", "app"):
        logs = run(["docker", "logs", box.container(service)])
        assert backrest_password not in logs.stdout + logs.stderr, service


def test_backrest_crea_el_repositorio_en_minio_con_bloqueo_y_hace_una_copia(box, s3, snapshots):
    assert snapshots[0]["paths"] == ["/source/caja", f"/source/host{s3.ca_dir}"]
    objects = s3.mc("ls", f"local/{BUCKET}/caja")
    assert "config" in objects.stdout and "snapshots/" in objects.stdout
    lock = s3.mc("retention", "info", "--default", f"local/{BUCKET}")
    assert "GOVERNANCE" in lock.stdout


def connector_view(s3, files, tmp_path):
    """El conector con el `backups.yaml` y las credenciales de lectura del paquete."""
    wrapper = tmp_path / "bin"
    wrapper.mkdir()
    script = wrapper / "restic"
    args = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        s3.network,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/cache",
        "-e",
        "XDG_CACHE_HOME=/cache",
        "-v",
        f"{s3.ca_dir}:/ca:ro",
        "-e",
        "RESTIC_CACERT=/ca/public.crt",
        "-e",
        "RESTIC_PASSWORD",
        "-e",
        "AWS_ACCESS_KEY_ID",
        "-e",
        "AWS_SECRET_ACCESS_KEY",
        "--entrypoint",
        "/bin/restic",
        backrest_image(),
    ]
    script.write_text("#!/bin/sh\nexec " + " ".join(f"'{a}'" for a in args) + ' "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{wrapper}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    for line in files["copias/app.env"].decode().splitlines():
        key, _, value = line.partition("=")
        env[key] = value
    config = BackupsRun(**yaml.safe_load(files["copias/backups.yaml"]))
    result = BackupsConnector(env=env).collect(config)
    return result, {
        r.check_id: r.status
        for check in backups_checks.ALL_CHECKS
        for r in check(result, "ev-1", datetime.now(UTC))
    }


def test_el_conector_backups_con_las_credenciales_de_lectura_ve_la_copia_y_la_integridad(
    s3, enabled, snapshots, tmp_path
):
    result, statuses = connector_view(s3, enabled, tmp_path)

    entry = result.data["repositories"][0]
    assert entry["error"] is None, entry
    assert entry["asset_id"] == "bk-caja"
    assert statuses["backups.backup_001_recent"] is CheckStatus.PASS, statuses
    assert statuses["backups.backup_004_integrity"] is CheckStatus.PASS, statuses
    assert re.fullmatch(r"[0-9a-f]{64}", entry["repository_id"])


def test_restic_check_no_lock_con_las_credenciales_de_lectura_pasa(s3, snapshots):
    check = s3.restic(READER, "--no-lock", "check")

    assert check.returncode == 0, check.stderr


def test_con_las_credenciales_de_lectura_forget_falla_y_la_copia_sigue(s3, snapshots):
    forget = s3.restic(READER, "forget", snapshots[0]["id"])

    assert forget.returncode != 0
    after = s3.restic(READER, "snapshots", "--no-lock", "--json")
    assert snapshots[0]["id"] in {s["id"] for s in json.loads(after.stdout)}


def test_las_credenciales_de_escritura_no_estan_fuera_del_volumen_de_backrest(box, enabled):
    env = (box.clone / "deploy" / ".env").read_text()
    assert WRITER[1] not in env
    assert READER[1] in env
    leaks = run(["grep", "-rl", "--exclude-dir=tests", WRITER[1], str(box.clone)]).stdout
    assert leaks.strip() == ""
