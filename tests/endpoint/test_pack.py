"""Paquete de consultas versionado: hash, contenido y datos del wheel."""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from ens_evidence.endpoint.pack import (
    PACK_DIR,
    SCRIPT_NAME,
    bundle_files,
    load_pack,
    manifest,
    pack_files,
    pack_sha256,
)

REPO = Path(__file__).resolve().parents[2]


def test_el_hash_del_paquete_es_determinista():
    assert pack_sha256() == pack_sha256()
    assert len(pack_sha256()) == 64


def test_el_hash_cambia_si_cambia_cualquier_consulta():
    contents = pack_files()
    changed = {**contents, "queries/alf.sql": contents["queries/alf.sql"] + b" "}

    assert manifest(changed) != manifest(contents)


def test_el_manifiesto_entregado_al_endpoint_es_el_que_da_el_hash():
    import hashlib

    delivered = bundle_files()

    assert hashlib.sha256(delivered["pack.manifest"]).hexdigest() == pack_sha256()
    assert delivered["pack.sha256"].decode().strip() == pack_sha256()


def test_cada_consulta_tiene_plataformas_y_proposito():
    for query in load_pack().queries:
        assert query.platforms, query.id
        assert query.purpose.strip(), query.id


def test_cada_consulta_tiene_su_fichero_sql_y_no_sobra_ninguno():
    declared = {f"{query.id}.sql" for query in load_pack().queries}
    on_disk = {entry.name for entry in PACK_DIR.iterdir() if entry.name.endswith(".sql")}

    assert on_disk == declared
    assert all((PACK_DIR / name).read_text().strip() for name in declared)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv no está instalado")
def test_el_wheel_incluye_las_consultas_y_el_script(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO,
        check=True,
        capture_output=True,
    )

    wheel = next(tmp_path.glob("ens_evidence-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    expected = {
        "ens_evidence/endpoint/queries/v2/pack.yaml",
        f"ens_evidence/endpoint/{SCRIPT_NAME}",
        *(f"ens_evidence/endpoint/queries/v2/{query.id}.sql" for query in load_pack().queries),
    }
    assert expected <= names


def test_las_consultas_no_piden_datos_personales_que_ningun_check_usa():
    sql = {name: body.decode() for name, body in pack_files().items() if name.endswith(".sql")}

    assert sql["queries/system_info.sql"].strip() == "SELECT hostname, uuid FROM system_info;"
    policy = sql["queries/software_update_policy.sql"]
    assert "AS user_scoped" in policy
    assert "username," not in policy.split("CASE")[0] and "SELECT username" not in policy
    assert not any("computer_name" in body for body in sql.values())


def test_disk_encryption_darwin_selecciona_columnas_explicitas_con_filevault():
    sql = bundle_files()["queries/disk_encryption_darwin.sql"].decode()

    assert "*" not in sql
    assert sql.strip() == (
        "SELECT name, uuid, encrypted, type, encryption_status, filevault_status "
        "FROM disk_encryption;"
    )
