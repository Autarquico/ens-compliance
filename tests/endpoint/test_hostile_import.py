"""Un paquete hostil o inconsistente se rechaza entero y no persiste nada."""

import gzip
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from ens_evidence.endpoint.pack import load_pack
from ens_evidence.endpoint.package import MAX_PACKAGE_BYTES
from ens_evidence.evidence import EvidenceImportError

from .support import (
    CONFIG,
    LAPTOP_HOST,
    LAPTOP_UUID,
    build,
    import_package,
    members_of,
    normalized,
    persisted_keys,
    rebuilt,
    regular,
    tar_of,
)

MIB = 1024 * 1024


def rejected(store, data: bytes, *, asset: str = "lap-ana") -> str:
    with pytest.raises(EvidenceImportError) as error:
        import_package(store, data, asset)
    assert persisted_keys(store) == []
    return str(error.value)


def rejected_keeping(store, data: bytes) -> str:
    with pytest.raises(EvidenceImportError) as error:
        import_package(store, data)
    return str(error.value)


def special(name: str, kind: bytes, **fields) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    for key, value in fields.items():
        setattr(info, key, value)
    return info


def entries_of(data: bytes) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    return [regular(name, body) for name, body in members_of(data).items()]


@pytest.mark.parametrize("name", ["/etc/evil.json", "../evil.json", "results/../../evil.json"])
def test_rutas_absolutas_o_con_punto_punto_se_rechazan(store, laptop_package, name):
    data = rebuilt(laptop_package, add={name: b"[]"})

    rejected(store, data)


def test_un_enlace_simbolico_se_rechaza(store, laptop_package):
    link = special("results/alf.json", tarfile.SYMTYPE, linkname="/etc/passwd")
    entries = [e for e in entries_of(laptop_package) if e[0].name != "results/alf.json"]

    rejected(store, tar_of([*entries, (link, None)]))


def test_un_enlace_duro_se_rechaza(store, laptop_package):
    link = special("results/alf.json", tarfile.LNKTYPE, linkname="results/apps.json")
    entries = [e for e in entries_of(laptop_package) if e[0].name != "results/alf.json"]

    rejected(store, tar_of([*entries, (link, None)]))


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_un_dispositivo_o_fifo_se_rechaza(store, laptop_package, kind):
    device = special("results/alf.json", kind, devmajor=1, devminor=3)
    entries = [e for e in entries_of(laptop_package) if e[0].name != "results/alf.json"]

    rejected(store, tar_of([*entries, (device, None)]))


def test_un_directorio_se_rechaza(store, laptop_package):
    directory = special("results", tarfile.DIRTYPE)

    rejected(store, tar_of([(directory, None), *entries_of(laptop_package)]))


def test_un_miembro_duplicado_se_rechaza(store, laptop_package):
    entries = entries_of(laptop_package)
    duplicate = regular("results/alf.json", b"[]")

    rejected(store, tar_of([*entries, duplicate]))


def test_un_miembro_extra_fuera_de_la_lista_blanca_se_rechaza(store, laptop_package):
    rejected(store, rebuilt(laptop_package, add={"results/extra.json": b"[]"}))


def test_demasiados_miembros_se_rechazan(store, laptop_package):
    present = set(members_of(laptop_package))
    full = {
        f"results/{query.id}.json": b"[]"
        for query in load_pack().queries
        if f"results/{query.id}.json" not in present
    }
    entries = entries_of(rebuilt(laptop_package, add=full)) + [regular("results/zzz.json", b"[]")]

    assert "demasiados" in rejected(store, tar_of(entries))


def test_un_resultado_faltante_se_rechaza(store, laptop_package):
    rejected(store, rebuilt(laptop_package, drop=("results/alf.json",)))


@pytest.mark.parametrize("missing", ["meta.json", "SHA256SUMS"])
def test_sin_meta_o_sin_sumas_se_rechaza(store, laptop_package, missing):
    members = members_of(laptop_package)
    del members[missing]

    rejected(store, build(members, fix_sums=False))


