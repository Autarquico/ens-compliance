"""Los logs no contienen JWT, cabeceras Cf-Access ni cuerpos de formulario."""

import logging

import pytest

from .conftest import WRITE_HEADERS, auth, mint
from .test_hostile import ALL_PAGES, STEP_PATHS

EMAIL = "custodio.secreto@example.com"
BODY_MARKER = "CUERPO-DE-FORMULARIO-SECRETO-7431"


def all_logged_text(caplog) -> str:
    return "\n".join(
        f"{r.name} {r.getMessage()} {r.exc_text or ''} {r.args!r}" for r in caplog.records
    )


@pytest.fixture
def traffic(raw_client, key, token, other_key):
    """Tráfico de todo tipo: correcto, rechazado, hostil y con errores."""

    def run() -> None:
        good = auth(token)
        for path in ALL_PAGES:
            raw_client.get(path, headers=good, follow_redirects=True)
        form = {"name": BODY_MARKER, "description": BODY_MARKER, "approved_by": BODY_MARKER}
        for path in STEP_PATHS:
            raw_client.post(path, data=form, headers={**good, **WRITE_HEADERS})
            raw_client.post(
                path, data=form, headers={**good, **WRITE_HEADERS, "HX-Request": "true"}
            )
            raw_client.post(path, data=form, headers=WRITE_HEADERS)
            raw_client.post(path, data=form, headers=good)
        for bad in (mint(other_key), mint(key, exp=1), mint(key, alg="none"), "x.y.z"):
            raw_client.get("/", headers=auth(bad))
            raw_client.post("/asistente/sistema", data=form, headers={**auth(bad), **WRITE_HEADERS})
        raw_client.post(
            "/asistente/sistema",
            content=BODY_MARKER.encode() * 50_000,
            headers={**good, **WRITE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )

    return run


def test_los_logs_no_llevan_jwt_cabeceras_de_access_ni_cuerpos(caplog, traffic, token, key):
    caplog.set_level(logging.DEBUG)

    traffic()

    logged = all_logged_text(caplog)
    assert token not in logged
    assert token.split(".")[1] not in logged
    assert token.split(".")[2] not in logged
    assert "cf-access" not in logged.lower()
    assert BODY_MARKER not in logged


def test_los_logs_no_llevan_las_claves_del_asistente_de_copias_ni_en_los_errores(
    caplog, raw_client, token
):
    from .test_tools import FORM, SENTINELS

    caplog.set_level(logging.DEBUG)
    good = {**auth(token), **WRITE_HEADERS}

    raw_client.post("/herramientas/copias", data=FORM, headers=good)
    raw_client.post("/herramientas/copias", data=FORM | {"host_paths": "/proc"}, headers=good)
    raw_client.post("/herramientas/copias", data=FORM | {"retention_days": "x"}, headers=good)
    raw_client.post("/herramientas/copias", data=FORM, headers=auth(token))
    raw_client.post("/herramientas/copias", data=FORM, headers=WRITE_HEADERS)

    logged = all_logged_text(caplog)
    assert not any(secret in logged for secret in SENTINELS)


def test_los_logs_no_llevan_nada_del_asistente_de_claves_ni_de_custodios(caplog, raw_client, token):
    from .test_custody import FORM as CUSTODIOS_FORM
    from .test_tools_claves import FORM as CLAVES_FORM

    caplog.set_level(logging.DEBUG)
    good = {**auth(token), **WRITE_HEADERS}
    names = ("Ana Pérez", "Luis Gómez", "Marta Ruiz", EMAIL)

    raw_client.post("/herramientas/claves", data=CLAVES_FORM, headers=good)
    raw_client.post("/herramientas/claves", data=CLAVES_FORM | {"key_threshold": "9"}, headers=good)
    raw_client.post("/herramientas/claves/custodios", data=CUSTODIOS_FORM, headers=good)
    raw_client.post(
        "/herramientas/claves/custodios",
        data=CUSTODIOS_FORM | {"custodian_name_0": EMAIL},
        headers=good,
    )
    raw_client.post("/herramientas/claves/custodios", data=CUSTODIOS_FORM, headers=auth(token))
    raw_client.post("/herramientas/claves/custodios", data=CUSTODIOS_FORM, headers=WRITE_HEADERS)

    logged = all_logged_text(caplog)
    assert not any(name in logged for name in names)
