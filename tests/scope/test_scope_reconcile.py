import pytest

from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    DiscrepancyKind,
    ObservedInputError,
    ScopeState,
    approve_scope,
    load_inventory,
    parse_observed,
    reconcile,
    scope_status,
)

from .scope_helpers import APPROVED_AT, edit


@pytest.fixture
def assets(store):
    return load_inventory(store).inventory.assets.assets


def observed(*hosts: str):
    lines = "\n".join(f"  - {host}" for host in hosts)
    text = f"source: edr\nobserved_at: 2026-10-27T08:00:00+00:00\nhosts:\n{lines or '  []'}\n"
    return parse_observed(text.replace("hosts:\n  []", "hosts: []"))


def pairs(report, kind):
    return [d.identity for d in report.discrepancies if d.kind is kind]


def test_identidad_por_hostname_ignora_mayusculas_y_punto_final(assets):
    report = reconcile(assets, observed("{hostname: 'SRV-APP.Taller-Norte.example.'}"))

    assert pairs(report, DiscrepancyKind.NOT_IN_INVENTORY) == []
    assert "asset:srv-app" not in pairs(report, DiscrepancyKind.NOT_OBSERVED)


def test_identidad_por_asset_id_prevalece_sobre_el_hostname(assets):
    report = reconcile(assets, observed("{hostname: otro-nombre, asset_id: srv-app}"))

    assert pairs(report, DiscrepancyKind.NOT_IN_INVENTORY) == []
    assert "asset:srv-app" not in pairs(report, DiscrepancyKind.NOT_OBSERVED)


def test_asset_id_desconocido_es_discrepancia_aunque_el_hostname_exista(assets):
    report = reconcile(
        assets, observed("{hostname: srv-app.taller-norte.example, asset_id: fantasma}")
    )

    assert pairs(report, DiscrepancyKind.NOT_IN_INVENTORY) == ["asset:fantasma"]


def test_not_observed_solo_para_laptop_y_server_desplegados(store, assets):
    edit(
        store,
        "assets",
        "  - id: prov-nube",
        "  - id: srv-futuro\n    type: server\n    deployment: planned\n"
        "    hostname: srv-futuro.taller-norte.example\n    function: x\n    os: x\n"
        "    environment: x\n    location: x\n  - id: prov-nube",
    )
    edit(store, "system", "  - prov-nube\n", "  - prov-nube\n  - srv-futuro\n")
    assets = load_inventory(store).inventory.assets.assets

    report = reconcile(assets, observed())

    assert pairs(report, DiscrepancyKind.NOT_OBSERVED) == [
        "asset:lap-ana",
        "asset:lap-personal",
        "asset:srv-app",
    ]


@pytest.mark.parametrize(
    "hosts",
    [
        ["{hostname: srv-app.x}", "{hostname: SRV-APP.x.}"],
        ["{hostname: a, asset_id: srv-app}", "{hostname: b, asset_id: srv-app}"],
    ],
)
def test_duplicados_se_informan_como_error_de_entrada(assets, hosts):
    report = reconcile(assets, observed(*hosts))

    assert any("equipo repetido" in e for e in report.input_errors)


def test_hostname_vacio_se_informa_como_error_de_entrada(assets):
    report = reconcile(assets, observed("{hostname: '  '}"))

    assert report.input_errors


@pytest.mark.parametrize(
    "text",
    [
        "hosts: []\n",
        "source: edr\nobserved_at: 2026-10-27T08:00:00\nhosts: []\n",
        "source: edr\nobserved_at: 2026-10-27T08:00:00+00:00\nhosts: [{nombre: x}]\n",
        "source: [\n",
    ],
)
def test_entrada_observada_invalida_se_rechaza(text):
    with pytest.raises(ObservedInputError):
        parse_observed(text)


def test_entrada_observada_con_schema_version_desconocida_se_rechaza_en_espanol():
    text = "schema_version: '999'\nsource: edr\nobserved_at: 2026-10-27T08:00:00+00:00\nhosts: []\n"

    with pytest.raises(ObservedInputError) as error:
        parse_observed(text)

    assert "no soportada" in "\n".join(error.value.errors)


def test_entrada_observada_con_schema_version_1_0_se_acepta():
    text = "schema_version: '1.0'\nsource: edr\nobserved_at: 2026-10-27T08:00:00+00:00\nhosts: []\n"

    assert parse_observed(text).schema_version == "1.0"


def test_la_discrepancia_no_impide_aprobar_el_alcance(store, assets):
    report = reconcile(assets, observed("{hostname: srv-extra.x}"))
    assert report.discrepancies

    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)

    assert scope_status(store).state is ScopeState.APPROVED
