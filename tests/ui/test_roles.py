"""Permisos de la UI: el fichero de roles, la CLI que lo escribe y su efecto en cada petición."""

import logging

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.ui.roles import (
    ROLES_KEY,
    Grant,
    Permissions,
    RolesFile,
    RolesFileInvalid,
    RolesStatus,
    dump_roles,
    load_roles,
    normalize_email,
    parse_roles,
)
from ens_evidence.ui.store import UiStore, WriteNotAllowed
from ens_evidence.workspace import LocalStore

from .conftest import Session, ui_client

EMAIL = "ana@example.com"
SO, SYSTEM_OWNER = Role.SECURITY_OFFICER, Role.SYSTEM_OWNER


def file_of(users: str) -> str:
    return f"users:\n{users}"


# --- lectura del fichero -------------------------------------------------------------------------


def test_parse_normaliza_el_correo_con_strip_y_casefold():
    text = file_of('  "  Ana@Example.COM ":\n    edit: true\n    roles: [security_officer]\n')

    assert parse_roles(text) == {EMAIL: Grant(True, frozenset({SO}))}


def test_parse_por_defecto_no_edita_y_no_tiene_roles():
    assert parse_roles(file_of("  ana@example.com: {}\n")) == {EMAIL: Grant()}


def test_parse_fichero_vacio_no_asigna_nada():
    assert parse_roles("") == {}


@pytest.mark.parametrize(
    "text",
    [
        file_of('  "": {edit: true}\n'),
        file_of('  "   ": {edit: true}\n'),
        file_of("  sin-arroba: {edit: true}\n"),
        file_of("  ana@example.com: {edit: true}\n  ana@example.com: {edit: false}\n"),
        file_of('  ana@example.com: {edit: true}\n  " ANA@example.com": {edit: false}\n'),
        file_of("  ana@example.com: {edit: 1}\n"),
        file_of("  ana@example.com: {edit: 'true'}\n"),
        file_of("  ana@example.com: {edit: yes please}\n"),
        file_of("  ana@example.com: {roles: [jefe_supremo]}\n"),
        file_of("  ana@example.com: {roles: security_officer}\n"),
        file_of("  ana@example.com: {edit: true, extra: 1}\n"),
        "users: []\n",
        "otra_clave: {}\n",
        "- nada\n",
        "users: {a@b.c: {edit: true}\n",
    ],
)
def test_parse_rechaza_lo_irregular(text):
    with pytest.raises(RolesFileInvalid):
        parse_roles(text)


def test_el_error_no_incluye_valores_del_fichero():
    with pytest.raises(RolesFileInvalid) as raised:
        parse_roles(file_of("  secreto@example.com: {roles: [valor-secreto]}\n"))

    assert "secreto" not in str(raised.value)


def test_dump_y_parse_son_inversos():
    users = {EMAIL: Grant(True, frozenset({SO, SYSTEM_OWNER})), "luis@example.com": Grant()}

    assert parse_roles(dump_roles(users)) == users


def test_la_identidad_se_normaliza_igual_que_el_fichero():
    roles = RolesFile(RolesStatus.OK, parse_roles(dump_roles({EMAIL: Grant(True)})))

    assert Permissions.of(roles, "  ANA@Example.com ").edit
    assert normalize_email(" ANA@Example.com ") == EMAIL


# --- fichero ausente o inválido: nadie edita -----------------------------------------------------


def remove_roles(tmp_path) -> None:
    (tmp_path / "ws" / ROLES_KEY).unlink()


def test_ausente_es_missing_y_nadie_edita(store, tmp_path):
    remove_roles(tmp_path)

    loaded = load_roles(store)

    assert loaded.status is RolesStatus.MISSING
    assert not loaded.grant_of(EMAIL).edit


def test_invalido_es_invalid_nadie_edita_y_se_registra(store, caplog):
    store.write_text(ROLES_KEY, "users: [esto no es un mapa")
    caplog.set_level(logging.WARNING, logger="ens_evidence.ui.roles")

    loaded = load_roles(store)

    assert loaded.status is RolesStatus.INVALID
    assert not loaded.grant_of(EMAIL).edit
    assert "roles_file=invalid" in caplog.text
    assert "esto no es" not in caplog.text


def banner_page(config, store, key, token, text, tmp_path) -> str:
    if text is None:
        remove_roles(tmp_path)
    else:
        store.write_text(ROLES_KEY, text)
    return Session(ui_client(config, store, key), token).get("/").text


def test_sin_fichero_el_aviso_global_se_ve(config, store, key, token, tmp_path):
    page = banner_page(config, store, key, token, None, tmp_path)

    assert "Todavía no hay roles asignados" in page
    assert "solo lectura" in page


def test_con_fichero_invalido_el_aviso_global_lo_dice(config, store, key, token, tmp_path):
    page = banner_page(config, store, key, token, "users: [roto", tmp_path)

    assert "El fichero de roles no es válido" in page


def test_sin_edicion_el_aviso_dice_que_solo_consulta(config, store, key, token, tmp_path):
    page = banner_page(config, store, key, token, dump_roles({EMAIL: Grant()}), tmp_path)

    assert "Tu usuario solo puede consultar" in page


