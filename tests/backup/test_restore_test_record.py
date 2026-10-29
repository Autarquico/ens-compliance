import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ens_evidence.backup import RestoreRecord, dump, list_restores
from ens_evidence.backup.records import restore_key
from ens_evidence.backup.restore_test import RestoreTest, restore_test_key
from ens_evidence.evidence.manifests import list_manifests

from .conftest import CANARY, REPOSITORY_ID, ens

SNAPSHOT = "c" * 64
NOW = datetime.now(UTC).replace(microsecond=0)
ASSETS = {
    "assets": [
        {
            "id": "APP-001",
            "type": "application",
            "deployment": "deployed",
            "service": "web",
            "domain": "app.example.org",
            "repo": "org/app",
            "database": "DB-001",
        }
    ]
}


def instant(delta: timedelta) -> str:
    return (NOW + delta).isoformat()


def valid_declaration() -> dict:
    return {
        "test_id": "RT-1",
        "system": "APP-001",
        "performed_at": instant(-timedelta(days=2)),
        "restore_id": "RS-OK",
        "isolated_target": True,
        "backup": {"snapshot_id": SNAPSHOT},
        "result": {"restored": True, "integrity_verified": True, "application_started": True},
        "review": {
            "approved": True,
            "reviewer": "Ana Gil",
            "role": "security_officer",
            "reviewed_at": instant(-timedelta(days=1)),
            "valid_until": instant(timedelta(days=90)),
        },
    }


