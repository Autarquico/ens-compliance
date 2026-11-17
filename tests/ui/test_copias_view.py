"""Vista de copias: los datos que entrega, sin depender de la plantilla."""

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from ens_evidence.backup.records import BackupRun, RestoreRecord, dump
from ens_evidence.backup.restore_test import RestoreTest
from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.store import record_run
from ens_evidence.connectors.backups import checks as backups_checks
from ens_evidence.evidence.importer import import_collected
from ens_evidence.models.contracts import CheckResult, CollectionResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import copias
from ens_evidence.workspace import LocalStore

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA = "a" * 64
RID = "b" * 64
SNAP = "c" * 64
RECENT = "backups.backup_001_recent"
RESTORE_TEST = "backups.backup_005_restore_test"
CONFIG = {
    "repositories": [
        {
            "id": "caja",
            "asset_id": "bk-diario",
            "repository": "s3:https://s3.example.test/b",
            "max_backup_age_hours": 24,
            "max_check_age_days": 7,
            "restore_test_validity_days": 90,
        }
    ]
}


@pytest.fixture
def local(tmp_path):
    return LocalStore(tmp_path)


def page(local, host="copias.example.org"):
    return copias.build_page(UiStore(local), host, NOW)


def write_config(local, data=None):
    local.write_text("connectors/backups.yaml", yaml.safe_dump(data or CONFIG))


def write_assets(local, frequency="Diaria"):
    assets = {
        "assets": [
            {
                "id": "bk-diario",
                "type": "backup",
                "deployment": "deployed",
                "frequency": frequency,
                "source": "datos",
                "destination": "bucket",
                "restoration": "trimestral",
            }
        ]
    }
    local.write_text("scope/assets.yaml", yaml.safe_dump(assets))


def evidence(local, items=None, repo_id="caja", asset_id="bk-diario", repository_id=RID):
    snapshots = [{"id": SNAP, "time": (NOW - timedelta(hours=2)).isoformat(), "foreign": True}]
    entry = {
        "id": repo_id,
        "asset_id": asset_id,
        "collection_status": "SUCCESS",
        "repository_id": repository_id,
        "sections": {
            "snapshots": {"status": "SUCCESS", "items": snapshots if items is None else items}
        },
    }
    return import_collected(
        local,
        CollectionResult(
            source="backups",
            collector="backups",
            asset_id="backups",
            collected_at=NOW,
            collection_status="SUCCESS",
            data={"repositories": [entry]},
        ),
        collector_version="1",
        now=NOW,
    )


def run(local, results, complete=True):
    record_run(
        local,
        StoredCheckRun(
            seq=1,
            connector="backups",
            engine_version="1",
            started_at=NOW - timedelta(minutes=5),
            finished_at=NOW - timedelta(minutes=1),
            complete=complete,
            scope_fingerprint=SHA,
            config_sha256=SHA,
            results=results,
        ),
        NOW,
    )


def check(manifest, status=CheckStatus.PASS, check_id=RECENT, reason="ok"):
    return CheckResult(
        check_id=check_id,
        asset_id="bk-diario",
        status=status,
        evaluated_at=NOW,
        evidence_ids=[manifest.id] if manifest else [],
        reason=reason,
        subject=RID,
    )


def declaration(
    test_id="RT-1",
    repository_id=RID,
    restore_id="R-1",
    snapshot=SNAP,
    reviewer="Ana",
    valid_until="2027-02-01T11:00:00+00:00",
    approved=True,
):
    return RestoreTest.model_validate(
        {
            "test_id": test_id,
            "system": "bk-diario",
            "performed_at": "2026-11-01T10:00:00+00:00",
            "restore_id": restore_id,
            "isolated_target": True,
            "backup": {"snapshot_id": snapshot},
            "result": {
                "restored": True,
                "integrity_verified": True,
                "application_started": True,
            },
            "review": {
                "approved": approved,
                "reviewer": reviewer,
                "role": "security_officer",
                "reviewed_at": "2026-11-01T11:00:00+00:00",
                "valid_until": valid_until,
            },
            "repository_id": repository_id,
        }
    )


def write_test(local, test):
    local.write_bytes(f"evidence/restore-tests/{test.test_id}.yaml", dump(test))


