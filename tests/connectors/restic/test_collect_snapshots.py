"""Sección `snapshots`: solo id, hora y vínculo con una ejecución; nada de rutas ni hostnames."""

import json

from ens_evidence.models.enums import CollectionStatus

from .fake_restic import CANARY, HOSTNAME, PATH_CANARY, SNAPSHOT_ID, snapshot_json
from .record_data import backup_run

OTHER_SNAPSHOT = "9f" * 32


def test_los_snapshots_no_llevan_rutas_ni_hostnames_ni_usuario(collect, restic):
    restic.with_snapshots(snapshot_json())

    text = json.dumps(collect(runs=[backup_run()]).data["sections"]["snapshots"])

    for private in (PATH_CANARY, HOSTNAME, "usuario-canario", "tree"):
        assert private not in text


def test_cada_snapshot_da_id_y_hora_en_utc(collect, restic):
    restic.with_snapshots(snapshot_json(time="2026-10-28T11:00:00.123456+01:00"))

    (item,) = collect().data["sections"]["snapshots"]["items"]

    assert item["id"] == SNAPSHOT_ID
    assert item["time"] == "2026-10-28T10:00:00.123456Z"


def test_un_snapshot_citado_por_una_ejecucion_completada_no_es_ajeno(collect, restic):
    restic.with_snapshots(snapshot_json())

    section = collect(runs=[backup_run("r1")]).data["sections"]["snapshots"]

    (item,) = section["items"]
    assert item["run_id"] == "r1"
    assert item["foreign"] is False
    assert section["summary"]["foreign_snapshots"] == []


def test_un_snapshot_sin_ejecucion_registrada_se_informa_como_ajeno(collect, restic):
    restic.with_snapshots(snapshot_json(), snapshot_json(OTHER_SNAPSHOT))

    section = collect(runs=[backup_run("r1")]).data["sections"]["snapshots"]

    assert section["summary"]["foreign_snapshots"] == [OTHER_SNAPSHOT]
    assert section["summary"]["count"] == 2


def test_una_ejecucion_fallida_o_interrumpida_no_vuelve_propio_un_snapshot(collect, restic):
    restic.with_snapshots(snapshot_json())

    runs = [backup_run("r1", "failed"), backup_run("r2", "started")]
    section = collect(runs=runs).data["sections"]["snapshots"]

    assert section["summary"]["foreign_snapshots"] == [SNAPSHOT_ID]


def test_una_ejecucion_puede_citar_el_snapshot_con_su_id_corto(collect, restic):
    restic.with_snapshots(snapshot_json())

    section = collect(runs=[backup_run("r1", snapshot_id=SNAPSHOT_ID[:8])]).data["sections"][
        "snapshots"
    ]

    assert section["items"][0]["foreign"] is False


def test_un_id_demasiado_corto_no_basta_para_citar_un_snapshot(collect, restic):
    restic.with_snapshots(snapshot_json())

    section = collect(runs=[backup_run("r1", snapshot_id=SNAPSHOT_ID[:4])]).data["sections"][
        "snapshots"
    ]

    assert section["items"][0]["foreign"] is True


def test_los_snapshots_salen_ordenados_por_hora(collect, restic):
    restic.with_snapshots(
        snapshot_json(OTHER_SNAPSHOT, time="2026-10-28T10:00:00Z"),
        snapshot_json(time="2026-10-27T10:00:00Z"),
    )

    items = collect().data["sections"]["snapshots"]["items"]

    assert [item["id"] for item in items] == [SNAPSHOT_ID, OTHER_SNAPSHOT]


def test_un_fallo_de_snapshots_solo_marca_su_seccion_como_error(collect, restic):
    restic.snapshots = (1, CANARY)

    result = collect(runs=[backup_run()])

    assert result.collection_status is CollectionStatus.SUCCESS
    section = result.data["sections"]["snapshots"]
    assert section["status"] == "ERROR"
    assert "1" in section["reason"]
    assert result.data["sections"]["repository"]["status"] == "SUCCESS"
    assert result.data["sections"]["runs"]["status"] == "SUCCESS"


def test_una_respuesta_con_forma_inesperada_es_error_de_la_seccion_sin_eco(collect, restic):
    restic.snapshots = (0, f'[{{"id": "{CANARY}"}}]')

    section = collect().data["sections"]["snapshots"]

    assert section["status"] == "ERROR"
    assert CANARY not in json.dumps(section)


def test_un_json_invalido_es_error_de_la_seccion(collect, restic):
    restic.snapshots = (0, f"no es json {CANARY}")

    section = collect().data["sections"]["snapshots"]

    assert section["status"] == "ERROR"
    assert CANARY not in json.dumps(section)


def test_un_repositorio_sin_snapshots_es_exito_con_cero(collect):
    section = collect().data["sections"]["snapshots"]

    assert section["status"] == "SUCCESS"
    assert section["summary"]["count"] == 0
