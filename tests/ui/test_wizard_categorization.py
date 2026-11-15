import pytest
import yaml

from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization import (
    CATEGORIZATION_KEY,
    CategorizationState,
    categorization_status,
)
from ens_evidence.models.enums import Role
from ens_evidence.scope import approve_scope

from .conftest import NOW, snapshot_of

JUSTIFICATION = "Criterio 1.º del anexo I, 3"


@pytest.fixture
def approved(example_store):
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    return example_store


def document(store) -> dict:
    return yaml.safe_load(store.read_text(CATEGORIZATION_KEY))


def rating_fields(kind: str, element: str, level: str = "MEDIO", only=tuple(Dimension)) -> dict:
    fields = {}
    for d in only:
        base = f"rating~{kind}~{element}~{d.value}"
        fields[f"{base}~level"] = level
        fields[f"{base}~justification"] = JUSTIFICATION
    return fields


def add_info(session, info_id="datos", description="Datos de clientes"):
    return session.post("/asistente/informacion", {"info_id": info_id, "description": description})


def test_informacion_needs_an_approved_scope(session, example_store):
    before = snapshot_of(example_store)

    response = add_info(session)

    assert response.status_code == 422
    assert "alcance no está aprobado" in response.text
    assert snapshot_of(example_store) == before


def test_informacion_inits_categorization_and_adds(session, approved):
    response = add_info(session)

    assert response.status_code == 303
    doc = document(approved)
    assert [i["id"] for i in doc["information"]] == ["datos"]
    assert [s["id"] for s in doc["services"]] == ["svc-facturacion"]
    page = session.get("/asistente/informacion")
    assert "Datos de clientes" in page.text


def test_informacion_requires_both_fields(session, approved):
    response = session.post("/asistente/informacion", {"info_id": "x", "description": ""})

    assert response.status_code == 422
    assert 'id="err-description"' in response.text


def test_valoraciones_lists_elements_with_an_empty_signer(session, approved):
    add_info(session)

    page = session.get("/asistente/valoraciones")

    assert 'name="rating~information~datos~C~level"' in page.text
    assert 'name="rating~service~svc-facturacion~D~justification"' in page.text
    assert 'name="valued_by~information~datos" value=""' in page.text


def test_valoraciones_saves_ratings_and_signs_complete_elements(session, approved):
    add_info(session)
    form = {
        **rating_fields("information", "datos", "ALTO"),
        **rating_fields("service", "svc-facturacion", "BAJO", only=(Dimension.C, Dimension.I)),
        "valued_by~information~datos": "Ana Ruiz",
        "valued_by~service~svc-facturacion": "Ana Ruiz",
    }

    response = session.post("/asistente/valoraciones", form)

    assert response.status_code == 303
    doc = document(approved)
    info, service = doc["information"][0], doc["services"][0]
    assert set(info["ratings"]) == {d.value for d in Dimension}
    assert info["valuation"] == {
        "valued_by": "Ana Ruiz",
        "role": "information_owner",
        "valued_at": NOW.isoformat(),
    }
    assert set(service["ratings"]) == {"C", "I"}
    assert "valuation" not in service


def test_valoraciones_field_errors_write_nothing(session, approved):
    add_info(session)
    before = snapshot_of(approved)
    form = {
        **rating_fields("information", "datos"),
        "rating~information~datos~C~level": "ENORME",
        "rating~information~datos~I~justification": "",
        "rating~service~svc-facturacion~T~justification": "sin nivel",
    }

    response = session.post("/asistente/valoraciones", form)

    assert response.status_code == 422
    for name in (
        "rating~information~datos~C~level",
        "rating~information~datos~I~justification",
        "rating~service~svc-facturacion~T~level",
    ):
        assert f'id="err-{name}"' in response.text
    assert snapshot_of(approved) == before


def test_valoraciones_unchanged_form_is_idempotent(session, approved):
    add_info(session)
    form = {**rating_fields("information", "datos"), "valued_by~information~datos": "Ana"}
    session.post("/asistente/valoraciones", form)
    before = snapshot_of(approved)

    response = session.post("/asistente/valoraciones", form)

    assert response.status_code == 303
    assert snapshot_of(approved) == before


def complete(session):
    add_info(session)
    form = {"valued_by~information~datos": "Ana", "valued_by~service~svc-facturacion": "Ana"}
    form |= rating_fields("information", "datos") | rating_fields("service", "svc-facturacion")
    assert session.post("/asistente/valoraciones", form).status_code == 303


def test_aprobacion_categorizacion_blocked_while_pending(session, approved):
    add_info(session)
    before = snapshot_of(approved)

    page = session.get("/asistente/aprobacion-categorizacion")
    response = session.post(
        "/asistente/aprobacion-categorizacion", {"approved_by": "Marta", "accion": "aprobar"}
    )

    assert "disabled" in page.text
    assert response.status_code == 422
    assert snapshot_of(approved) == before


def test_aprobacion_categorizacion_approves(session, approved):
    complete(session)

    response = session.post(
        "/asistente/aprobacion-categorizacion", {"approved_by": "Marta Gil", "accion": "aprobar"}
    )

    assert response.status_code == 303
    status = categorization_status(approved, NOW)
    assert status.state is CategorizationState.CURRENT
    assert status.approval.approved_by == "Marta Gil"
    assert status.approval.role is Role.SECURITY_OFFICER
    assert "Marta Gil" in session.get("/asistente/aprobacion-categorizacion").text


def test_editing_after_approval_leaves_it_stale(session, approved):
    complete(session)
    session.post(
        "/asistente/aprobacion-categorizacion", {"approved_by": "Marta", "accion": "aprobar"}
    )

    session.post(
        "/asistente/valoraciones",
        rating_fields("information", "datos", "ALTO", only=(Dimension.C,)),
    )

    assert categorization_status(approved, NOW).state is CategorizationState.STALE
