"""PWD-001..003: PASS solo con datos que lo demuestran."""

import pytest

from ens_evidence.connectors.passwords import checks
from ens_evidence.models.enums import CheckStatus

from .conftest import NOW, days_ago, export_doc, iso, user

PASS, FAIL, UNKNOWN, ERROR = (
    CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN, CheckStatus.ERROR
)  # fmt: skip


@pytest.fixture
def run(collect):
    def evaluate(check, doc=None, at=NOW, **changes):
        result = collect(doc if doc is not None else export_doc(), **changes)
        (outcome,) = check(result, "ev-1", at)
        assert outcome.evidence_ids == ["ev-1"] and outcome.asset_id == "vaultwarden"
        return outcome

    return evaluate


def test_los_ids_son_los_del_catalogo():
    from ens_evidence.checks.catalog import CHECKS

    ids = {c for c, s in CHECKS.items() if s.connector == "passwords"}
    assert ids == {checks.SECOND_FACTOR, checks.NO_INACTIVE, checks.SNAPSHOT_RECENT}


def test_todo_en_orden_da_pass_en_los_tres(run):
    for check in (checks.second_factor, checks.no_inactive_accounts, checks.snapshot_recent):
        assert run(check).status is PASS


# --- PWD-001 ---


def test_sin_segundo_factor_es_fail_con_ids_internos_y_sin_datos_personales(run):
    bad = user(second_factor=False)
    outcome = run(checks.second_factor, export_doc([user(), bad]))

    assert outcome.status is FAIL
    assert bad["id"] in outcome.reason and "1 de 2" in outcome.reason
    assert "@" not in outcome.reason


def test_deshabilitados_e_invitados_no_cuentan_en_pwd_001(run):
    users = [
        user(),
        user(second_factor=False, enabled=False),
        user(second_factor=False, invited=True),
    ]

    assert run(checks.second_factor, export_doc(users)).status is PASS


@pytest.mark.parametrize(
    "users",
    [
        [],
        [user(enabled=False)],
        [user(invited=True), user(invited=True)],
    ],
    ids=["cero", "todos-deshabilitados", "todos-invitados"],
)
@pytest.mark.parametrize("check", [checks.second_factor, checks.no_inactive_accounts])
def test_poblacion_vacia_es_unknown(run, check, users):
    assert run(check, export_doc(users)).status is UNKNOWN


def test_fail_lista_como_mucho_cinco_ids(run):
    users = [user(second_factor=False) for _ in range(7)]
    outcome = run(checks.second_factor, export_doc(users))

    assert "y 2 más" in outcome.reason


# --- PWD-002 ---


@pytest.mark.parametrize(
    ("days", "status"),
    [(89, PASS), (90, PASS), (90.0001, FAIL), (91, FAIL)],
    ids=["antes", "en-el-umbral", "justo-despues", "despues"],
)
def test_umbral_de_inactividad(run, days, status):
    doc = export_doc([user(last_active=iso(days_ago(days)))])

    assert run(checks.no_inactive_accounts, doc).status is status


def test_pasa_sin_actividad_reciente_pero_dentro_del_umbral_y_dice_que_no_suspende(run):
    outcome = run(checks.no_inactive_accounts)

    assert "no suspende" in outcome.reason


def test_fail_dice_que_detectar_no_es_suspender(run):
    outcome = run(checks.no_inactive_accounts, export_doc([user(last_active=iso(days_ago(200)))]))

    assert outcome.status is FAIL and "sigue habilitada" in outcome.reason


def test_sin_actividad_se_mide_desde_created_at_y_se_nombra(run):
    old = user(last_active=None, created_at=iso(days_ago(120)))
    fresh = user(last_active=None, created_at=iso(days_ago(10)))

    outcome = run(checks.no_inactive_accounts, export_doc([old, fresh]))
    assert (
        outcome.status is FAIL and old["id"] in outcome.reason and fresh["id"] not in outcome.reason
    )
    assert "created_at" in outcome.reason

    ok = run(checks.no_inactive_accounts, export_doc([fresh]))
    assert ok.status is PASS and "created_at" in ok.reason


def test_actividad_posterior_al_momento_de_evaluar_es_unknown(run):
    early = days_ago(3)

    assert run(checks.no_inactive_accounts, at=early).status is UNKNOWN


def test_el_umbral_se_lee_de_la_evidencia(run):
    doc = export_doc([user(last_active=iso(days_ago(40)))])

    assert run(checks.no_inactive_accounts, doc, inactive_days=30).status is FAIL
    assert run(checks.no_inactive_accounts, doc, inactive_days=60).status is PASS


# --- PWD-003 ---


def test_instantanea_fallida_es_fail_y_cita_la_ultima_buena(run):
    last = iso(NOW - __import__("datetime").timedelta(hours=3))
    doc = export_doc(snapshot_ok=False, last_good_snapshot_at=last)

    outcome = run(checks.snapshot_recent, doc)
    assert outcome.status is FAIL and last in outcome.reason


def test_instantanea_fallida_sin_ninguna_buena_es_fail(run):
    doc = export_doc(snapshot_ok=False, last_good_snapshot_at=None)

    assert run(checks.snapshot_recent, doc).status is FAIL


@pytest.mark.parametrize(
    ("gap_minutes", "status"), [(59, PASS), (60, PASS), (61, FAIL)], ids=["antes", "en", "despues"]
)
def test_la_instantanea_no_va_mas_atras_del_export_que_el_umbral(run, gap_minutes, status):
    from datetime import timedelta

    exported = NOW - timedelta(minutes=5)
    doc = export_doc(
        exported_at=iso(exported),
        last_good_snapshot_at=iso(exported - timedelta(minutes=gap_minutes)),
    )

    assert run(checks.snapshot_recent, doc).status is status


# --- sin export ---


@pytest.mark.parametrize(
    "check", [checks.second_factor, checks.no_inactive_accounts, checks.snapshot_recent]
)
def test_sin_export_valido_nunca_es_pass(run, check, collect, export_file):
    export_file.path.unlink(missing_ok=True)
    result = collect(export_path=str(export_file.path))
    (outcome,) = check(result, "ev-1", NOW)

    assert outcome.status is ERROR and "no hay export" in outcome.reason
