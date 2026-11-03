"""Ventanas de D4: 30 d (revisiones, aprobaciones, restore tests, offsite), 14 d (manual),
7 d antes de los 30 (técnica) y al vencer (acciones)."""

from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from checks.acceptance_helpers import make_world
from checks.test_inputs import collect as collect_evidence
from matrix.evaluate_helpers import NOW

from ens_evidence.backup.restore_test import RestoreTest, restore_test_key
from ens_evidence.catalog.controls import CatalogVersion
from ens_evidence.categorization.approval import APPROVAL_KEY as CATEGORIZATION_KEY
from ens_evidence.categorization.approval import CategorizationApproval
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.evidence.importer import import_manual
from ens_evidence.models.enums import Category, Role
from ens_evidence.reports.expiries import WINDOW_DAYS, scan_expiries
from ens_evidence.risk.models import APPROVAL_KEY as RISK_KEY
from ens_evidence.risk.models import RISK_FILES, RiskApproval
from ens_evidence.scheduling.config import ExpiryWarnings
from ens_evidence.workspace import LocalStore

from .world import MADRID

TODAY = date(2026, 11, 2)
CUTOFF = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)
SHA = "a" * 64


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def scan(store, *, cutoff=CUTOFF, today=TODAY):
    return scan_expiries(store, cutoff=cutoff, today=today, tz=MADRID)


def by_kind(result):
    return {(i.kind, i.subject): i for i in result.items}


def dump(model) -> str:
    return yaml.safe_dump(model.model_dump(mode="json"))


def approved(days_left: int) -> tuple[datetime, datetime]:
    valid_until = CUTOFF + timedelta(days=days_left)
    return valid_until.replace(year=valid_until.year - 1), valid_until


def test_workspace_vacio_no_tiene_fuentes_ni_problemas(store):
    result = scan(store)

    assert result.items == ()
    assert result.consulted == ()
    assert result.problems == ()


def test_las_ventanas_son_las_de_d4():
    assert WINDOW_DAYS == {
        "measure_review": 30,
        "applicability_review": 30,
        "categorization_approval": 30,
        "risk_approval": 30,
        "restore_test": 30,
        "offsite_declaration": 30,
        "manual_evidence": 14,
        "technical_evidence": 7,
        "treatment_action": 0,
    }


def write_categorization(store, days_left: int) -> None:
    approved_at, valid_until = approved(days_left)
    approval = CategorizationApproval(
        file_sha256=SHA,
        scope_fingerprint=SHA,
        category=Category.MEDIA,
        approved_by="Ana",
        role=Role.SECURITY_OFFICER,
        approved_at=approved_at,
        valid_until=valid_until,
    )
    store.write_text(CATEGORIZATION_KEY, dump(approval))


def test_las_ventanas_pasadas_cambian_el_resultado(store):
    write_categorization(store, 45)

    default = scan(store)
    wider = scan_expiries(
        store,
        cutoff=CUTOFF,
        today=TODAY,
        tz=MADRID,
        windows={**WINDOW_DAYS, "categorization_approval": 60},
    )

    assert default.items == ()
    assert [i.kind for i in wider.items] == ["categorization_approval"]


def test_un_tipo_que_falta_en_las_ventanas_usa_la_ventana_por_defecto(store):
    write_categorization(store, 20)

    result = scan_expiries(store, cutoff=CUTOFF, today=TODAY, tz=MADRID, windows={})

    assert [i.kind for i in result.items] == ["categorization_approval"]


def test_las_ventanas_de_schedule_yaml_se_traducen_a_cada_tipo_de_caducidad():
    assert ExpiryWarnings().windows() == WINDOW_DAYS
    narrower = ExpiryWarnings(approvals=5, measure_reviews=3).windows()
    assert narrower["categorization_approval"] == narrower["risk_approval"] == 5
    assert narrower["measure_review"] == narrower["applicability_review"] == 3


# --- aprobaciones -------------------------------------------------------------------------------


@pytest.mark.parametrize(("days_left", "listed"), [(30, True), (31, False), (-3, True)])
def test_aprobacion_de_categorizacion_entra_a_30_dias(store, days_left, listed):
    approved_at, valid_until = approved(days_left)
    store.write_text(
        CATEGORIZATION_KEY,
        dump(
            CategorizationApproval(
                file_sha256=SHA,
                scope_fingerprint=SHA,
                category=Category.MEDIA,
                approved_by="Ana",
                role=Role.SECURITY_OFFICER,
                approved_at=approved_at,
                valid_until=valid_until,
            )
        ),
    )

    result = scan(store)

    assert bool(result.items) is listed
    assert [s.key for s in result.consulted] == [CATEGORIZATION_KEY]


