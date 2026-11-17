"""Solo lectura de verdad: sin edición los formularios se pintan inactivos y se explica por qué."""

import re

import pytest

from ens_evidence.models.enums import Role
from ens_evidence.ui.pages import code_spans, roles_notices
from ens_evidence.ui.roles import ROLES_KEY, Grant, Permissions, RolesStatus, dump_roles

EMAIL = "ana@example.com"
FORM_PAGES = [
    "/asistente/sistema",
    "/asistente/activos",
    "/asistente/aprobacion-alcance",
    "/evidencias/nueva",
    "/incidentes/nuevo",
    "/herramientas/copias",
    "/herramientas/claves",
    "/herramientas/contrasenas",
    "/herramientas/claves/custodios",
]
POST_FORM = re.compile(
    r'<form method="post"[^>]*>(?:\s*<p class="muted">Solo lectura[^<]*</p>)?'
    r'\s*<fieldset class="gate"( disabled)?'
)


def grant(store, edit: bool, *roles: Role) -> None:
    store.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(edit, frozenset(roles))}))


@pytest.fixture
def reader(example_store):
    grant(example_store, False)


def gates(page: str) -> list[bool]:
    """Para cada formulario que escribe, si su `fieldset` de guardia está desactivado."""
    return [bool(m.group(1)) for m in POST_FORM.finditer(page)]


@pytest.mark.parametrize("path", FORM_PAGES)
def test_sin_edicion_todo_formulario_que_escribe_queda_inactivo(session, reader, path):
    page = session.get(path).text

    assert page.count('<form method="post"') == len(gates(page)) > 0
    assert all(gates(page))
    assert "Solo lectura: para editar hace falta permiso de edición." in page


def test_sin_edicion_la_ficha_de_un_conector_no_ofrece_formulario_y_lo_explica(session, reader):
    page = session.get("/conectores/github").text

    assert 'method="post"' not in page
    assert "Solo lectura: para configurar este conector hace falta permiso de edición." in page


@pytest.mark.parametrize("path", ["/asistente/sistema", "/evidencias/nueva", "/incidentes/nuevo"])
def test_con_edicion_los_formularios_estan_activos(session, example_store, path):
    grant(example_store, True)

    page = session.get(path).text

    assert gates(page) and not any(gates(page))
    assert "Solo lectura:" not in page


def test_el_aviso_de_rol_da_el_valor_exacto_de_la_orden(session, example_store):
    grant(example_store, True)

    page = session.get("/asistente/aprobacion-alcance").text

    assert "--role system_owner" in page
    assert "--role …" not in page
    assert all(gates(page))  # editor sin el rol: formulario de firma inactivo (PROD-3)


def test_preauditoria_anuncia_el_rol_que_exige(session, example_store):
    grant(example_store, True)

    page = session.get("/preauditoria").text

    assert "--role security_officer" in page
    assert "Responsable de la seguridad" in page


def test_preauditoria_con_el_rol_no_avisa(session, example_store):
    grant(example_store, True, Role.SECURITY_OFFICER)

    assert "Necesitas el rol" not in session.get("/preauditoria").text


def test_custodios_sin_rol_de_revision_lo_anuncia_y_deshabilita(session, example_store):
    grant(example_store, True)

    page = session.get("/herramientas/claves/custodios").text

    assert "--role system_administrator" in page
    assert "--role system_administrator_delegate" in page
    assert all(gates(page))


def test_custodios_con_rol_de_revision_queda_activo(session, example_store):
    grant(example_store, True, Role.SYSTEM_ADMINISTRATOR)

    page = session.get("/herramientas/claves/custodios").text

    assert not any(gates(page))
    assert "Necesitas el rol" not in page


# --- PROD-7: código en línea sin acentos graves literales ----------------------------------------


def test_code_spans_pinta_codigo_y_escapa_todo():
    html = str(code_spans("usa `ens-evidence <x>` y <b>no</b>"))

    assert html == ('usa <span class="mono">ens-evidence &lt;x&gt;</span> y &lt;b&gt;no&lt;/b&gt;')


def test_code_spans_con_un_acento_sin_cerrar_no_inventa_codigo():
    assert str(code_spans("a ` b <i>")) == "a ` b &lt;i&gt;"


@pytest.mark.parametrize("status", [RolesStatus.MISSING, RolesStatus.INVALID, RolesStatus.OK])
def test_los_avisos_remiten_al_runbook(status):
    (notice,) = roles_notices(Permissions(status=status))

    assert "docs/runbooks/interfaz-web.md" in notice["how"]


def test_la_pagina_no_muestra_acentos_graves_literales_ni_en_el_aviso_ni_en_el_403(session, reader):
    page = session.get("/").text
    assert 'class="mono">ens-evidence ui roles grant' in page
    assert "`" not in page

    denied = session.post("/evidencias/nueva", {})
    assert denied.status_code == 403
    assert "`" not in denied.text
    assert 'class="mono">ens-evidence ui roles grant' in denied.text


def test_el_403_por_rol_da_el_valor_del_rol(session, example_store):
    grant(example_store, True)

    denied = session.post("/asistente/aprobacion-alcance", {"accion": "aprobar"})

    assert denied.status_code == 403
    assert "--role system_owner" in denied.text
    assert "`" not in denied.text
