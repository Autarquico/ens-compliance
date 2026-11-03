from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.models.enums import CheckStatus, CollectionStatus, Readiness
from ens_evidence.reports.changes import changes_since_base, diff
from ens_evidence.reports.model import (
    AssetsSummary,
    CheckCounts,
    ConnectorEntry,
    ExpiryItem,
    ReadinessValue,
    Sourced,
    Unavailable,
)
from ens_evidence.workspace import LocalStore

from .helpers import NOON, daily, put_daily, ref


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def at(day: int, hour: int = 6) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def sourced(model_type, value):
    return Sourced[model_type](value=value, sources=[ref()])


def counts(**kw) -> Sourced:
    return sourced(CheckCounts, CheckCounts(counts={CheckStatus[k]: n for k, n in kw.items()}))


def connector(name="github", *, status=CollectionStatus.SUCCESS, **kw) -> ConnectorEntry:
    return ConnectorEntry(
        connector=name,
        collection=sourced(CollectionStatus, status),
        checks=counts(**(kw or {"PASS": 1})),
    )


def readiness(state=Readiness.NOT_READY, *blockers) -> Sourced:
    return sourced(ReadinessValue, ReadinessValue(readiness=state, blockers=list(blockers)))


def expiry(kind, subject) -> ExpiryItem:
    return ExpiryItem(kind=kind, subject=subject, expires_at=at(20), days_left=18, sources=[ref()])


# --- base ---------------------------------------------------------------------------------------


def test_primer_informe_no_es_sin_cambios(store):
    result = changes_since_base(store, daily("2026-11-02"))

    assert isinstance(result, Unavailable)
    assert "no hay informe diario anterior" in result.reason


def test_con_base_identica_es_sin_cambios_citando_el_informe(store):
    pair = put_daily(store, daily("2026-11-01"), at(1))

    result = changes_since_base(store, daily("2026-11-02"))

    assert isinstance(result, Sourced)
    assert result.value.items == []
    assert result.value.base_period == "2026-11-01"
    (source,) = result.sources
    assert (source.kind, source.key) == ("report", pair.json_key)


def test_base_corrupta_es_unavailable_y_no_retrocede_a_una_anterior(store):
    put_daily(store, daily("2026-10-30"), at(1) - timedelta(days=2))
    broken = put_daily(store, daily("2026-11-01"), at(1))
    (store._root / broken.json_key).write_bytes(b"{")

    result = changes_since_base(store, daily("2026-11-02"))

    assert isinstance(result, Unavailable)
    assert broken.json_key in result.reason


def test_base_con_html_vacio_es_unavailable(store):
    put_daily(store, daily("2026-11-01"), at(1), html=b"  ")

    assert isinstance(changes_since_base(store, daily("2026-11-02")), Unavailable)


def test_varios_informes_del_dia_la_base_es_el_ultimo(store):
    put_daily(store, daily("2026-11-01", readiness=readiness(Readiness.READY)), at(1, 6))
    put_daily(store, daily("2026-11-01", readiness=readiness(Readiness.NOT_READY)), at(1, 9))

    result = changes_since_base(
        store, daily("2026-11-02", readiness=readiness(Readiness.NOT_READY))
    )

    assert result.value.items == []


def test_un_hueco_de_dias_usa_el_informe_mas_reciente(store):
    put_daily(store, daily("2026-10-29"), at(1) - timedelta(days=3))

    result = changes_since_base(store, daily("2026-11-02"))

    assert result.value.base_period == "2026-10-29"


def test_el_informe_del_mismo_dia_no_es_base_de_si_mismo(store):
    put_daily(store, daily("2026-11-02"), at(2))

    assert isinstance(changes_since_base(store, daily("2026-11-02")), Unavailable)


# --- diferencias --------------------------------------------------------------------------------


def subjects(base, current):
    return [(i.subject, i.change) for i in diff(base, current)]


def test_igual_no_da_diferencias_aunque_cambien_instante_y_fuentes():
    base = daily(connectors=[connector()])
    current = daily(connectors=[connector()], generated_at=NOON + timedelta(days=1))

    assert diff(base, current) == []


def test_cambio_de_preparacion_y_de_bloqueos():
    base = daily(readiness=readiness(Readiness.NOT_READY, "a", "b"))
    current = daily(readiness=readiness(Readiness.READY, "b", "c"))

    assert subjects(base, current) == [
        ("blocker: a", "removed"),
        ("blocker: c", "added"),
        ("readiness", "changed"),
    ]


def test_conectores_alta_baja_y_cambio():
    base = daily(connectors=[connector("github"), connector("restic")])
    current = daily(
        connectors=[connector("github", FAIL=2), connector("cloudflare")],
    )

    assert subjects(base, current) == [
        ("connector: cloudflare", "added"),
        ("connector: github", "changed"),
        ("connector: restic", "removed"),
    ]


def test_conector_que_pasa_a_no_disponible_es_un_cambio():
    gone = Unavailable(reason="sin ejecución")
    base = daily(connectors=[connector()])
    current = daily(connectors=[ConnectorEntry(connector="github", collection=gone, checks=gone)])

    assert subjects(base, current) == [("connector: github", "changed")]


def test_activos_y_activos_sin_agente():
    base = daily(
        assets=sourced(AssetsSummary, AssetsSummary(total=2, by_type={"laptop": 2})),
        assets_without_agent=sourced(list[str], ["lap-1"]),
    )
    current = daily(
        assets=sourced(AssetsSummary, AssetsSummary(total=3, by_type={"laptop": 2, "server": 1})),
        assets_without_agent=sourced(list[str], ["lap-2"]),
    )

    assert subjects(base, current) == [
        ("assets.server", "changed"),
        ("assets.total", "changed"),
        ("assets_without_agent: lap-1", "removed"),
        ("assets_without_agent: lap-2", "added"),
    ]


def test_caducidades_nuevas_y_resueltas():
    base = daily(expiring=sourced(list[ExpiryItem], [expiry("restore_test", "RT-1")]))
    current = daily(expiring=sourced(list[ExpiryItem], [expiry("risk_approval", "análisis")]))

    assert subjects(base, current) == [
        ("expiring: restore_test RT-1", "removed"),
        ("expiring: risk_approval análisis", "added"),
    ]


def test_dato_que_deja_de_estar_disponible_se_dice_no_se_calla():
    base = daily()
    current = daily(readiness=Unavailable(reason="x"))

    assert subjects(base, current) == [("readiness: disponibilidad del dato", "changed")]


def test_dos_unavailable_no_son_un_cambio():
    base = daily(expiring=Unavailable(reason="a"))
    current = daily(expiring=Unavailable(reason="b"))

    assert diff(base, current) == []