def write_restore(local, restore_id="R-1", repository_id=RID, snapshot=SNAP, **fields):
    values = {
        "restore_id": restore_id,
        "status": "completed",
        "started_at": NOW - timedelta(days=7),
        "finished_at": NOW - timedelta(days=7),
        "repository_id": repository_id,
        "target": "/data/x",
        "snapshot_id": snapshot,
        "inventory_verified": True,
        "mismatches": 0,
    } | fields
    local.write_bytes(f"evidence/backups/restores/{restore_id}.yaml", dump(RestoreRecord(**values)))


def configured_with_snapshot(local):
    write_config(local)
    write_assets(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    return manifest


# --- C1: último snapshot -------------------------------------------------------------------------


def test_el_ultimo_snapshot_sale_del_artefacto_citado(local):
    configured_with_snapshot(local)
    block = page(local).repositories[0]
    assert block.last_snapshot == copias.LastSnapshot("2026-11-08 10:00 UTC", 2)
    assert block.checked_at == "2026-11-08 11:59 UTC"
    assert block.problem is False
    assert block.frequency == "Diaria"
    assert block.asset_href == "/inventario/activos/bk-diario"


def test_sin_artefacto_solo_queda_la_fecha_del_check(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    local.write_bytes(manifest.artifact.key, b"{}")
    block = page(local).repositories[0]
    assert block.last_snapshot is None
    assert block.checked_at is not None


def mutate(local, manifest, new_bytes):
    local.write_bytes(manifest.artifact.key, new_bytes)


def test_hash_incorrecto_cae_al_check(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    original = local.read_bytes(manifest.artifact.key)
    mutate(local, manifest, original.replace(b"SUCCESS", b"SUCCESz", 1))
    assert page(local).repositories[0].last_snapshot is None


def test_json_malformado_cae_al_check(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    mutate(local, manifest, b"{no es json")
    assert page(local).repositories[0].last_snapshot is None


def test_tamano_excedido_cae_al_check(local, monkeypatch):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    monkeypatch.setattr(copias, "MAX_ARTIFACT_BYTES", 10)
    assert page(local).repositories[0].last_snapshot is None


def test_artefacto_excedido_se_lee_acotado(local, monkeypatch):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    size = len(local.read_bytes(manifest.artifact.key))
    monkeypatch.setattr(copias, "MAX_ARTIFACT_BYTES", size - 1)
    asked = []
    original = UiStore.read_bytes

    def spy(self, key, limit=None):
        data = original(self, key, limit)
        asked.append((key, limit, len(data)))
        return data

    monkeypatch.setattr(UiStore, "read_bytes", spy)
    assert page(local).repositories[0].last_snapshot is None
    mine = [call for call in asked if call[0] == manifest.artifact.key]
    assert mine == [(manifest.artifact.key, size, size)]
    assert all(read <= copias.MAX_ARTIFACT_BYTES + 1 for _, _, read in mine)


def test_repositorio_ajeno_en_el_artefacto_cae_al_check(local):
    write_config(local)
    manifest = evidence(local, repo_id="otro", asset_id="otro-activo")
    run(local, [check(manifest)])
    assert page(local).repositories[0].last_snapshot is None


def test_clave_ajena_en_el_manifiesto_cae_al_check(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    key = f"evidence/manifests/{manifest.id}.yaml"
    text = local.read_text(key).replace(manifest.artifact.key, "settings/secreto")
    local.write_text(key, text)
    assert page(local).repositories[0].last_snapshot is None


def test_manifiesto_de_otra_fuente_no_se_lee(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    key = f"evidence/manifests/{manifest.id}.yaml"
    local.write_text(key, local.read_text(key).replace("source: backups", "source: restic"))
    assert page(local).repositories[0].last_snapshot is None


def test_un_snapshot_futuro_no_cuenta(local):
    write_config(local)
    manifest = evidence(local, items=[{"id": SNAP, "time": (NOW + timedelta(days=1)).isoformat()}])
    run(local, [check(manifest)])
    assert page(local).repositories[0].last_snapshot is None


@pytest.mark.parametrize(
    "entry",
    [
        {"repo_id": "caja", "asset_id": "otro-activo"},
        {"repo_id": "otro", "asset_id": "bk-diario"},
        {"repository_id": "d" * 64},
    ],
    ids=["solo_id", "solo_asset_id", "otro_repository_id"],
)
def test_el_artefacto_exige_id_activo_y_repositorio_a_la_vez(local, entry):
    write_config(local)
    manifest = evidence(local, **entry)
    run(local, [check(manifest)])
    assert page(local).repositories[0].last_snapshot is None


def structure_evidence(local, data):
    return import_collected(
        local,
        CollectionResult(
            source="backups",
            collector="backups",
            asset_id="backups",
            collected_at=NOW,
            collection_status="SUCCESS",
            data=data,
        ),
        collector_version="1",
        now=NOW,
    )


@pytest.mark.parametrize(
    "data",
    [
        {"repositories": 5},
        {"repositories": [{"id": "caja", "asset_id": "bk-diario", "repository_id": RID}]},
        {
            "repositories": [
                {
                    "id": "caja",
                    "asset_id": "bk-diario",
                    "repository_id": RID,
                    "sections": {"snapshots": {"items": "no"}},
                }
            ]
        },
        {},
    ],
    ids=["no_lista", "sin_secciones", "items_no_lista", "sin_repositorios"],
)
def test_estructura_malformada_con_hash_valido_cae_al_check(local, data):
    write_config(local)
    manifest = structure_evidence(local, data)
    run(local, [check(manifest)])
    block = page(local).repositories[0]
    assert block.last_snapshot is None
    assert block.checked_at is not None


# --- C6: ausencia --------------------------------------------------------------------------------


def test_configuracion_sin_ejecuciones_no_da_nada_favorable(local):
    write_config(local)
    block = page(local).repositories[0]
    assert (block.evidence, block.results, block.last_snapshot, block.checked_at) == (
        None,
        (),
        None,
        None,
    )
    assert block.problem is False


def test_backup_001_fail_sin_snapshots(local):
    write_config(local)
    manifest = evidence(local, items=[])
    run(local, [check(manifest, CheckStatus.FAIL, reason="RAZON-SENSIBLE")])
    block = page(local).repositories[0]
    assert block.problem is True
    assert block.last_snapshot is None
    assert block.checked_at is not None
    assert block.evidence.favorable is False
    assert "RAZON-SENSIBLE" not in repr(block)


# --- C2/C3: pruebas de restauración --------------------------------------------------------------


def test_prueba_atribuida_por_el_sujeto_del_check(local):
    configured_with_snapshot(local)
    write_test(local, declaration())
    write_restore(local)
    p = page(local)
    declared = p.repositories[0].restore_test
    assert (declared.test_id, declared.reviewer, declared.valid_until) == (
        "RT-1",
        "Ana",
        "2027-02-01 11:00 UTC",
    )
    assert not hasattr(declared, "current")
    assert [(h.repository_label, h.matched) for h in p.history] == [("caja", "sí")]


def test_sin_resultados_las_pruebas_registradas_no_se_atribuyen_ni_se_niegan(local):
    write_config(local)
    write_test(local, declaration())
    block = page(local).repositories[0]
    assert block.restore_test is None
    assert block.tests_unattributable is True


def test_sin_pruebas_registradas_no_hay_nada_que_atribuir(local):
    write_config(local)
    assert page(local).repositories[0].tests_unattributable is False


def test_con_sujetos_y_una_prueba_ajena_consta_que_no_hay_prueba_propia(local):
    configured_with_snapshot(local)
    write_test(local, declaration(repository_id="d" * 64))
    assert page(local).repositories[0].tests_unattributable is False


def test_historial_ilegible_no_permite_negar_las_pruebas(local):
    configured_with_snapshot(local)
    write_test(local, declaration(repository_id="d" * 64))
    local.write_text("evidence/checks/backups/000009-roto.json", "{no es json")
    p = page(local)
    assert p.history_notes
    assert p.repositories[0].tests_unattributable is True


def test_la_atribucion_funciona_con_los_sujetos_de_los_checks_reales(local):
    write_config(local)
    manifest = evidence(local)
    result = CollectionResult(
        source="backups",
        collector="backups",
        asset_id="backups",
        collected_at=NOW,
        collection_status="SUCCESS",
        data={
            "repositories": [
                json.loads(local.read_bytes(manifest.artifact.key))["data"]["repositories"][0]
            ]
        },
    )
    real = [r for c in backups_checks.ALL_CHECKS for r in c(result, manifest.id, NOW)]
    assert {r.check_id for r in real} >= {RECENT, RESTORE_TEST}
    run(local, real)
    write_test(local, declaration(test_id="RT-9"))
    block = page(local).repositories[0]
    assert block.restore_test is not None and block.restore_test.test_id == "RT-9"
    assert block.tests_unattributable is False
    assert block.last_snapshot is not None


def test_prueba_ajena_va_al_historial_sin_atribuir(local):
    configured_with_snapshot(local)
    write_test(local, declaration(repository_id="d" * 64))
    p = page(local)
    assert p.repositories[0].restore_test is None
    assert p.history[0].repository_label == "sin atribuir (`dddddddd`)"


def test_sin_registro_citado_es_sin_verificar(local):
    configured_with_snapshot(local)
    write_test(local, declaration())
    assert page(local).history[0].matched == "sin verificar"


def test_registro_con_otro_snapshot_o_repositorio_es_sin_verificar(local):
    configured_with_snapshot(local)
    write_test(local, declaration(restore_id="R-1"))
    write_test(local, declaration(test_id="RT-2", restore_id="R-2"))
    write_restore(local, "R-1", snapshot="e" * 64)
    write_restore(local, "R-2", repository_id="f" * 64)
    assert {h.matched for h in page(local).history} == {"sin verificar"}


def test_restauracion_no_verificada_es_no(local):
    configured_with_snapshot(local)
    write_test(local, declaration())
    write_restore(local, inventory_verified=False, mismatches=2)
    assert page(local).history[0].matched == "no"


def test_registro_no_completado_es_sin_verificar(local):
    configured_with_snapshot(local)
    write_test(local, declaration())
    write_restore(
        local,
        status="started",
        finished_at=None,
        snapshot_id=None,
        inventory_verified=None,
        mismatches=None,
    )
    assert page(local).history[0].matched == "sin verificar"


def test_prueba_ilegible_se_cuenta_sin_mostrar_su_contenido(local):
    configured_with_snapshot(local)
    local.write_text("evidence/restore-tests/RT-X.yaml", "test_id: [SECRETO")
    p = page(local)
    assert p.unreadable_tests == 1
    assert "SECRETO" not in repr(p)


def test_el_historial_va_de_la_mas_reciente_a_la_mas_antigua(local):
    configured_with_snapshot(local)
    new = declaration("RT-NEW")
    old = declaration("RT-OLD").model_copy(
        update={"performed_at": datetime(2026, 10, 20, tzinfo=UTC)}
    )
    write_test(local, old)
    write_test(local, new)
    assert [h.test_id for h in page(local).history] == ["RT-NEW", "RT-OLD"]


# --- C4: configuración ---------------------------------------------------------------------------


def test_sin_configuracion(local):
    p = page(local)
    assert (p.configured, p.config_error, p.repositories) == (False, None, ())
    assert p.wizard_href == "/herramientas/copias"


@pytest.mark.parametrize(
    "text",
    [
        "repositories: [SECRETO-VALOR",
        "SECRETO-CLAVE: s3:https://u:p@s3.example.test/b\n",
        "repositories:\n- {id: caja, SECRETO-CLAVE: 'https://u:p@h/x'}\n",
        "AWS_SECRET_ACCESS_KEY: SECRETO-VALOR\n",
    ],
)
def test_configuracion_invalida_usa_un_mensaje_fijo(local, text):
    local.write_text("connectors/backups.yaml", text)
    p = page(local)
    assert (p.configured, p.config_error, p.repositories) == (True, copias.CONFIG_INVALID, ())
    assert "SECRETO" not in repr(p)
    assert "u:p@" not in repr(p)


# --- C8: guía ------------------------------------------------------------------------------------


def test_la_guia_lleva_el_repositorio_y_el_activo_sin_rellenar_el_revisor(local):
    configured_with_snapshot(local)
    guide = page(local).repositories[0].guide
    assert "--repository caja" in guide.command
    declared = yaml.safe_load(guide.yaml)
    assert declared["system"] == "bk-diario"
    assert declared["review"]["reviewer"].startswith("<")
    assert declared["backup"]["snapshot_id"] == "<64 caracteres>"


@pytest.mark.parametrize("hostile", ["a\nb: 1", "x'\"y", "<script>alert(1)</script>", "k: v #c"])
def test_la_guia_con_valores_hostiles_sigue_siendo_yaml_de_un_solo_campo(hostile):
    guide = copias._guide("caja", hostile)
    assert yaml.safe_load(guide.yaml)["system"] == hostile
    assert (
        yaml.safe_load(guide.yaml).keys() == yaml.safe_load(copias._guide("caja", "ok").yaml).keys()
    )


# --- caja y enlace a Backrest --------------------------------------------------------------------


def test_copia_propia_interrumpida_y_sin_texto_de_error(local):
    for run_id, status, extra in (
        ("r1", "failed", {"finished_at": NOW, "error": "SECRETO-DEL-ERROR"}),
        ("r2", "started", {}),
    ):
        run_ = BackupRun(
            run_id=run_id,
            status=status,
            started_at=NOW - timedelta(hours=1 if run_id == "r2" else 2),
            repository="s3:https://u:p@s3.example.test/b",
            **extra,
        )
        local.write_bytes(f"evidence/backups/{run_id}.yaml", dump(run_))
    box = page(local).box
    assert [r.run_id for r in box.interrupted] == ["r2"]
    assert box.last.run_id == "r2"
    assert box.last.status_label == "Iniciada, sin terminar"
    assert "SECRETO-DEL-ERROR" not in repr(box)
    assert "u:p@" not in repr(box)


def test_sin_runs_la_caja_no_tiene_ultima(local):
    box = page(local).box
    assert (box.last, box.interrupted, box.unreadable_count) == (None, (), 0)


@pytest.mark.parametrize(
    ("host", "href"), [("copias.example.org", "https://copias.example.org"), (None, None)]
)
def test_enlace_a_backrest_solo_sale_de_la_configuracion(local, host, href):
    assert page(local, host).backrest_href == href


def test_los_dto_son_inmutables_y_serializables(local):
    configured_with_snapshot(local)
    p = page(local)
    assert dataclasses.is_dataclass(p)
    json.dumps(dataclasses.asdict(p), default=str)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.configured = False  # type: ignore[misc]


# --- K6, C1 y C8 en el contexto ------------------------------------------------------------------


def test_una_ejecucion_incompleta_da_cobertura_parcial_y_nada_favorable(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)], complete=False)
    summary = page(local).repositories[0].evidence
    assert (summary.coverage, summary.favorable) == ("parcial", False)
    assert summary.partial_reasons


def test_un_artefacto_citado_solo_por_otro_check_no_da_ultimo_snapshot(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest, check_id="backups.backup_003_repository_accessible")])
    assert page(local).repositories[0].last_snapshot is None


def test_un_artefacto_de_otro_almacenamiento_no_se_lee(local):
    write_config(local)
    manifest = evidence(local)
    run(local, [check(manifest)])
    key = f"evidence/manifests/{manifest.id}.yaml"
    local.write_text(key, local.read_text(key).replace("storage: workspace", "storage: s3"))
    assert page(local).repositories[0].last_snapshot is None


@pytest.mark.parametrize("hostile", ["<script>alert(1)</script>", "a\nb: 1", "x'\"y"])
def test_un_activo_hostil_no_rompe_la_pagina_ni_la_guia(local, hostile):
    config = {"repositories": [{**CONFIG["repositories"][0], "asset_id": hostile}]}
    write_config(local, config)
    block = page(local).repositories[0]
    assert block.asset_href is None
    assert yaml.safe_load(block.guide.yaml)["system"] == hostile


def test_un_run_id_o_snapshot_id_hostil_de_la_caja_cuenta_como_registro_ilegible(local):
    hostile = "<script>alert(1)</script>"
    run_ = BackupRun.model_construct(
        run_id=hostile,
        status="completed",
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        repository="s3:x",
        snapshot_id=hostile,
        error=None,
    )
    local.write_bytes("evidence/backups/h.yaml", dump(run_))
    box = page(local).box
    assert (box.last, box.unreadable_count) == (None, 1)
    assert hostile not in repr(box)
