import pytest
import yaml

from ens_evidence.models.enums import Role
from ens_evidence.scope import ScopeState, approve_scope, load_documents, scope_status
from ens_evidence.scope.edit import (
    ScopeChoice,
    ScopeEditRejected,
    asset_references,
    remove_asset,
    save_asset,
)

from .scope_helpers import APPROVED_AT, edit

NOW = APPROVED_AT
KEYS = ("scope/assets.yaml", "scope/system.yaml", "scope/approval.yaml")


class FailingStore:
    """Delega en el store real y hace fallar la escritura número `fail_at` (desde 1)."""

    def __init__(self, inner, fail_at: int) -> None:
        self._inner, self._fail_at, self.writes = inner, fail_at, 0

    def write_text(self, key: str, text: str) -> None:
        self.writes += 1
        if self.writes == self._fail_at:
            raise OSError("disco lleno")
        self._inner.write_text(key, text)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def snapshot(store) -> dict[str, str | None]:
    return {k: store.read_text(k) if store.exists(k) else None for k in KEYS}


def approve(store) -> None:
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    assert scope_status(store).state is ScopeState.APPROVED


def assets_by_id(store) -> dict:
    return {a["id"]: a for a in yaml.safe_load(store.read_text("scope/assets.yaml"))["assets"]}


def system(store) -> dict:
    return yaml.safe_load(store.read_text("scope/system.yaml"))


SERVER = {
    "hostname": "nuevo.ejemplo.org",
    "function": "Pruebas",
    "os": "Debian 13",
    "environment": "dev",
    "location": "Oficina",
    "deployment": "planned",
    "type": "server",
}
INCLUDED = ScopeChoice(included=True)


def test_alta_incluye_el_activo_y_conserva_el_hostname(store):
    revoked = save_asset(
        store,
        "srv-nuevo",
        {"type": "laptop", "deployment": "deployed", "os": "macOS", "owner": "Luis",
         "encrypted": True, "usage": "Oficina", "hostname": "luis.local"},
        INCLUDED,
        NOW,
        create=True,
    )  # fmt: skip

    assert revoked is False
    assert assets_by_id(store)["srv-nuevo"]["hostname"] == "luis.local"
    assert "srv-nuevo" in system(store)["included_assets"]
    assert load_documents(store).integrity_errors == []


def test_alta_con_id_existente_se_rechaza_sin_escribir(store):
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected, match="ya existe"):
        save_asset(store, "srv-app", SERVER, INCLUDED, NOW, create=True)

    assert snapshot(store) == before


def test_edicion_de_un_activo_inexistente_se_rechaza(store):
    with pytest.raises(ScopeEditRejected, match="no existe"):
        save_asset(store, "fantasma", {"os": "x"}, None, NOW, create=False)


def test_edicion_cambia_un_campo_y_conserva_el_resto(store):
    original = assets_by_id(store)["srv-app"]

    save_asset(store, "srv-app", {"location": "Rack 2"}, None, NOW, create=False)

    assert assets_by_id(store)["srv-app"] == {**original, "location": "Rack 2"}


def test_edicion_con_none_borra_un_campo_opcional(store):
    save_asset(store, "lap-ana", {"hostname": "ana.local"}, None, NOW, create=False)
    assert assets_by_id(store)["lap-ana"]["hostname"] == "ana.local"

    save_asset(store, "lap-ana", {"hostname": None}, None, NOW, create=False)

    assert "hostname" not in assets_by_id(store)["lap-ana"]


def test_edicion_que_deja_sin_un_campo_obligatorio_se_rechaza_sin_escribir(store):
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected, match="necesita estos campos"):
        save_asset(store, "srv-app", {"function": None}, None, NOW, create=False)

    assert snapshot(store) == before


def test_el_id_no_se_cambia_por_edicion(store):
    save_asset(store, "srv-app", {"id": "otro", "os": "Debian 12"}, None, NOW, create=False)

    assert "otro" not in assets_by_id(store)
    assert assets_by_id(store)["srv-app"]["os"] == "Debian 12"