def test_un_sha256sums_incorrecto_se_rechaza(store, laptop_package):
    members = members_of(laptop_package)
    members["results/alf.json"] = b'[{"global_state": "1"}]'

    assert "SHA256SUMS" in rejected(store, build(members, fix_sums=False))


def test_un_sha256sums_con_lineas_repetidas_o_mal_formadas_se_rechaza(store, laptop_package):
    sums = members_of(laptop_package)["SHA256SUMS"].decode()

    rejected(
        store,
        rebuilt(laptop_package, replace={"SHA256SUMS": (sums + sums).encode()}, fix_sums=False),
    )
    rejected(store, rebuilt(laptop_package, replace={"SHA256SUMS": b"basura\n"}, fix_sums=False))


def test_un_paquete_de_consultas_distinto_del_empaquetado_se_rechaza(store, laptop_package):
    data = rebuilt(laptop_package, meta={"pack_sha256": "0" * 64})

    assert "paquete de consultas" in rejected(store, data)


def test_una_version_de_paquete_de_consultas_distinta_se_rechaza(store, laptop_package):
    rejected(store, rebuilt(laptop_package, meta={"pack_version": "2"}))


def test_collected_at_sin_zona_horaria_se_rechaza(store, laptop_package):
    rejected(store, rebuilt(laptop_package, meta={"collected_at": "2026-10-29T10:00:00"}))


def test_collected_at_en_el_futuro_se_rechaza(store, laptop_package):
    future = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    assert "futuro" in rejected(store, rebuilt(laptop_package, meta={"collected_at": future}))


def test_meta_con_campos_desconocidos_o_json_roto_se_rechaza(store, laptop_package):
    rejected(store, rebuilt(laptop_package, meta={"inesperado": 1}))
    rejected(store, rebuilt(laptop_package, replace={"meta.json": b"{no es json"}))


def test_meta_con_json_demasiado_profundo_se_rechaza(store, laptop_package):
    deep = b"[" * 50_000 + b"]" * 50_000

    rejected(store, rebuilt(laptop_package, replace={"meta.json": deep}))


def test_un_activo_no_inscrito_se_rechaza(store, laptop_package):
    config = {**CONFIG, "hosts": {"srv-app": CONFIG["hosts"]["srv-app"]}}
    store.write_text("connectors/endpoint.yaml", yaml.safe_dump(config))

    assert "inscrit" in rejected(store, laptop_package)


def test_un_activo_inexistente_en_el_inventario_se_rechaza(store, laptop_package):
    rejected(store, laptop_package, asset="fantasma")


def test_un_activo_de_otro_tipo_se_rechaza_aunque_este_inscrito(store, laptop_package):
    config = {**CONFIG, "hosts": {"app-facturas": CONFIG["hosts"]["lap-ana"]}}
    store.write_text("connectors/endpoint.yaml", yaml.safe_dump(config))

    rejected(store, laptop_package, asset="app-facturas")


def test_un_paquete_de_otro_equipo_que_el_inscrito_para_el_activo_se_rechaza(store, laptop_package):
    assert "no corresponde" in rejected(store, laptop_package, asset="srv-app")


def replace_identity(data: bytes, **changes: str) -> bytes:
    identity = {"hostname": LAPTOP_HOST, "uuid": LAPTOP_UUID, **changes}
    return rebuilt(data, replace={"results/system_info.json": json.dumps([identity]).encode()})


def test_un_uuid_que_no_coincide_con_el_inscrito_se_rechaza(store, laptop_package):
    rejected(
        store,
        replace_identity(laptop_package, uuid="00000000-0000-0000-0000-000000000000"),
    )


def test_un_hostname_que_no_coincide_con_el_inscrito_se_rechaza(store, laptop_package):
    rejected(store, replace_identity(laptop_package, hostname="otro.example"))


def test_una_identidad_ambigua_se_rechaza(store, laptop_package):
    identity = [
        {"hostname": LAPTOP_HOST, "uuid": LAPTOP_UUID},
        {"hostname": "otro", "uuid": "x"},
    ]

    rejected(
        store,
        rebuilt(
            laptop_package, replace={"results/system_info.json": json.dumps(identity).encode()}
        ),
    )