def test_con_edicion_no_hay_aviso(config, store, key, token, tmp_path):
    page = banner_page(config, store, key, token, dump_roles({EMAIL: Grant(True)}), tmp_path)

    assert "solo puede consultar" not in page
    assert "Todavía no hay roles" not in page


# --- CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def ens(tmp_path):
    root = tmp_path / "cli-ws"
    root.mkdir()
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(root)}}})
    )
    runner = CliRunner()
    run = lambda *args: runner.invoke(app, ["--settings", str(settings), "ui", "roles", *args])  # noqa: E731
    run.store = LocalStore(root)
    return run


def assigned(ens) -> dict[str, Grant]:
    return parse_roles(ens.store.read_text(ROLES_KEY))


def test_list_sin_fichero_dice_que_todo_es_solo_lectura(ens):
    result = ens("list")

    assert result.exit_code == 0
    assert "solo lectura" in result.output


def test_grant_crea_el_fichero_y_list_lo_muestra(ens):
    assert ens("grant", " Ana@Example.com ", "--edit", "--role", "security_officer").exit_code == 0

    assert assigned(ens) == {EMAIL: Grant(True, frozenset({SO}))}
    listed = ens("list").output
    assert EMAIL in listed and "security_officer" in listed and "edición=sí" in listed


def test_grant_suma_a_lo_que_ya_tenia(ens):
    ens("grant", EMAIL, "--edit")
    ens("grant", EMAIL, "--role", "security_officer")
    ens("grant", EMAIL, "--role", "system_owner", "--role", "security_officer")

    assert assigned(ens) == {EMAIL: Grant(True, frozenset({SO, SYSTEM_OWNER}))}


def test_grant_sin_nada_que_conceder_falla(ens):
    assert ens("grant", EMAIL).exit_code != 0


@pytest.mark.parametrize("email", ["", "sin-arroba", "  "])
def test_grant_rechaza_un_correo_mal_formado_y_no_escribe(ens, email):
    assert ens("grant", email, "--edit").exit_code != 0
    assert not ens.store.exists(ROLES_KEY)


def test_grant_con_rol_desconocido_falla(ens):
    assert ens("grant", EMAIL, "--role", "jefe_supremo").exit_code != 0


def test_revoke_sin_opciones_quita_a_la_persona(ens):
    ens("grant", EMAIL, "--edit", "--role", "security_officer")
    ens("grant", "luis@example.com", "--edit")

    assert ens("revoke", EMAIL.upper()).exit_code == 0

    assert set(assigned(ens)) == {"luis@example.com"}


def test_revoke_role_quita_solo_ese_rol(ens):
    ens("grant", EMAIL, "--edit", "--role", "security_officer", "--role", "system_owner")

    ens("revoke", EMAIL, "--role", "security_officer")

    assert assigned(ens) == {EMAIL: Grant(True, frozenset({SYSTEM_OWNER}))}


def test_revoke_no_edit_quita_solo_la_edicion(ens):
    ens("grant", EMAIL, "--edit", "--role", "security_officer")

    ens("revoke", EMAIL, "--no-edit")

    assert assigned(ens) == {EMAIL: Grant(False, frozenset({SO}))}


def test_revoke_de_quien_no_tiene_nada_falla_y_no_escribe(ens):
    ens("grant", "luis@example.com", "--edit")
    before = ens.store.read_bytes(ROLES_KEY)

    assert ens("revoke", EMAIL).exit_code != 0
    assert ens.store.read_bytes(ROLES_KEY) == before


def test_con_el_fichero_invalido_la_cli_no_lo_pisa(ens):
    ens.store.write_text(ROLES_KEY, "users: [roto")

    for args in (("grant", EMAIL, "--edit"), ("revoke", EMAIL), ("list",)):
        assert ens(*args).exit_code != 0
    assert ens.store.read_text(ROLES_KEY) == "users: [roto"


# --- efecto en la petición siguiente -------------------------------------------------------------


def test_la_revocacion_se_aplica_en_la_peticion_siguiente(config, store, key, token):
    app_session = Session(ui_client(config, store, key), token)
    store.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(True)}))
    body = {"hostname": "x"}

    assert app_session.post("/incidentes/nuevo", body).status_code != 403
    store.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(False)}))

    assert app_session.post("/incidentes/nuevo", body).status_code == 403


def test_la_concesion_se_aplica_en_la_peticion_siguiente(config, store, key, token):
    store.write_text(ROLES_KEY, dump_roles({}))
    app_session = Session(ui_client(config, store, key), token)

    assert app_session.post("/incidentes/nuevo", {}).status_code == 403
    store.write_text(ROLES_KEY, dump_roles({EMAIL: Grant(True)}))

    assert app_session.post("/incidentes/nuevo", {}).status_code != 403


def test_la_ui_no_puede_escribir_el_fichero_de_roles(store):
    with pytest.raises(WriteNotAllowed):
        UiStore(store).write_text(ROLES_KEY, dump_roles({EMAIL: Grant(True)}))