def restore(restore_id="RS-OK", **fields) -> RestoreRecord:
    values = {
        "restore_id": restore_id,
        "status": "completed",
        "started_at": NOW - timedelta(days=3),
        "finished_at": NOW - timedelta(days=3) + timedelta(minutes=5),
        "repository_id": REPOSITORY_ID,
        "target": "/restaurado",
        "snapshot_id": SNAPSHOT,
        "inventory_verified": True,
        "mismatches": 0,
        **fields,
    }
    return RestoreRecord(**values)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """El reloj del comando es `NOW`: las fechas de las declaraciones no dependen de la hora."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr("ens_evidence.cli.datetime", FrozenDatetime)


@pytest.fixture
def box(workspace, scripted_restic):
    workspace.write_text("scope/assets.yaml", yaml.safe_dump(ASSETS))
    workspace.create_bytes(restore_key("RS-OK"), dump(restore()))
    return workspace


def declare(tmp_path: Path, declaration: dict) -> Path:
    path = tmp_path / "declaration.yaml"
    path.write_text(yaml.safe_dump(declaration))
    return path


def record(settings, tmp_path, declaration: dict):
    return ens(settings, "backup", "restore-test", "record", str(declare(tmp_path, declaration)))


def mutated(path: str, value) -> dict:
    declaration = copy.deepcopy(valid_declaration())
    target = declaration
    *parents, leaf = path.split(".")
    for parent in parents:
        target = target[parent]
    if value is ...:
        del target[leaf]
    else:
        target[leaf] = value
    return declaration


def stored(workspace) -> list[str]:
    return workspace.list("evidence/restore-tests")


def test_una_declaracion_completa_se_guarda_una_vez_y_queda_como_evidencia_manual(
    settings, box, tmp_path
):
    result = record(settings, tmp_path, valid_declaration())

    assert result.exit_code == 0, result.output
    assert stored(box) == [restore_test_key("RT-1")]
    saved = RestoreTest.model_validate(yaml.safe_load(box.read_bytes(restore_test_key("RT-1"))))
    assert saved.repository_id == REPOSITORY_ID
    assert saved.review.role == "security_officer"
    listing = list_manifests(box)
    assert listing.errors == []
    (manifest,) = listing.manifests
    assert manifest.asset_id == "APP-001"
    assert manifest.provenance.kind == "manual"
    assert manifest.provenance.submitted_by == "Ana Gil"
    assert manifest.valid_until == NOW + timedelta(days=90)


def test_application_started_not_applicable_con_justificacion_se_acepta(settings, box, tmp_path):
    declaration = mutated("result.application_started", "not_applicable")
    declaration["result"]["application_justification"] = "Es un almacén sin aplicación."

    assert record(settings, tmp_path, declaration).exit_code == 0
    assert stored(box) == [restore_test_key("RT-1")]


def test_un_test_con_resultado_negativo_se_registra_para_que_el_collector_lo_evalue(
    settings, box, tmp_path
):
    declaration = mutated("review.approved", False)

    assert record(settings, tmp_path, declaration).exit_code == 0
    assert stored(box) == [restore_test_key("RT-1")]


REJECTED = {
    "performed_at_sin_hora": mutated("performed_at", "2026-10-15"),
    "performed_at_sin_zona": mutated("performed_at", "2026-10-15T10:00:00"),
    "performed_at_futuro": {
        **mutated("performed_at", instant(timedelta(days=1))),
        "review": {
            **valid_declaration()["review"],
            "reviewed_at": instant(timedelta(days=2)),
            "valid_until": instant(timedelta(days=90)),
        },
    },
    "reviewed_at_sin_zona": mutated("review.reviewed_at", "2026-10-16T10:00:00"),
    "reviewed_at_futuro": mutated("review.reviewed_at", instant(timedelta(hours=1))),
    "reviewed_at_anterior_a_performed_at": mutated(
        "review.reviewed_at", instant(-timedelta(days=3))
    ),
    "valid_until_igual_a_reviewed_at": mutated(
        "review.valid_until", valid_declaration()["review"]["reviewed_at"]
    ),
    "valid_until_anterior_a_reviewed_at": mutated(
        "review.valid_until", instant(-timedelta(days=2))
    ),
    "valid_until_ausente": mutated("review.valid_until", ...),
    "isolated_target_falso": mutated("isolated_target", False),
    "isolated_target_ausente": mutated("isolated_target", ...),
    "isolated_target_como_texto": mutated("isolated_target", "yes"),
    "not_applicable_sin_justificacion": mutated("result.application_started", "not_applicable"),
    "justificacion_sin_not_applicable": {
        **mutated("result.application_started", True),
        "result": {
            **valid_declaration()["result"],
            "application_justification": "no hacía falta",
        },
    },
    "restore_id_inexistente": mutated("restore_id", "RS-NO"),
    "snapshot_de_otra_restauracion": mutated("backup.snapshot_id", "d" * 64),
    "snapshot_abreviado": mutated("backup.snapshot_id", "cccccccc"),
    "restauracion_de_otro_repositorio": mutated("restore_id", "RS-OTHER-REPO"),
    "restauracion_sin_inventario_verificado": mutated("restore_id", "RS-UNVERIFIED"),
    "restauracion_fallida": mutated("restore_id", "RS-FAILED"),
    "restauracion_sin_terminar": mutated("restore_id", "RS-STARTED"),
    "system_fuera_del_inventario": mutated("system", "APP-999"),
    "test_id_con_ruta": mutated("test_id", "../escapa"),
    "test_id_con_espacios": mutated("test_id", "con espacios"),
    "campo_desconocido": {**valid_declaration(), "extra": "no previsto"},
}


@pytest.fixture
def other_restores(box):
    for related in (
        restore("RS-OTHER-REPO", repository_id="9" * 64),
        restore("RS-UNVERIFIED", inventory_verified=False, mismatches=2),
        restore(
            "RS-FAILED",
            status="failed",
            snapshot_id=None,
            inventory_verified=None,
            mismatches=None,
            failure="restore_failed",
        ),
        restore(
            "RS-STARTED",
            status="started",
            finished_at=None,
            snapshot_id=None,
            inventory_verified=None,
            mismatches=None,
        ),
    ):
        box.create_bytes(restore_key(related.restore_id), dump(related))
    assert len(list_restores(box)[0]) == 5


@pytest.mark.parametrize("declaration", REJECTED.values(), ids=REJECTED.keys())
def test_una_declaracion_que_no_cumple_se_rechaza_sin_escribir_nada(
    settings, box, other_restores, tmp_path, declaration
):
    before = {key: box.read_bytes(key) for key in box.list("")}

    result = record(settings, tmp_path, declaration)

    assert result.exit_code != 0
    assert {key: box.read_bytes(key) for key in box.list("")} == before


def test_un_test_id_ya_registrado_se_rechaza_y_conserva_el_original(settings, box, tmp_path):
    assert record(settings, tmp_path, valid_declaration()).exit_code == 0
    original = box.read_bytes(restore_test_key("RT-1"))
    evidences = len(list_manifests(box).manifests)
    repeated = mutated("review.reviewer", "Otra Persona")

    result = record(settings, tmp_path, repeated)

    assert result.exit_code != 0
    assert box.read_bytes(restore_test_key("RT-1")) == original
    assert len(list_manifests(box).manifests) == evidences


@pytest.mark.parametrize(
    "path",
    [
        "performed_at",
        "review.reviewed_at",
        "isolated_target",
        "result.application_started",
        "result.restored",
        "review.role",
        "backup.snapshot_id",
        "system",
        "restore_id",
        "test_id",
    ],
)
def test_los_mensajes_de_error_no_repiten_los_valores_del_yaml(settings, box, tmp_path, path):
    declaration = mutated(path, CANARY)
    declaration["extra"] = CANARY

    result = record(settings, tmp_path, declaration)

    assert result.exit_code != 0
    assert CANARY not in result.output


def test_un_fichero_que_no_es_yaml_no_se_cita_en_el_error(settings, box, tmp_path):
    path = tmp_path / "roto.yaml"
    path.write_text(f"{CANARY}: [sin cerrar")

    result = ens(settings, "backup", "restore-test", "record", str(path))

    assert result.exit_code != 0
    assert CANARY not in result.output
    assert stored(box) == []


def test_un_repositorio_ilegible_impide_registrar(settings, box, scripted_restic, tmp_path):
    scripted_restic.reply("cat", err=CANARY, code=12)

    result = record(settings, tmp_path, valid_declaration())

    assert result.exit_code != 0
    assert stored(box) == []
