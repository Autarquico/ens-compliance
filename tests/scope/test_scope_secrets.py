import pytest

from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    ScopeApprovalRejected,
    ScopeState,
    approve_scope,
    find_secrets,
    load_inventory,
    scope_status,
)

from .scope_helpers import APPROVED_AT, TOKEN, edit

PASSWORD_VALUE = "hunter2-correct-horse"


def test_valor_con_forma_de_token_se_rechaza_sin_aparecer_en_los_errores(store):
    edit(store, "assets", "usage: Desarrollo y administración", f"usage: {TOKEN}")

    errors = load_inventory(store).errors

    assert any("assets.yaml" in e and "usage" in e for e in errors)
    assert TOKEN not in " ".join(errors)


def test_clave_tipo_password_se_rechaza_sin_aparecer_en_los_errores(store):
    edit(
        store,
        "assets",
        "    owner: Ana Ruiz\n",
        f"    owner: Ana Ruiz\n    password: {PASSWORD_VALUE}\n",
    )

    errors = load_inventory(store).errors

    assert any("password" in e for e in errors)
    assert PASSWORD_VALUE not in " ".join(errors)


def test_aprobar_con_secreto_en_el_inventario_se_rechaza(store):
    edit(store, "services", "name: Facturación online", f"name: {TOKEN}")

    with pytest.raises(ScopeApprovalRejected) as rejected:
        approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)

    assert TOKEN not in str(rejected.value)
    assert TOKEN not in " ".join(rejected.value.errors)
    assert scope_status(store).state is ScopeState.INVALID


def test_url_con_credenciales_se_detecta():
    assert find_secrets({"repo": "https://user:pass@example.com/x.git"}) == ["repo"]


def test_valores_sin_forma_de_secreto_pasan():
    assert find_secrets({"repo": "taller-norte/facturas", "notes": ["MFA activo"]}) == []