def test_aprobacion_de_riesgos_lleva_su_fuente_con_huella(store):
    approved_at, valid_until = approved(10)
    store.write_text(
        RISK_KEY,
        dump(
            RiskApproval(
                approved_by="Ana",
                role=Role.SECURITY_OFFICER,
                approved_at=approved_at,
                valid_until=valid_until,
                scope_fingerprint=SHA,
                catalog_version=CatalogVersion(
                    snapshot_sha256=SHA, consolidation_date="2026-01-01"
                ),
                files=dict.fromkeys(RISK_FILES, SHA),
            )
        ),
    )

    (item,) = scan(store).items

    assert (item.kind, item.days_left) == ("risk_approval", 10)
    (source,) = item.sources
    assert (source.kind, source.key, source.at) == ("approval", RISK_KEY, approved_at)


def test_aprobacion_ilegible_va_a_problemas_sin_su_contenido(store):
    store.write_text(CATEGORIZATION_KEY, "no: [valido\n")

    result = scan(store)

    assert result.items == ()
    assert result.problems == (f"{CATEGORIZATION_KEY}: ilegible",)


# --- acciones del plan --------------------------------------------------------------------------


def write_plan(store, *actions: tuple[str, str, str]):
    rows = "".join(
        f"  - {{id: {i}, risk: R1, description: d, owner: o, due: {due}, status: {status}}}\n"
        for i, due, status in actions
    )
    store.write_text("risk/treatment-plan.yaml", "actions:\n" + rows)


def test_accion_se_lista_al_vencer_y_no_antes(store):
    write_plan(
        store,
        ("A-ayer", "2026-11-01", "pending"),
        ("A-hoy", "2026-11-02", "in_progress"),
        ("A-manana", "2026-11-03", "pending"),
    )

    found = by_kind(scan(store))

    assert set(found) == {("treatment_action", "A-ayer"), ("treatment_action", "A-hoy")}
    assert found["treatment_action", "A-ayer"].days_left == -1
    assert found["treatment_action", "A-hoy"].days_left == 0


def test_accion_hecha_o_cancelada_no_caduca(store):
    write_plan(store, ("A1", "2026-10-01", "done"), ("A2", "2026-10-01", "cancelled"))

    assert scan(store).items == ()


def test_plan_ilegible_va_a_problemas(store):
    store.write_text("risk/treatment-plan.yaml", "actions: 3\n")

    assert scan(store).problems == ("risk/treatment-plan.yaml: ilegible",)


# --- evidencia ----------------------------------------------------------------------------------


def manual(store, *, valid_in_days: int | None, asset: str = "doc-1", collected=CUTOFF):
    return import_manual(
        store,
        f"{asset}-{valid_in_days}".encode(),
        asset_id=asset,
        submitted_by="ana",
        now=collected + timedelta(minutes=1),
        collected_at=collected,
        valid_until=None if valid_in_days is None else CUTOFF + timedelta(days=valid_in_days),
    )


@pytest.mark.parametrize(("days", "listed"), [(14, True), (15, False)])
def test_evidencia_manual_entra_a_14_dias(store, days, listed):
    manual(store, valid_in_days=days)

    result = scan(store, cutoff=CUTOFF + timedelta(minutes=2))

    assert bool(result.items) is listed
    assert result.consulted


def test_evidencia_sin_valid_until_no_caduca(store):
    manual(store, valid_in_days=None)

    assert scan(store, cutoff=CUTOFF + timedelta(minutes=2)).items == ()


def test_una_evidencia_manual_sustituida_ya_no_se_lista(store):
    manual(store, valid_in_days=3, collected=CUTOFF - timedelta(days=2))
    manual(store, valid_in_days=200, collected=CUTOFF - timedelta(days=1))

    assert scan(store).items == ()


@pytest.mark.parametrize(("age_days", "listed"), [(23, True), (22, False)])
def test_evidencia_tecnica_avisa_7_dias_antes_de_sus_30(store, age_days, listed):
    collected = CUTOFF - timedelta(days=age_days)
    collect_evidence(store, now=collected + timedelta(minutes=1), collected_at=collected)

    result = scan(store)

    assert bool(result.items) is listed
    if listed:
        (item,) = result.items
        assert (item.kind, item.days_left) == ("technical_evidence", 7)


def test_evidencia_de_collector_con_fuente_ajena_no_se_lista_y_va_a_problemas(store):
    collected = CUTOFF - timedelta(days=24)
    collect_evidence(
        store,
        now=collected + timedelta(minutes=1),
        collected_at=collected,
        source="<script>x</script>",
    )

    result = scan(store)

    assert result.items == ()
    assert len(result.problems) == 1
    assert result.problems[0].endswith("identidad no coincide con el conector")
    assert "script" not in result.problems[0]


