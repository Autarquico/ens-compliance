import pytest

from ens_evidence.models.enums import Role
from ens_evidence.scope import ScopeState, approve_scope, require_approved_scope, scope_status

from .scope_helpers import APPROVED_AT, edit

EDITS = {
    "assets": ("os: Debian 12", "os: Debian 13"),
    "services": ("name: Facturación online", "name: Facturación"),
    "dependencies": ("purpose: Destino de las copias", "purpose: Copias"),
    "system": ("Sistema de información que", "Sistema que"),
}


@pytest.fixture
def approved(store):
    return approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)


@pytest.mark.parametrize("name", EDITS)
def test_editar_cualquiera_de_los_cuatro_ficheros_deja_el_alcance_en_stale(store, approved, name):
    edit(store, name, *EDITS[name])

    status = scope_status(store)

    assert status.state is ScopeState.STALE
    assert status.fingerprint != approved.fingerprint
    assert status.approval == approved


@pytest.mark.parametrize("name", EDITS)
def test_publicacion_parcial_de_un_fichero_nunca_queda_aprobada(store, approved, name):
    old, new = EDITS[name]
    edit(store, name, old, new)

    assert scope_status(store).state is not ScopeState.APPROVED


def test_publicacion_parcial_que_rompe_el_inventario_queda_invalida(store, approved):
    store.write_text("scope/services.yaml", "services: [\n")

    assert scope_status(store).state is ScopeState.INVALID


def test_comentarios_yaml_no_cambian_la_huella(store, approved):
    edit(store, "assets", "assets:\n", "# revisado en octubre\nassets:\n")
    edit(store, "system", "included_services:", "# servicios\nincluded_services:")

    status = scope_status(store)

    assert status.state is ScopeState.APPROVED
    assert status.fingerprint == approved.fingerprint


def test_reordenar_claves_dentro_de_un_activo_no_cambia_la_huella(store, approved):
    edit(
        store,
        "dependencies",
        "asset: bk-diario\n    depends_on: prov-nube",
        "depends_on: prov-nube\n    asset: bk-diario",
    )

    assert scope_status(store).fingerprint == approved.fingerprint


def test_volver_a_aprobar_tras_editar_recupera_el_estado_aprobado(store, approved):
    edit(store, "assets", *EDITS["assets"])
    renewed = approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)

    assert renewed.fingerprint != approved.fingerprint
    assert require_approved_scope(store).fingerprint == renewed.fingerprint
