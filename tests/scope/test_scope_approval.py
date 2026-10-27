from datetime import datetime

import pytest

from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    APPROVAL_KEY,
    ScopeApprovalRejected,
    ScopeNotApproved,
    ScopeState,
    approve_scope,
    require_approved_scope,
    scope_status,
)

from .scope_helpers import APPROVED_AT, edit


def break_empty_inventory(store):
    store.write_text("scope/assets.yaml", "assets: []\n")


def break_invalid_yaml(store):
    store.write_text("scope/assets.yaml", "assets: [\n")


def break_reference(store):
    edit(store, "services", "assets: [app-facturas", "assets: [fantasma, app-facturas")


def break_unscoped_asset(store):
    edit(store, "system", "  - bk-externo\n", "")


def break_unjustified_exclusion(store):
    edit(
        store,
        "system",
        "justification: Equipo personal sin acceso a producción ni a datos de clientes.",
        'justification: "  "',
    )


@pytest.mark.parametrize(
    ("breaker", "expected"),
    [
        (break_empty_inventory, "el inventario está vacío"),
        (break_invalid_yaml, "no es YAML válido"),
        (break_reference, "el activo fantasma no existe"),
        (break_unscoped_asset, "no está incluido ni excluido"),
        (break_unjustified_exclusion, "justification"),
    ],
)
def test_aprobacion_rechazada_con_inventario_incompleto(store, breaker, expected):
    breaker(store)

    with pytest.raises(ScopeApprovalRejected) as rejected:
        approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)

    assert any(expected in error for error in rejected.value.errors)
    assert not store.exists(APPROVAL_KEY)
    assert scope_status(store).state is ScopeState.INVALID


@pytest.mark.parametrize("role", [r for r in Role if r is not Role.SYSTEM_OWNER])
def test_aprobacion_rechazada_con_rol_distinto_de_system_owner(store, role):
    with pytest.raises(ScopeApprovalRejected) as rejected:
        approve_scope(store, "Ana Ruiz", role, APPROVED_AT)

    assert any("system_owner" in error for error in rejected.value.errors)
    assert not store.exists(APPROVAL_KEY)


@pytest.mark.parametrize("by", ["", "   "])
def test_aprobacion_rechazada_con_aprobador_vacio(store, by):
    with pytest.raises(ScopeApprovalRejected):
        approve_scope(store, by, Role.SYSTEM_OWNER, APPROVED_AT)

    assert not store.exists(APPROVAL_KEY)


def test_aprobacion_rechazada_con_fecha_sin_zona_horaria(store):
    with pytest.raises(ScopeApprovalRejected):
        approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, datetime(2026, 10, 27, 9, 0))

    assert not store.exists(APPROVAL_KEY)


def test_alcance_sin_aprobar_no_pasa_la_puerta(store):
    assert scope_status(store).state is ScopeState.NOT_APPROVED

    with pytest.raises(ScopeNotApproved) as blocked:
        require_approved_scope(store)

    assert blocked.value.status.state is ScopeState.NOT_APPROVED


def test_aprobacion_corrupta_deja_el_alcance_invalido(store):
    store.write_text(APPROVAL_KEY, "fingerprint: nope\n")

    assert scope_status(store).state is ScopeState.INVALID
    with pytest.raises(ScopeNotApproved):
        require_approved_scope(store)


def test_aprobacion_con_rol_distinto_en_el_fichero_no_pasa_la_puerta(store):
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    text = store.read_text(APPROVAL_KEY).replace("system_owner", "security_officer")
    store.write_text(APPROVAL_KEY, text)

    assert scope_status(store).state is ScopeState.INVALID


def test_aprobacion_rechazada_si_falta_un_fichero_del_alcance(store, tmp_path):
    (tmp_path / "ws" / "scope" / "dependencies.yaml").unlink()

    with pytest.raises(ScopeApprovalRejected) as rejected:
        approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)

    assert any("dependencies.yaml: no existe" in e for e in rejected.value.errors)


def test_inventario_no_utf8_es_invalid_y_la_puerta_lanza(store):
    (store._root / "scope" / "assets.yaml").write_bytes(b"assets: \xff\xfe\n")

    status = scope_status(store)

    assert status.state is ScopeState.INVALID
    assert any("scope/assets.yaml" in e for e in status.errors)
    with pytest.raises(ScopeNotApproved):
        require_approved_scope(store)


def test_aprobacion_no_utf8_es_invalid_y_la_puerta_lanza(store):
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    (store._root / "scope" / "approval.yaml").write_bytes(b"\xff\xfe\x00")

    status = scope_status(store)

    assert status.state is ScopeState.INVALID
    assert any(APPROVAL_KEY in e for e in status.errors)
    with pytest.raises(ScopeNotApproved):
        require_approved_scope(store)
