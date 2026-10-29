"""Estado de cada consulta en la evidencia normalizada (ADR 0002)."""

from typing import Any

import pytest

from .support import SERVER_HOST, SERVER_UUID, Answer, collect, import_package, normalized


def queries_after_import(store, tmp_path, answers=None, **platform) -> dict[str, Any]:
    asset = "srv-app" if platform else "lap-ana"
    run = collect(tmp_path / "run", answers=answers, **platform)
    import_package(store, run.data, asset)
    return normalized(store)["data"]["queries"]


def test_una_consulta_vacia_con_codigo_de_salida_distinto_de_cero_es_error(store, tmp_path):
    queries = queries_after_import(store, tmp_path, {"os_version": Answer(out=b"", code=3)})

    assert queries["os_version"]["status"] == "ERROR"
    assert queries["os_version"]["exit_code"] == 3
    assert queries["os_version"]["rows"] == []


def test_un_json_ilegible_es_error(store, tmp_path):
    queries = queries_after_import(store, tmp_path, {"os_version": Answer(out=b"{no es json")})

    assert queries["os_version"]["status"] == "ERROR"


def test_una_salida_vacia_con_codigo_cero_es_error_y_no_exito(store, tmp_path):
    queries = queries_after_import(store, tmp_path, {"os_version": Answer(out=b"")})

    assert queries["os_version"]["status"] == "ERROR"


def test_un_json_que_no_es_una_lista_de_filas_de_cadenas_es_error(store, tmp_path):
    queries = queries_after_import(
        store,
        tmp_path,
        {"os_version": Answer(out=b'{"a": "b"}'), "osquery_info": Answer(out=b'[{"version": 5}]')},
    )

    assert queries["os_version"]["status"] == "ERROR"
    assert queries["osquery_info"]["status"] == "ERROR"


def test_un_json_demasiado_profundo_en_una_consulta_es_error_de_esa_consulta(store, tmp_path):
    deep = b"[" * 50_000 + b"]" * 50_000

    queries = queries_after_import(store, tmp_path, {"apps": Answer(out=deep)})

    assert queries["apps"]["status"] == "ERROR"
    assert queries["alf"]["status"] == "SUCCESS"


def test_una_consulta_sin_filas_es_exito_con_cero_filas_no_error(store, tmp_path):
    queries = queries_after_import(store, tmp_path, {"disk_encryption": Answer(out=b"[]")})

    assert queries["disk_encryption"]["status"] == "SUCCESS"
    assert queries["disk_encryption"]["row_count"] == 0


def test_un_permiso_denegado_se_conserva_como_permission_denied(store, tmp_path):
    answers = {"alf": Answer(out=b"", code=1, err="Permission denied")}

    queries = queries_after_import(store, tmp_path, answers)

    assert queries["alf"]["status"] == "PERMISSION_DENIED"
    assert queries["alf"]["permission_hint"] is True


def test_una_consulta_de_otra_plataforma_es_not_supported(store, tmp_path):
    queries = queries_after_import(
        store, tmp_path, platform="linux", uuid=SERVER_UUID, hostname=SERVER_HOST
    )

    for query_id in ("alf", "apps", "filevault_status", "software_update_policy"):
        assert queries[query_id]["status"] == "NOT_SUPPORTED", query_id
    assert queries["deb_packages"]["status"] == "SUCCESS"


def test_una_consulta_darwin_solo_no_existe_en_linux_y_las_de_linux_no_existen_en_darwin(
    store, tmp_path
):
    queries = queries_after_import(store, tmp_path)

    assert queries["deb_packages"]["status"] == "NOT_SUPPORTED"
    assert queries["rpm_packages"]["status"] == "NOT_SUPPORTED"


@pytest.mark.parametrize("query_id", ["apps", "disk_encryption"])
def test_las_filas_conservan_solo_las_columnas_necesarias(store, tmp_path, query_id):
    queries = queries_after_import(store, tmp_path)

    for row in queries[query_id]["rows"]:
        assert "path" not in row


def test_la_identidad_del_equipo_no_se_guarda_en_la_evidencia(store, tmp_path):
    queries = queries_after_import(store, tmp_path)

    assert queries["system_info"]["rows"] == []
    assert queries["system_info"]["status"] == "SUCCESS"


def test_el_estado_global_no_oculta_un_fallo_de_consulta(store, tmp_path):
    answers = {query: Answer(out=b"", code=1) for query in ("os_version", "alf", "apps")}

    queries = queries_after_import(store, tmp_path, answers)

    assert {queries[q]["status"] for q in answers} == {"ERROR"}
    assert queries["osquery_info"]["status"] == "SUCCESS"