def test_excluir_con_justificacion_y_volver_a_incluir(store):
    save_asset(store, "srv-app", {}, ScopeChoice(False, "Se retira"), NOW, create=False)
    scope = system(store)
    assert "srv-app" not in scope["included_assets"]
    assert {"kind": "asset", "ref": "srv-app", "justification": "Se retira"} in scope["exclusions"]

    save_asset(store, "srv-app", {}, INCLUDED, NOW, create=False)

    scope = system(store)
    assert "srv-app" in scope["included_assets"]
    assert all(e["ref"] != "srv-app" for e in scope["exclusions"])


def test_exclusion_sin_justificacion_se_rechaza_sin_escribir(store):
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected, match="justification"):
        save_asset(store, "srv-app", {}, ScopeChoice(False, "  "), NOW, create=False)

    assert snapshot(store) == before


def test_hostname_repetido_se_rechaza_pero_un_error_previo_no_bloquea(store):
    taken = assets_by_id(store)["srv-app"]["hostname"]
    with pytest.raises(ScopeEditRejected, match="hostname repetido"):
        save_asset(store, "srv-otro", SERVER | {"hostname": taken}, INCLUDED, NOW, create=True)
    edit(store, "system", "  - bk-externo\n", "")  # error previo: activo sin decidir

    save_asset(store, "srv-app", {"location": "Rack 3"}, None, NOW, create=False)

    assert assets_by_id(store)["srv-app"]["location"] == "Rack 3"


def test_secreto_en_los_cambios_se_rechaza_sin_escribir(store):
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected, match="secreto"):
        save_asset(
            store, "srv-app", {"function": "ghp_" + "A1b2C3d4E5" * 4}, None, NOW, create=False
        )

    assert snapshot(store) == before


def test_secreto_en_la_justificacion_de_exclusion_se_rechaza_sin_escribir(store):
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected, match="secreto"):
        save_asset(
            store,
            "srv-app",
            {},
            ScopeChoice(False, "ghp_" + "A1b2C3d4E5" * 4),
            NOW,
            create=False,
        )

    assert snapshot(store) == before


def test_sin_cambios_efectivos_no_escribe_ni_revoca(store):
    approve(store)
    before = snapshot(store)
    original = assets_by_id(store)["srv-app"]

    revoked = save_asset(
        store, "srv-app", {"location": original["location"]}, INCLUDED, NOW, create=False
    )

    assert revoked is False
    assert snapshot(store) == before
    assert scope_status(store).state is ScopeState.APPROVED


def test_un_cambio_real_revoca_la_aprobacion(store):
    approve(store)

    revoked = save_asset(store, "srv-app", {"location": "Rack 2"}, None, NOW, create=False)

    assert revoked is True
    assert scope_status(store).state is not ScopeState.APPROVED


@pytest.mark.parametrize("fail_at", [1, 2])
def test_fallo_en_cada_escritura_deja_todo_como_estaba_y_no_revoca(store, fail_at):
    approve(store)
    before = snapshot(store)
    failing = FailingStore(store, fail_at)

    with pytest.raises(OSError):
        save_asset(failing, "srv-nuevo", SERVER, INCLUDED, NOW, create=True)

    assert snapshot(store) == before
    assert scope_status(store).state is ScopeState.APPROVED


@pytest.mark.parametrize("fail_at", [1, 2])
def test_fallo_en_cada_escritura_de_la_baja_deja_todo_como_estaba(store, fail_at):
    approve(store)
    before = snapshot(store)

    with pytest.raises(OSError):
        remove_asset(FailingStore(store, fail_at), "lap-ana", NOW)

    assert snapshot(store) == before
    assert scope_status(store).state is ScopeState.APPROVED


def _fail_on_approval(store, operation) -> None:
    """Ejecuta la operación con la aprobación vigente y la escritura de approval.yaml rota."""
    approve(store)
    before = snapshot(store)
    real = store.write_text

    def write(key: str, text: str) -> None:
        if key == "scope/approval.yaml" and "revoked" in text:
            raise OSError("disco lleno")
        real(key, text)

    store.write_text = write
    with pytest.raises(OSError):
        operation(store)
    del store.write_text
    assert snapshot(store) == before
    assert scope_status(store).state is ScopeState.APPROVED


