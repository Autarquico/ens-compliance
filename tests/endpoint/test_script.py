"""`collect-endpoint.sh` ejecutado con `sh` real y un `osqueryi` falso."""

import hashlib
import re
import shutil
import subprocess
import tarfile

import pytest

from ens_evidence.endpoint.pack import SCRIPT_NAME, load_pack, pack_files

from .support import LAPTOP_HOST, Answer, collect, members_of


def platform_queries(platform: str) -> set[str]:
    return {query.id for query in load_pack().queries if platform in query.platforms}


def test_el_script_es_sintacticamente_valido_para_sh(tmp_path):
    run = collect(tmp_path)

    result = subprocess.run(["sh", "-n", str(run.bundle / SCRIPT_NAME)], capture_output=True)

    assert result.returncode == 0, result.stderr


def test_el_script_nunca_evalua_ni_carga_contenido_del_paquete(tmp_path):
    script = (collect(tmp_path).bundle / SCRIPT_NAME).read_text()
    code = "\n".join(line.split("#", 1)[0] for line in script.splitlines())

    assert not re.search(r"\beval\b|\bsource\b|(^|[;&|(]\s*)\.\s+\S", code, re.MULTILINE)


def test_un_sql_malicioso_con_manifiesto_coherente_no_se_ejecuta_como_shell(tmp_path):
    sentinel = tmp_path / "pwned"

    def swap(bundle):
        poisoned = f"SELECT 1; $(touch {sentinel}) `touch {sentinel}`\n".encode()
        contents = {**pack_files(), "queries/alf.sql": poisoned}
        listing = "".join(
            f"{hashlib.sha256(body).hexdigest()}  {name}\n"
            for name, body in sorted(contents.items())
        ).encode()
        (bundle / "queries" / "alf.sql").write_bytes(poisoned)
        (bundle / "pack.manifest").write_bytes(listing)
        (bundle / "pack.sha256").write_text(hashlib.sha256(listing).hexdigest() + "\n")

    run = collect(tmp_path / "run", bundle_edit=swap)

    assert not sentinel.exists()
    assert run.process.returncode == 0, run.process.stderr


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_el_tar_contiene_solo_la_lista_blanca_de_miembros(tmp_path, platform):
    run = collect(tmp_path, platform=platform)

    with tarfile.open(run.archive) as archive:
        members = list(archive)
    assert all(member.isreg() for member in members)
    names = {member.name for member in members}
    assert names == {
        "meta.json",
        "SHA256SUMS",
        *(f"results/{query}.json" for query in platform_queries(platform)),
    }
    assert len(names) == len(members)


def test_meta_json_es_json_valido_aunque_el_hostname_tenga_caracteres_a_escapar(tmp_path):
    run = collect(tmp_path, hostname='ho"st\\na\tmeé')

    assert run.process.returncode == 0, run.process.stderr
    assert run.meta["hostname"].startswith('ho"st\\na')
    assert "\t" not in run.meta["hostname"]
    assert re.fullmatch(r"[A-Za-z0-9._-]+\.endpoint\.tar\.gz", run.archive.name)


def test_sha256sums_coincide_con_el_contenido_de_cada_miembro(tmp_path):
    members = members_of(collect(tmp_path).data)

    listed = dict(
        reversed(line.split("  ", 1)) for line in members.pop("SHA256SUMS").decode().splitlines()
    )

    assert listed.keys() == members.keys()
    assert all(hashlib.sha256(members[n]).hexdigest() == d for n, d in listed.items())


def test_meta_json_registra_el_paquete_de_consultas_la_plataforma_y_la_fecha(tmp_path):
    from ens_evidence.endpoint.pack import pack_sha256

    run = collect(tmp_path)

    assert run.meta["pack_sha256"] == pack_sha256()
    assert run.meta["platform"] == "darwin"
    assert run.meta["collected_at"].endswith("Z")
    assert run.meta["osquery_version"].startswith("osqueryi version")


