import pytest
import yaml

from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.wizard import STEPS

from .conftest import NOW, Session, snapshot_of, ui_client

TOKEN = "ghp_" + "A1b2C3d4E5" * 4
LAPTOP = {
    "asset_type": "laptop",
    "asset_id": "lap-nuevo",
    "asset_os": "macOS",
    "asset_owner": "Ana Ruiz",
    "asset_encrypted": "sí",
    "asset_usage": "Trabajo",
    "accion": "anadir",
}


def system(store) -> dict:
    return yaml.safe_load(store.read_text("scope/system.yaml"))


@pytest.mark.parametrize("slug", [s.slug for s in STEPS])
def test_every_step_renders(session, slug):
    response = session.get(f"/asistente/{slug}")

    assert response.status_code == 200
    assert "ana@example.com" in response.text or "<h2>" in response.text
    assert "<h2>" in response.text


def test_wizard_index_redirects_to_first_pending_step(session):
    response = session.get("/asistente")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/asistente/")


def test_unknown_step_is_404(session):
    assert session.get("/asistente/nope").status_code == 404


def test_sistema_saves_and_redirects(session, example_store):
    response = session.post(
        "/asistente/sistema", {"name": "Mi sistema", "description": "Describe todo"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/asistente/sistema?guardado=1"
    assert system(example_store)["name"] == "Mi sistema"
    assert "Guardado" in session.get(response.headers["location"]).text


def test_primer_guardado_en_un_workspace_vacio_crea_el_esqueleto_de_init(
    store, config, key, token, tmp_path
):
    from ens_evidence.scope.skeleton import create_scope_skeleton
    from ens_evidence.workspace import LocalStore

    reference = LocalStore(tmp_path / "init")
    (tmp_path / "init").mkdir()
    create_scope_skeleton(reference)
    assert store.list("") == []
    fresh = Session(ui_client(config, store, key), token)

    response = fresh.post("/asistente/sistema", {"name": "Mi sistema", "description": "Todo"})

    assert response.status_code == 303
    assert set(store.list("")) == set(reference.list(""))
    assert system(store)["name"] == "Mi sistema"
    assert store.read_text("scope/assets.yaml") == reference.read_text("scope/assets.yaml")


def test_sistema_errors_rerender_without_writing(session, example_store):
    before = snapshot_of(example_store)

    response = session.post("/asistente/sistema", {"name": "", "description": "x"})

    assert response.status_code == 422
    assert "No se ha guardado" in response.text
    assert 'id="err-name"' in response.text
    assert snapshot_of(example_store) == before


def test_htmx_returns_fragment_with_200_even_on_errors(session):
    response = session.post("/asistente/sistema", {"name": "", "description": ""}, htmx=True)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert "No se ha guardado" in response.text


def test_htmx_success_shows_saved(session):
    response = session.post("/asistente/sistema", {"name": "A", "description": "B"}, htmx=True)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Guardado" in response.text


def test_activos_includes_an_existing_asset(session, example_store):

    from ens_evidence.scope.edit import add_asset

    laptop = {
        "id": "lap-nuevo", "type": "laptop", "deployment": "deployed",
        "os": "macOS", "owner": "Ana", "encrypted": True, "usage": "Trabajo",
    }  # fmt: skip
    add_asset(example_store, laptop, False, NOW)
    included = system(example_store)["included_assets"]

    response = session.post("/asistente/activos", {"included_assets": [*included, "lap-nuevo"]})

    assert response.status_code == 303
    assert "lap-nuevo" in system(example_store)["included_assets"]
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED


def test_activos_cannot_remove_from_scope(session, example_store):
    before = snapshot_of(example_store)

    response = session.post("/asistente/activos", {"included_assets": ["lap-ana"]})

    assert response.status_code == 422
    assert 'id="err-included_assets"' in response.text
    assert snapshot_of(example_store) == before


def test_activos_adds_new_asset_included(session, example_store):
    response = session.post("/asistente/activos", LAPTOP)

    assert response.status_code == 303
    assets = yaml.safe_load(example_store.read_text("scope/assets.yaml"))["assets"]
    new = next(a for a in assets if a["id"] == "lap-nuevo")
    assert new["encrypted"] is True
    assert "lap-nuevo" in system(example_store)["included_assets"]


def test_activos_type_form_lists_required_fields(session):
    response = session.get("/asistente/activos?tipo=laptop")

    assert 'name="asset_os"' in response.text
    assert 'name="asset_encrypted"' in response.text


def test_activos_rejects_secret_without_writing(session, example_store):
    before = snapshot_of(example_store)

    response = session.post("/asistente/activos", {**LAPTOP, "asset_usage": TOKEN})

    assert response.status_code == 422
    assert "secreto" in response.text
    assert TOKEN not in response.text
    assert snapshot_of(example_store) == before


def test_activos_rejects_unknown_type_and_duplicate_id(session, example_store):
    before = snapshot_of(example_store)

    unknown = session.post("/asistente/activos", {**LAPTOP, "asset_type": "toaster"})
    duplicate = session.post("/asistente/activos", {**LAPTOP, "asset_id": "lap-ana"})

    assert unknown.status_code == duplicate.status_code == 422
    assert snapshot_of(example_store) == before


def test_servicios_adds_and_includes(session, example_store):
    response = session.post(
        "/asistente/servicios",
        {
            "included_services": ["svc-facturacion"],
            "service_id": "svc-soporte",
            "service_name": "Soporte",
            "service_assets": ["lap-ana", "srv-app"],
        },
    )

    assert response.status_code == 303
    assert "svc-soporte" in system(example_store)["included_services"]
    services = yaml.safe_load(example_store.read_text("scope/services.yaml"))["services"]
    assert next(s for s in services if s["id"] == "svc-soporte")["assets"] == ["lap-ana", "srv-app"]


def test_servicios_incomplete_new_service_reports_each_field(session, example_store):
    before = snapshot_of(example_store)

    response = session.post(
        "/asistente/servicios",
        {"included_services": ["svc-facturacion"], "service_id": "svc-x"},
    )

    assert response.status_code == 422
    assert 'id="err-service_name"' in response.text
    assert 'id="err-service_assets"' in response.text
    assert snapshot_of(example_store) == before


def test_dependencias_adds_and_empty_form_is_noop(session, example_store):
    before = snapshot_of(example_store)
    partial = session.post("/asistente/dependencias", {"asset": "lap-ana"})
    assert partial.status_code == 422
    assert 'id="err-dependency_id"' in partial.text
    empty = session.post("/asistente/dependencias", {"purpose": ""})
    assert empty.status_code == 303 and "guardado" not in empty.headers["location"]
    assert snapshot_of(example_store) == before

    response = session.post(
        "/asistente/dependencias",
        {"dependency_id": "dep-x", "asset": "lap-ana", "depends_on": "srv-app", "purpose": "SSH"},
    )

    assert response.status_code == 303
    deps = yaml.safe_load(example_store.read_text("scope/dependencies.yaml"))["dependencies"]
    assert next(d for d in deps if d["id"] == "dep-x")["purpose"] == "SSH"


def test_aprobacion_alcance_leaves_the_name_empty_and_approves(session, example_store):
    page = session.get("/asistente/aprobacion-alcance")
    assert 'name="approved_by" value=""' in page.text

    response = session.post(
        "/asistente/aprobacion-alcance", {"approved_by": "Ana Ruiz", "accion": "aprobar"}
    )

    assert response.status_code == 303
    status = scope_status(example_store)
    assert status.state is ScopeState.APPROVED
    assert status.approval.approved_by == "Ana Ruiz"
    assert status.approval.approved_at == NOW


def test_aprobacion_alcance_requires_a_name(session, example_store):
    response = session.post(
        "/asistente/aprobacion-alcance", {"approved_by": " ", "accion": "aprobar"}
    )

    assert response.status_code == 422
    assert scope_status(example_store).state is ScopeState.NOT_APPROVED


def test_aprobacion_alcance_with_invalid_inventory_is_rejected(session, example_store):
    example_store.write_text("scope/services.yaml", "services:\n  - id: s\n")
    before = snapshot_of(example_store)

    page = session.get("/asistente/aprobacion-alcance")
    response = session.post(
        "/asistente/aprobacion-alcance", {"approved_by": "Ana", "accion": "aprobar"}
    )

    assert "Antes de aprobar" in page.text
    assert response.status_code == 422
    assert snapshot_of(example_store) == before


def test_oversized_form_is_rejected(session, example_store):
    before = snapshot_of(example_store)

    response = session.post("/asistente/sistema", {"name": "x" * 300_000, "description": "y"})

    assert response.status_code == 413
    assert snapshot_of(example_store) == before


def test_dependencias_es_opcional_con_el_alcance_aprobado(completed):
    # Con el alcance y la categorización hechos, el asistente sigue por los riesgos.
    assert completed.get("/asistente").headers["location"] == "/asistente/riesgos-metodologia"
    page = completed.get("/asistente/sistema").text
    assert "Dependencias (opcional)" in page


def test_dependencias_guardar_con_los_desplegables_por_defecto_no_es_error(session, example_store):
    before = snapshot_of(example_store)

    response = session.post(
        "/asistente/dependencias",
        {"dependency_id": "", "asset": "", "depends_on": "", "purpose": ""},
    )

    assert response.status_code == 303 and "guardado" not in response.headers["location"]
    assert snapshot_of(example_store) == before
    page = session.get("/asistente/dependencias").text
    assert page.count('<option value="">') == 2


def test_guardar_sin_cambios_no_dice_guardado(session):
    response = session.post("/asistente/aprobacion-alcance", {"accion": "guardar"})

    assert response.status_code == 303
    assert "guardado" not in response.headers["location"]
    assert "Guardar" not in session.get("/asistente/aprobacion-alcance").text


def test_activo_con_espacios_y_booleano_invalido_dan_error_de_campo(session):
    response = session.post(
        "/asistente/activos",
        {
            "accion": "anadir", "asset_type": "laptop", "asset_id": "mi portatil",
            "asset_os": "macOS", "asset_owner": "Ana", "asset_encrypted": "quizá",
            "asset_usage": "x",
        },
    )  # fmt: skip

    assert response.status_code == 422
    assert 'id="err-asset_id"' in response.text
    assert 'id="err-asset_encrypted"' in response.text
    assert "encrypted:" not in response.text


def test_lo_ya_incluido_no_se_puede_desmarcar(session):
    page = session.get("/asistente/activos").text

    assert "checked disabled" in page
    assert 'type="hidden" name="included_assets"' in page