def test_evidencia_de_collector_con_activo_ajeno_al_configurado_no_se_lista(store):
    config = connector_config_key("github")
    store.write_text(config, "asset_id: gh-taller\norganization: taller-norte\n")
    collected = CUTOFF - timedelta(days=24)
    collect_evidence(
        store,
        now=collected + timedelta(minutes=1),
        collected_at=collected,
        asset_id="otro-activo",
    )

    result = scan(store)

    assert result.items == ()
    assert len(result.problems) == 1


def test_evidencia_posterior_al_corte_no_existe(store):
    manual(store, valid_in_days=5, collected=CUTOFF + timedelta(days=1))

    assert scan(store).items == ()


# --- restore tests y copia externa --------------------------------------------------------------


def restore_test(test_id="RT-1", *, valid_in_days: int, performed=CUTOFF - timedelta(days=5)):
    return RestoreTest.model_validate(
        {
            "test_id": test_id,
            "system": "svc",
            "performed_at": performed,
            "restore_id": "R1",
            "isolated_target": True,
            "backup": {"snapshot_id": SHA},
            "result": {"restored": True, "integrity_verified": True, "application_started": True},
            "review": {
                "approved": True,
                "reviewer": "Ana",
                "role": "system_administrator",
                "reviewed_at": performed,
                "valid_until": CUTOFF + timedelta(days=valid_in_days),
            },
            "repository_id": SHA,
        }
    )


def test_restore_test_entra_a_30_dias_y_no_se_duplica_como_evidencia_manual(store):
    record = restore_test(valid_in_days=12)
    data = dump(record).encode()
    store.write_bytes(restore_test_key("RT-1"), data)
    import_manual(
        store,
        data,
        asset_id="svc",
        submitted_by="Ana",
        now=CUTOFF,
        collected_at=record.performed_at,
        valid_until=record.review.valid_until,
    )

    (item,) = scan(store).items

    assert (item.kind, item.subject, item.days_left) == ("restore_test", "RT-1 (svc)", 12)
    assert item.sources[0].kind == "restore_test"


def test_solo_cuenta_el_restore_test_mas_reciente_del_sistema(store):
    old = restore_test("RT-old", valid_in_days=2, performed=CUTOFF - timedelta(days=40))
    new = restore_test("RT-new", valid_in_days=300)
    store.write_text(restore_test_key("RT-old"), dump(old))
    store.write_text(restore_test_key("RT-new"), dump(new))

    assert scan(store).items == ()


def test_restore_test_ilegible_va_a_problemas(store):
    store.write_text(restore_test_key("RT-x"), "test_id: 3\n")

    assert scan(store).problems == (f"{restore_test_key('RT-x')}: ilegible",)


def test_declaracion_offsite_entra_a_30_dias(store):
    store.write_text(
        connector_config_key("restic"),
        yaml.safe_dump(
            {
                "asset_id": "bk",
                "max_backup_age_hours": 24,
                "max_check_age_days": 30,
                "restore_test_validity_days": 90,
                "min_check_read_data_percent": 5,
                "offsite": {
                    "location": "S3 en otra región",
                    "administrative_separation": "otra cuenta",
                    "reviewer": "Ana",
                    "role": "system_administrator",
                    "reviewed_at": CUTOFF - timedelta(days=100),
                    "valid_until": CUTOFF + timedelta(days=20),
                },
            }
        ),
    )

    (item,) = scan(store).items

    assert (item.kind, item.subject, item.days_left) == ("offsite_declaration", "copia externa", 20)
    assert item.sources[0].kind == "config"


# --- revisiones de medida -----------------------------------------------------------------------


def test_revisiones_de_medida_con_fuente_y_huella(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch, "ready")
    far = NOW + timedelta(days=400)

    result = scan_expiries(world.store, cutoff=far, today=far.date(), tz=MADRID)

    reviews = [i for i in result.items if i.kind == "measure_review"]
    assert len(reviews) >= 60
    assert all(i.days_left < 0 for i in reviews)
    assert all(i.sources[0].kind == "review" for i in reviews)
    assert all(world.store.exists(i.sources[0].key) for i in reviews)
    assert result.problems == ()


def test_revision_ilegible_va_a_problemas_y_no_se_oculta(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch, "ready")
    key = next(k for k in world.store.list("controls/reviews") if k.endswith(".yaml"))
    world.store.write_bytes(key, b"axis: [")

    result = scan_expiries(world.store, cutoff=NOW, today=NOW.date(), tz=MADRID)

    assert any("revisiones de" in p and "ilegibles" in p for p in result.problems)


def test_orden_estable_por_caducidad(store):
    write_plan(store, ("B", "2026-11-01", "pending"), ("A", "2026-11-01", "pending"))

    assert [i.subject for i in scan(store).items] == ["A", "B"]
