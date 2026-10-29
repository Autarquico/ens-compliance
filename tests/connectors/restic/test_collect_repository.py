"""Sección `repository`: existe, no existe, contraseña incorrecta, sin credenciales, backend."""

import pytest

from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.connectors.restic.process import ResticUnavailable
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace.settings import BackupSettings

from .fake_restic import NOW, PASSWORD, REPOSITORY, REPOSITORY_ID, FakeRestic
from .record_data import backup_run, restic_run


def test_un_repositorio_accesible_da_su_id_y_existe(collect):
    result = collect()

    assert result.collection_status is CollectionStatus.SUCCESS
    assert result.data["repository_id"] == REPOSITORY_ID
    (item,) = result.data["sections"]["repository"]["items"]
    assert item == {"exists": True, "repository_id": REPOSITORY_ID, "backend": "remote"}


def test_un_repositorio_inexistente_es_exito_con_exists_false(collect, restic):
    restic.cat = (10, "")

    result = collect()

    assert result.collection_status is CollectionStatus.SUCCESS
    assert result.data["error"] is None
    (item,) = result.data["sections"]["repository"]["items"]
    assert item["exists"] is False
    assert item["repository_id"] is None
    assert result.data["sections"]["snapshots"]["summary"]["repository_exists"] is False


def test_con_repositorio_inexistente_ningun_registro_queda_atado(collect, restic):
    restic.cat = (10, "")

    section = collect(runs=[backup_run()]).data["sections"]["runs"]

    assert [item["bound"] for item in section["items"]] == [False]


def test_contrasena_incorrecta_es_un_error_global_sin_secciones(collect, restic):
    restic.cat = (12, "")

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "12" in result.data["error"]
    assert result.data["sections"] == {}


@pytest.mark.parametrize("code", [1, 2, 3, 11, 130])
def test_cualquier_otro_codigo_de_cat_config_es_un_error_global(collect, restic, code):
    restic.cat = (code, "")

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert str(code) in result.data["error"]
    assert result.data["sections"] == {}


@pytest.mark.parametrize("kind", ["not_installed", "os_error", "timeout", "not_text"])
def test_restic_no_ejecutable_es_un_error_global_con_el_tipo_de_fallo(kind):
    def unavailable(args):
        raise ResticUnavailable(kind)

    connector = ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY), executor=unavailable
    )

    result = connector.collect(restic_run())

    assert result.collection_status is CollectionStatus.ERROR
    assert kind in result.data["error"]


def test_sin_contrasena_en_el_entorno_es_un_error_global_y_no_ejecuta_restic():
    connector = ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY), clock=lambda: NOW, env={}
    )

    result = connector.collect(restic_run())

    assert result.collection_status is CollectionStatus.ERROR
    assert "ENS_BACKUP_RESTIC_PASSWORD" in result.data["error"]
    assert result.data["sections"] == {}


def test_sin_repositorio_en_los_ajustes_es_un_error_global():
    connector = ResticConnector(backup=None, executor=FakeRestic(), clock=lambda: NOW)

    result = connector.collect(restic_run())

    assert result.collection_status is CollectionStatus.ERROR
    assert "backup.restic_repository" in result.data["error"]


def test_un_repositorio_s3_sin_claves_de_acceso_es_un_error_global():
    connector = ResticConnector(
        backup=BackupSettings(restic_repository=REPOSITORY),
        clock=lambda: NOW,
        env={"ENS_BACKUP_RESTIC_PASSWORD": PASSWORD, "PATH": "/nonexistent"},
    )

    result = connector.collect(restic_run())

    assert result.collection_status is CollectionStatus.ERROR
    assert PASSWORD not in result.data["error"]


@pytest.mark.parametrize(
    ("repository", "backend"),
    [
        ("/repos/copias", "local"),
        ("relativo/copias", "local"),
        ("local:/repos/copias", "local"),
        ("C:\\repos\\copias", "local"),
        ("s3:s3.example.com/copias", "remote"),
        ("b2:bucket:ruta", "remote"),
        ("azure:contenedor:/ruta", "remote"),
        ("gs:bucket:/ruta", "remote"),
        ("rest:https://servidor.example.com/", "remote"),
        ("sftp:usuario@servidor.example.com:/copias", "remote"),
        ("rclone:destino:ruta", "remote"),
        ("desconocido:algo", "unknown"),
    ],
)
def test_el_backend_se_clasifica_por_el_esquema_del_repositorio(repository, backend):
    connector = ResticConnector(
        backup=BackupSettings(restic_repository=repository),
        executor=FakeRestic(),
        clock=lambda: NOW,
    )

    result = connector.collect(restic_run())

    (item,) = result.data["sections"]["repository"]["items"]
    assert item["backend"] == backend


def test_la_cadena_del_repositorio_no_se_guarda_en_la_evidencia(collect):
    assert REPOSITORY not in collect().model_dump_json()