def test_una_consulta_fallida_queda_vacia_con_su_codigo_de_salida(tmp_path):
    run = collect(tmp_path, answers={"alf": Answer(out=b"", code=3)})

    entry = next(q for q in run.meta["queries"] if q["id"] == "alf")
    assert entry["exit_code"] == 3
    assert members_of(run.data)["results/alf.json"] == b""
    assert all(q["exit_code"] == 0 for q in run.meta["queries"] if q["id"] != "alf")
    assert "alf" in run.process.stderr


def test_un_error_de_permisos_se_marca_pero_el_script_no_eleva(tmp_path):
    run = collect(
        tmp_path,
        answers={"disk_encryption_darwin": Answer(out=b"", code=1, err="Permission denied")},
    )

    entry = next(q for q in run.meta["queries"] if q["id"] == "disk_encryption_darwin")
    assert entry["permission_hint"] is True
    assert "no los eleva" in run.process.stderr


def test_una_consulta_que_agota_el_tiempo_se_registra_con_codigo_124(tmp_path):
    run = collect(tmp_path, timeout_expires=True)

    assert all(q["exit_code"] == 124 for q in run.meta["queries"])
    assert "60" in (run.fake_dir / "timeout.log").read_text()


def test_una_salida_mayor_que_el_limite_se_descarta(tmp_path):
    huge = b"[" + b" " * (11 * 1024 * 1024) + b"]"

    run = collect(tmp_path, answers={"apps": Answer(out=huge)})

    entry = next(q for q in run.meta["queries"] if q["id"] == "apps")
    assert entry["exit_code"] != 0
    assert members_of(run.data)["results/apps.json"] == b""


def edit_sql(bundle):
    (bundle / "queries" / "alf.sql").write_text("SELECT 1;\n")


def edit_manifest(bundle):
    manifest = bundle / "pack.manifest"
    manifest.write_text(manifest.read_text().replace("a", "b", 1))


def edit_pack_hash(bundle):
    (bundle / "pack.sha256").write_text("0" * 64 + "\n")


def add_unlisted_query(bundle):
    (bundle / "queries" / "extra.sql").write_text("SELECT 1;\n")
    with (bundle / "queries.list").open("a") as listing:
        listing.write("extra darwin,linux\n")


def drop_manifest(bundle):
    (bundle / "pack.manifest").unlink()


@pytest.mark.parametrize(
    "tamper", [edit_sql, edit_manifest, edit_pack_hash, add_unlisted_query, drop_manifest]
)
def test_un_paquete_de_consultas_manipulado_aborta_sin_producir_nada(tmp_path, tamper):
    run = collect(tmp_path, bundle_edit=tamper)

    assert run.process.returncode != 0
    assert run.archive is None
    assert list(run.out_dir.iterdir()) == []
    assert list(run.tmp.iterdir()) == []


def test_el_script_solo_escribe_en_su_directorio_temporal_y_en_el_de_salida(tmp_path):
    run = collect(tmp_path)

    assert [p.name for p in run.out_dir.iterdir()] == [run.archive.name]
    assert list(run.tmp.iterdir()) == []
    assert list(run.home.iterdir()) == []
    assert list(run.cwd.iterdir()) == []


def test_el_paquete_y_sus_miembros_no_son_accesibles_a_otros_usuarios(tmp_path):
    run = collect(tmp_path, umask="000")

    assert run.archive.stat().st_mode & 0o077 == 0
    with tarfile.open(run.archive) as archive:
        assert all(member.mode & 0o077 == 0 for member in archive)


def test_el_script_no_usa_sudo(tmp_path):
    script = (collect(tmp_path).bundle / SCRIPT_NAME).read_text()
    code = "\n".join(line.split("#", 1)[0] for line in script.splitlines())

    assert "sudo" not in code