def test_un_resultado_mayor_que_el_limite_se_rechaza(store, laptop_package):
    bomb = b"[" + b" " * (10 * MIB + 1) + b"]"

    rejected(store, rebuilt(laptop_package, replace={"results/apps.json": bomb}))


def test_una_bomba_de_descompresion_por_total_de_bytes_se_rechaza(store, laptop_package):
    zeros = b" " * (10 * MIB)
    names = [n for n in members_of(laptop_package) if n.startswith("results/")]
    data = rebuilt(laptop_package, replace={name: zeros for name in names[:8]})

    assert "tamaño" in rejected(store, data)


def test_un_fichero_que_no_es_un_tar_se_rechaza(store):
    rejected(store, b"esto no es un tar")
    rejected(store, b"")


def test_un_tar_truncado_se_rechaza(store, laptop_package):
    rejected(store, laptop_package[: len(laptop_package) // 2])


def test_un_paquete_demasiado_grande_se_rechaza(store, laptop_package):
    rejected(store, laptop_package + b"\0" * (32 * MIB + 1))


def gz_tar_with_oversized_header(kind: bytes, size: int) -> bytes:
    header = special("././@LongLink", kind, size=size).tobuf(tarfile.GNU_FORMAT)
    padding = b"\0" * (-size % 512)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as stream:
        stream.write(header)
        for _ in range(size // MIB):
            stream.write(b"\0" * MIB)
        stream.write(b"\0" * (size % MIB) + padding + b"\0" * 1024)
    return buffer.getvalue()


@pytest.mark.parametrize("kind", [tarfile.GNUTYPE_LONGNAME, tarfile.XHDTYPE, tarfile.XGLTYPE])
def test_una_cabecera_extendida_gigante_se_rechaza_por_el_tope_descomprimido(store, kind):
    bomb = gz_tar_with_oversized_header(kind, 80 * MIB)

    assert len(bomb) < MAX_PACKAGE_BYTES
    assert "descomprimido" in rejected(store, bomb)


def test_datos_tras_el_gzip_se_rechazan(store, laptop_package):
    rejected(store, laptop_package + gzip.compress(b"extra"))


def test_reimportar_el_mismo_paquete_no_crea_otro_par_de_manifiestos(store, laptop_package):
    import_package(store, laptop_package)
    before = persisted_keys(store)

    assert "ya estaba importado" in rejected_keeping(store, laptop_package)
    assert persisted_keys(store) == before


def test_lo_normalizado_guarda_la_identidad_verificada_del_equipo(store, laptop_package):
    import_package(store, laptop_package)

    package = normalized(store)["data"]["package"]

    assert (package["hardware_uuid"], package["hostname"]) == (LAPTOP_UUID, LAPTOP_HOST)


def test_lo_normalizado_conserva_user_scoped_y_no_el_nombre_de_usuario(store, laptop_package):
    import_package(store, laptop_package)

    rows = normalized(store)["data"]["queries"]["software_update_policy"]["rows"]

    assert rows and all(row["user_scoped"] == "0" and "username" not in row for row in rows)


def test_un_hostname_de_meta_distinto_del_de_system_info_se_rechaza(store, laptop_package):
    data = rebuilt(laptop_package, meta={"hostname": "otro.example"})

    assert "meta.json" in rejected(store, data)


def test_el_nombre_corto_en_meta_frente_al_fqdn_de_system_info_no_se_rechaza(store, laptop_package):
    data = rebuilt(laptop_package, meta={"hostname": "LAP-ANA"})

    import_package(store, data, "lap-ana")

    assert persisted_keys(store) != []


@pytest.mark.parametrize("field", ["uname", "gname", "uid", "gid"])
def test_un_paquete_con_propietario_en_las_cabeceras_se_rechaza(store, laptop_package, field):
    value = 1000 if field in ("uid", "gid") else "ana"
    entries = [(info, body) for info, body in entries_of(laptop_package)]
    setattr(entries[0][0], field, value)

    assert "propietario" in rejected(store, tar_of(entries))
