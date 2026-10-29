"""La recogida solo ejecuta los dos comandos de la lista blanca, sin lock ni caché."""

import pytest

from ens_evidence.backup.environment import BackupSecrets
from ens_evidence.connectors.restic.process import (
    ALLOWED,
    CAT_CONFIG,
    LIST_SNAPSHOTS,
    ResticProcess,
)
from ens_evidence.models.enums import CollectionStatus

from .fake_restic import PASSWORD, config_json, snapshot_json

FORBIDDEN = ("check", "restore", "forget", "prune", "backup", "init", "unlock", "rebuild-index")


def test_solo_ejecuta_cat_config_y_snapshots_con_no_lock_y_no_cache(collect, restic):
    restic.with_snapshots(snapshot_json())

    collect()

    assert restic.calls == [CAT_CONFIG, LIST_SNAPSHOTS]
    for call in restic.calls:
        assert call[:2] == ("--no-lock", "--no-cache")
        assert not set(call) & set(FORBIDDEN)


def test_la_lista_blanca_son_exactamente_dos_comandos():
    assert ALLOWED == {
        ("--no-lock", "--no-cache", "cat", "config"),
        ("--no-lock", "--no-cache", "snapshots", "--json", "--tag", "ens-evidence"),
    }


def test_si_el_repositorio_no_existe_no_se_listan_snapshots(collect, restic):
    restic.cat = (10, "")

    collect()

    assert restic.calls == [CAT_CONFIG]


@pytest.mark.parametrize("code", [1, 12])
def test_un_fallo_de_cat_config_no_se_reintenta_ni_sigue_con_otros_comandos(collect, restic, code):
    restic.cat = (code, "")

    collect()

    assert restic.calls == [CAT_CONFIG]


@pytest.mark.parametrize(
    "args",
    [
        ("check",),
        ("--no-lock", "--no-cache", "check"),
        ("--no-lock", "--no-cache", "restore", "latest", "--target", "/tmp/x"),
        ("--no-lock", "--no-cache", "forget", "--prune"),
        ("--no-lock", "--no-cache", "prune"),
        ("cat", "config"),
        ("--no-cache", "cat", "config"),
        ("--no-lock", "cat", "config"),
        ("--no-lock", "--no-cache", "snapshots", "--json"),
        (*CAT_CONFIG, "--option", "x"),
    ],
)
def test_el_proceso_rechaza_cualquier_comando_fuera_de_la_lista_blanca(args):
    process = ResticProcess("/repo", BackupSecrets(PASSWORD), base_env={"PATH": "/nonexistent"})

    with pytest.raises(ValueError, match="no permitido"):
        process(args)


def test_un_cat_config_sin_id_de_64_hex_es_un_error_global_sin_eco_de_lo_recibido(collect, restic):
    restic.cat = (0, config_json("no-es-un-id"))

    result = collect()

    assert result.collection_status is CollectionStatus.ERROR
    assert "no-es-un-id" not in result.data["error"]