def test_una_senal_de_terminacion_limpia_y_sale_con_error(tmp_path):
    run = collect(tmp_path, env={"FAKE_TERM_ON": "os_version"})

    assert run.process.returncode == 130
    assert list(run.out_dir.iterdir()) == []
    assert list(run.tmp.iterdir()) == []


STAMP = "20260101T000000Z"


def plant_links(victim):
    def prepare(out_dir):
        stem = f"{LAPTOP_HOST}-{STAMP}.endpoint.tar.gz"
        (out_dir / f"{stem}.part").symlink_to(victim)

    return prepare


def test_el_paquete_no_se_escribe_a_traves_de_enlaces_plantados_en_el_directorio_de_salida(
    tmp_path,
):
    victim = tmp_path / "victima.txt"
    victim.write_text("intacta")

    run = collect(tmp_path / "run", env={"FAKE_STAMP": STAMP}, prepare=plant_links(victim))

    assert run.archive is not None and not run.archive.is_symlink()
    assert victim.read_text() == "intacta"


def test_un_destino_final_que_es_un_enlace_se_rechaza(tmp_path):
    victim = tmp_path / "victima.txt"
    victim.write_text("intacta")

    def prepare(out_dir):
        (out_dir / f"{LAPTOP_HOST}-{STAMP}.endpoint.tar.gz").symlink_to(victim)

    run = collect(tmp_path / "run", env={"FAKE_STAMP": STAMP}, prepare=prepare)

    assert run.process.returncode != 0
    assert victim.read_text() == "intacta"
    assert not any(p.name.endswith(".part") for p in run.out_dir.iterdir())


def test_las_cabeceras_del_tar_no_filtran_usuario_ni_grupo(tmp_path):
    run = collect(tmp_path)

    with tarfile.open(run.archive) as archive:
        members = list(archive)
    assert members
    assert all((m.uname, m.gname, m.uid, m.gid) == ("", "", 0, 0) for m in members)


def test_un_destino_que_aparece_entre_medias_no_se_sobrescribe(tmp_path):
    victim = tmp_path / "victima.txt"
    victim.write_text("intacta")
    destination = f"{LAPTOP_HOST}-{STAMP}.endpoint.tar.gz"

    def appear_after_tar(out_dir):
        wrapper = out_dir.parent / "fakebin" / "tar"
        wrapper.write_text(
            f'#!/bin/sh\n{shutil.which("tar")} "$@" || exit $?\n'
            f'ln -s "{victim}" "{out_dir / destination}"\n'
        )
        wrapper.chmod(0o755)

    run = collect(tmp_path / "run", env={"FAKE_STAMP": STAMP}, prepare=appear_after_tar)

    assert run.process.returncode != 0
    assert victim.read_text() == "intacta"


def test_un_destino_que_es_un_enlace_a_un_directorio_no_se_trata_como_directorio(tmp_path):
    target = tmp_path / "otro"
    target.mkdir()

    def prepare(out_dir):
        (out_dir / f"{LAPTOP_HOST}-{STAMP}.endpoint.tar.gz").symlink_to(target)

    run = collect(tmp_path / "run", env={"FAKE_STAMP": STAMP}, prepare=prepare)

    assert run.process.returncode != 0
    assert list(target.iterdir()) == []


def test_un_directorio_de_salida_escribible_por_otros_sin_sticky_se_rechaza(tmp_path):
    def prepare(out_dir):
        out_dir.chmod(0o777)

    run = collect(tmp_path, prepare=prepare)

    assert run.process.returncode != 0
    assert run.archive is None or not run.archive.exists()
    assert list(run.out_dir.iterdir()) == []


def test_un_directorio_de_salida_escribible_por_otros_con_sticky_se_acepta(tmp_path):
    def prepare(out_dir):
        out_dir.chmod(0o1777)

    run = collect(tmp_path, prepare=prepare)

    assert run.process.returncode == 0, run.process.stderr
