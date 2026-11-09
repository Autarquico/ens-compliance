"""Escenarios de docs/specs/kit-backups.md: del repositorio leído a lo que dice la matriz.

Solo se simula el proceso de restic; conector, checks y matriz son los reales.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from connectors.restic.fake_restic import NOW, FakeRestic, snapshot_json
from matrix.source_helpers import AT as MATRIX_AT
from matrix.source_helpers import ROW, check_link, check_result, run

from ens_evidence.connectors.backups import BackupsConfig, BackupsConnector, BackupsRun
from ens_evidence.connectors.backups import checks as backups_checks
from ens_evidence.connectors.config import ConnectorConfigError, load_connector_config
from ens_evidence.matrix.technical import technical_support
from ens_evidence.models.enums import CheckStatus
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.workspace import LocalStore

RUNBOOKS = Path(__file__).resolve().parents[2] / "docs" / "runbooks"
RECENT, RESTORE = backups_checks.RECENT, backups_checks.RESTORE_TEST
LIMIT_HOURS = 26
REPO = {
    "id": "caja",
    "asset_id": "bk-caja",
    "repository": "s3:s3.example.com/copias",
    "max_backup_age_hours": LIMIT_HOURS,
    "max_check_age_days": 7,
    "restore_test_validity_days": 90,
}


def snapshot_hours_ago(hours: float) -> dict:
    taken = NOW - timedelta(hours=hours)
    return {**snapshot_json(time=taken.isoformat()), "tags": ["plan:diario"]}


def checks_of(*snapshots: dict) -> dict[str, list]:
    fake = FakeRestic().with_snapshots(*snapshots)
    result = BackupsConnector(executor=lambda _: fake, clock=lambda: NOW).collect(
        BackupsRun(repositories=[REPO])
    )
    outcomes = [r for check in backups_checks.ALL_CHECKS for r in check(result, "ev-1", NOW)]
    by_id: dict[str, list] = {}
    for outcome in outcomes:
        by_id.setdefault(outcome.check_id, []).append(outcome)
    return by_id


def mp_info_6_support(tmp_path, outcomes, check_id):
    """Lo que la matriz saca de esos resultados al evaluar una medida enlazada al check."""
    store = LocalStore(tmp_path / "ws")
    results = [
        check_result(store, o.asset_id, check_id=o.check_id, status=o.status) for o in outcomes
    ]
    runs = [run(results, connector="backups", expected=("bk-caja",))]
    return technical_support(store, ROW, check_link(check_id), runs, MATRIX_AT)


# --- instalar no es cumplir -----------------------------------------------------------------


def test_backrest_sin_ninguna_copia_no_da_evidencia_vigente_y_el_check_dice_que_falta(tmp_path):
    by_id = checks_of()  # el repositorio existe (Backrest lo inicializó) pero está vacío

    (recent,) = by_id[RECENT]
    assert recent.status is CheckStatus.FAIL
    assert "ningún snapshot" in recent.reason
    support = mp_info_6_support(tmp_path, by_id[RECENT], RECENT)
    assert not support.counts and not support.evidence
    assert [b.kind for b in support.blockers] == [BlockerKind.NOT_COMPLIANT]


def test_con_copia_pero_sin_prueba_de_restauracion_la_medida_sigue_sin_evidencia(tmp_path):
    by_id = checks_of(snapshot_hours_ago(2))

    (recent,) = by_id[RECENT]
    (restore,) = by_id[RESTORE]
    assert recent.status is CheckStatus.PASS
    assert restore.status is not CheckStatus.PASS
    assert "ningún restore test" in restore.reason
    support = mp_info_6_support(tmp_path, by_id[RESTORE], RESTORE)
    assert not support.counts and not support.evidence


# --- una copia que deja de hacerse se ve -----------------------------------------------------


@pytest.mark.parametrize(
    "hours, status",
    [(LIMIT_HOURS - 1, CheckStatus.PASS), (LIMIT_HOURS + 1, CheckStatus.FAIL)],
)
def test_la_ultima_copia_mas_antigua_que_la_periodicidad_da_fail(hours, status):
    (recent,) = checks_of(snapshot_hours_ago(hours))[RECENT]

    assert recent.status is status
    assert recent.asset_id == "bk-caja"


def test_la_copia_vieja_aparece_como_hallazgo_en_la_matriz(tmp_path):
    by_id = checks_of(snapshot_hours_ago(LIMIT_HOURS + 30))

    support = mp_info_6_support(tmp_path, by_id[RECENT], RECENT)

    assert not support.counts
    assert [(b.kind, b.detail) for b in support.blockers] == [
        (BlockerKind.NOT_COMPLIANT, f"check_failed:{RECENT}:bk-caja")
    ]


# --- los secretos no se quedan en la app (credenciales en la configuración) ---------------------


# Credenciales falsas partidas para que el escáner de secretos no las tome por reales.
FAKE_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_SECRET_KEY = "abcdEFGH1234" * 3 + "abcd"


@pytest.mark.parametrize(
    "entry",
    [
        {"restic_password": "hunter2-hunter2"},
        {"aws_secret_access_key": "x" * 40},
        {"repository": "s3:https://" + FAKE_KEY_ID + ":" + FAKE_SECRET_KEY + "@h/b"},
    ],
)
def test_la_carga_de_backups_yaml_rechaza_credenciales_sin_repetirlas(tmp_path, entry):
    store = LocalStore(tmp_path / "ws")
    store.write_text(
        "connectors/backups.yaml", yaml.safe_dump({"repositories": [{**REPO, **entry}]})
    )

    with pytest.raises(ConnectorConfigError) as caught:
        load_connector_config(store, "backups", BackupsConfig)

    assert not any(str(v) in str(caught.value) for v in entry.values())


# --- restaurar el VPS entero (prueba manual: el runbook tiene el procedimiento) -----------------


def runbook_section(title: str) -> str:
    text = (RUNBOOKS / "kit-copias.md").read_text(encoding="utf-8")
    return re.split(rf"^## \d+\. {re.escape(title)}\n", text, flags=re.MULTILINE)[1].split("\n## ")[
        0
    ]


def test_el_runbook_tiene_la_restauracion_completa_y_su_registro_como_revision_humana():
    section = runbook_section("Restaurar el VPS entero en una máquina nueva")

    assert "box.sh install" in section and "restic" in section and "restore" in section
    assert "revisión humana" in section
    assert "restauracion-de-evidencias.md" in (RUNBOOKS / "kit-copias.md").read_text("utf-8")
    assert (RUNBOOKS / "restauracion-de-evidencias.md").is_file()
