"""Registro de custodios del almacén de claves en la web."""

from datetime import timedelta

import pytest

from ens_evidence.evidence.importer import import_collected
from ens_evidence.kit.custody import CUSTODIANS_PREFIX, latest_register
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus

from .conftest import NOW

URL = "/herramientas/claves/custodios"
FORM = {
    "asset_id": "app-facturas",
    "key_shares": "5",
    "key_threshold": "3",
    "custodian_name_0": "Ana Pérez",
    "custodian_since_0": "2026-11-01",
    "custodian_name_1": "Luis Gómez",
    "custodian_since_1": "2026-11-02",
    "reviewer": "Marta Ruiz",
    "role": "security_officer",
    "reviewed_on": "2026-11-07",
    "valid_until_on": "2027-11-07",
}


def form_of(html: str) -> str:
    """El formulario sin la cabecera, que sí muestra quién ha entrado."""
    return html.split('<form method="post"', 1)[1]


def seal_evidence(store, n: int, t: int) -> None:
    result = CollectionResult(
        source="keystore",
        collector="keystore",
        collected_at=NOW - timedelta(hours=1),
        asset_id="app-facturas",
        data={
            "sections": {
                "seal": {"summary": {"initialized": True, "sealed": False, "n": n, "t": t}}
            }
        },
        collection_status=CollectionStatus.SUCCESS,
    )
    import_collected(store, result, collector_version="1", now=NOW)


def test_el_formulario_llega_vacio_y_sin_el_email_de_access(session):
    html = session.get(URL).text

    assert "ana@example.com" in html
    assert "ana@example.com" not in form_of(html)
    assert 'name="reviewer" value=""' in html
    assert 'name="custodian_name_0" value=""' in html
    assert "La herramienta no guarda las partes de desbloqueo, ni su número ni su huella" in html
    assert "Tu nombre completo. Queda registrado como lo declaras." in html


def test_el_formulario_no_prellena_tras_un_error_con_el_email(session):
    html = session.post(URL, {**FORM, "reviewer": ""}).text

    assert "ana@example.com" not in form_of(html)


@pytest.mark.parametrize("name", ["ana@example.com", "12345678Z", "password: hunter2hunter2"])
def test_nombres_con_email_dni_o_secreto_dan_422(session, example_store, name):
    response = session.post(URL, {**FORM, "custodian_name_0": name})

    assert response.status_code == 422
    assert name not in form_of(response.text)
    assert example_store.list(CUSTODIANS_PREFIX) == []


def test_revisor_con_email_da_422(session):
    response = session.post(URL, {**FORM, "reviewer": "marta@example.com"})

    assert response.status_code == 422 and "marta@example.com" not in form_of(response.text)


def test_umbral_mayor_que_partes_da_422(session):
    assert session.post(URL, {**FORM, "key_shares": "2", "key_threshold": "3"}).status_code == 422


def test_activo_fuera_del_inventario_da_422(session):
    assert session.post(URL, {**FORM, "asset_id": "no-existe"}).status_code == 422


def test_guarda_el_registro_y_la_evidencia_manual_del_activo(session, example_store):
    response = session.post(URL, FORM)

    assert response.status_code == 303 and response.headers["location"] == f"{URL}?guardado=1"
    register, _ = latest_register(example_store, NOW)
    assert register is not None and register.key_threshold == 3
    manifests = [example_store.read_text(k) for k in example_store.list("evidence/manifests")]
    assert len(manifests) == 1
    assert "asset_id: app-facturas" in manifests[0] and "Marta Ruiz" in manifests[0]
    page = session.get(f"{URL}?guardado=1").text
    assert "Guardado" in page and "Ana Pérez" in page


def test_no_sobrescribe_un_registro_existente(session, example_store):
    session.post(URL, FORM)
    before = example_store.list("evidence")

    response = session.post(URL, FORM)

    assert response.status_code == 409
    assert example_store.list("evidence") == before


def test_avisa_si_n_y_m_no_casan_con_openbao(session, example_store):
    seal_evidence(example_store, n=3, t=2)
    session.post(URL, FORM)

    html = session.get(URL).text

    assert "N no casa" in html and "M no casa" in html


def test_sin_aviso_si_casan(session, example_store):
    seal_evidence(example_store, n=5, t=3)
    session.post(URL, FORM)

    assert "no casa" not in session.get(URL).text


def test_el_activo_se_propone_con_el_de_la_ultima_evidencia_del_conector(session, example_store):
    seal_evidence(example_store, n=5, t=3)

    assert 'value="app-facturas" selected' in session.get(URL).text


def test_la_tarjeta_de_claves_enlaza_al_registro(session):
    assert f'href="{URL}"' in session.get("/herramientas/claves").text