def test_fallo_al_revocar_en_el_alta_restaura_activos_sistema_y_aprobacion(store):
    _fail_on_approval(
        store, lambda s: save_asset(s, "srv-nuevo", SERVER, INCLUDED, NOW, create=True)
    )


def test_fallo_al_revocar_en_la_edicion_restaura_activos_y_aprobacion(store):
    _fail_on_approval(
        store, lambda s: save_asset(s, "srv-app", {"location": "Rack 2"}, None, NOW, create=False)
    )


def test_fallo_al_revocar_en_la_baja_restaura_activos_sistema_y_aprobacion(store):
    _fail_on_approval(store, lambda s: remove_asset(s, "lap-ana", NOW))


def test_baja_quita_el_activo_del_inventario_y_del_alcance(store):
    revoked = remove_asset(store, "lap-ana", NOW)

    assert revoked is False
    assert "lap-ana" not in assets_by_id(store)
    assert "lap-ana" not in system(store)["included_assets"]


def test_baja_de_un_activo_excluido_quita_su_exclusion(store):
    remove_asset(store, "lap-personal", NOW)

    assert all(e["ref"] != "lap-personal" for e in system(store)["exclusions"])


def test_baja_revoca_la_aprobacion(store):
    approve(store)

    assert remove_asset(store, "lap-ana", NOW) is True


def test_baja_de_un_activo_inexistente_se_rechaza(store):
    with pytest.raises(ScopeEditRejected, match="no existe"):
        remove_asset(store, "fantasma", NOW)


@pytest.mark.parametrize(
    ("asset_id", "expected"),
    [
        ("srv-app", "el servicio svc-facturacion referencia el activo srv-app"),
        ("bk-diario", "la dependencia dep-backup-nube referencia el activo bk-diario"),
        ("prov-nube", "la dependencia dep-backup-nube referencia el activo prov-nube"),
    ],
)
def test_baja_de_un_activo_referenciado_se_rechaza_sin_escribir(store, asset_id, expected):
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected) as rejected:
        remove_asset(store, asset_id, NOW)

    assert any(expected in e for e in rejected.value.errors)
    assert snapshot(store) == before


def test_asset_references_es_pura_y_lista_todas(store):
    docs = load_documents(store)

    refs = asset_references(docs.services.model, docs.dependencies.model, "db-facturas")

    assert len(refs) == 2
    assert asset_references(None, None, "db-facturas") == []


@pytest.mark.parametrize("name", ["services", "dependencies"])
@pytest.mark.parametrize(
    "breaker",
    ["ilegible", "esquema", "yaml"],
)
def test_baja_cerrada_si_servicios_o_dependencias_no_son_fiables(store, name, breaker):
    key = f"scope/{name}.yaml"
    if breaker == "ilegible":
        (store._root / key).write_bytes(  # noqa: SLF001
            b"\xff\xfe\x00 no utf8 \xc3\x28"
        )
    elif breaker == "esquema":
        store.write_text(key, f"{name}: 3\n")
    else:
        store.write_text(key, f"{name}: [\n")
    before = snapshot(store)

    with pytest.raises(ScopeEditRejected) as rejected:
        remove_asset(store, "lap-ana", NOW)

    assert key in rejected.value.errors[0]
    assert snapshot(store) == before


@pytest.mark.parametrize("name", ["services", "dependencies"])
def test_baja_con_servicios_o_dependencias_ausentes_vale_como_vacio(store, name):
    (store._root / f"scope/{name}.yaml").unlink()  # noqa: SLF001

    remove_asset(store, "lap-ana", NOW)

    assert "lap-ana" not in assets_by_id(store)


def test_load_documents_separa_errores_por_documento(store):
    store.write_text("scope/services.yaml", "services: [\n")

    docs = load_documents(store)

    assert docs.assets.model is not None and docs.assets.errors == []
    assert docs.services.model is None and docs.services.errors
    assert docs.services.absent is False
    assert docs.integrity_errors == []


def test_load_documents_calcula_la_integridad_aparte(store):
    edit(store, "system", "  - bk-externo\n", "")

    docs = load_documents(store)

    assert docs.document_errors == []
    assert any("bk-externo" in e for e in docs.integrity_errors)
