from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    DiscrepancyKind,
    ScopeState,
    approve_scope,
    load_inventory,
    parse_observed,
    reconcile,
    require_approved_scope,
    scope_status,
)

from .scope_helpers import APPROVED_AT

OBSERVED_WITH_UNKNOWN_HOST = """
source: edr
observed_at: 2026-10-27T08:00:00+00:00
hosts:
  - hostname: lap-ana.taller-norte.example
  - hostname: srv-app.taller-norte.example
  - hostname: srv-extra.taller-norte.example
"""


def test_alcance_aprobado_queda_registrado_y_desbloquea_la_categorizacion(store):
    assert load_inventory(store).errors == []

    approval = approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)

    registered = scope_status(store)
    assert registered.state is ScopeState.APPROVED
    assert registered.approval == approval
    assert (approval.approved_by, approval.role, approval.approved_at) == (
        "Ana Ruiz",
        Role.SYSTEM_OWNER,
        APPROVED_AT,
    )
    assert require_approved_scope(store).fingerprint == approval.fingerprint


def test_host_no_inventariado_se_lista_como_discrepancia(store):
    assets = load_inventory(store).inventory.assets.assets

    report = reconcile(assets, parse_observed(OBSERVED_WITH_UNKNOWN_HOST))

    unknown = [d for d in report.discrepancies if d.kind is DiscrepancyKind.NOT_IN_INVENTORY]
    assert [d.identity for d in unknown] == ["host:srv-extra.taller-norte.example"]
