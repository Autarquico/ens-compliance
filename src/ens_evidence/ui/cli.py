"""`ens-evidence ui serve`: el servicio web, detrás de Cloudflare Access.

Los imports pesados (extra `ui`) están dentro del comando para que la CLI base funcione sin él.
"""

import logging
from typing import Annotated

import typer

from ens_evidence.models.enums import Role
from ens_evidence.ui.roles import (
    ROLES_KEY,
    Grant,
    RolesFileInvalid,
    dump_roles,
    normalize_email,
    parse_roles,
)
from ens_evidence.workspace import NotFound, WorkspaceError

ui_app = typer.Typer(help="Interfaz web (ADR 0021).", no_args_is_help=True)
roles_app = typer.Typer(
    help="Quién edita y qué roles ENS tiene cada persona en la interfaz (solo desde aquí).",
    no_args_is_help=True,
)
ui_app.add_typer(roles_app, name="roles")


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(2)


@ui_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Dirección de escucha."),
    port: int = typer.Option(8000, min=1, max=65535, help="Puerto de escucha."),
) -> None:
    """Sirve la UI; la autenticación la hace el JWT de Access en cada petición."""
    from datetime import UTC, datetime
    from functools import partial

    try:
        import uvicorn

        from ens_evidence.ui.access import load_jwks_file
        from ens_evidence.ui.app import create_ui_app
        from ens_evidence.ui.config import UiConfigError, load_ui_config
        from ens_evidence.ui.routes import ui_routes
    except ImportError:
        raise _fail("falta el extra ui: instala ens-evidence[ui]") from None
    from ens_evidence.workspace import (
        SettingsError,
        load_settings,
        open_store,
        resolve_settings_path,
    )

    try:
        config = load_ui_config()
        settings = load_settings(resolve_settings_path())
    except (UiConfigError, SettingsError) as exc:
        raise _fail(str(exc)) from None
    if settings.workspace.backend != "local":
        raise _fail("la UI solo trabaja sobre un workspace local")
    app = create_ui_app(
        config,
        open_store(settings),
        partial(datetime.now, UTC),
        partial(load_jwks_file, config.jwks_file),
        routes=ui_routes(),
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        app,
        host=host,
        port=port,
        server_header=False,
        access_log=False,
        proxy_headers=False,
    )


EmailArg = Annotated[str, typer.Argument(help="Correo con el que la persona entra por Access.")]


def _cli():
    import ens_evidence.cli as cli  # import tardío: cli.py monta este grupo

    return cli


def _read_users(store) -> dict[str, Grant]:
    """Lo asignado; ausente es vacío, ilegible aborta para no pisarlo."""
    try:
        return parse_roles(store.read_text(ROLES_KEY))
    except NotFound:
        return {}
    except (RolesFileInvalid, WorkspaceError):
        raise _fail(
            f"{ROLES_KEY} no es válido; corrígelo a mano antes de usar este comando."
        ) from None


def _clean_email(raw: str) -> str:
    email = normalize_email(raw)
    try:
        parse_roles(dump_roles({email: Grant()}))
    except RolesFileInvalid:
        raise _fail("El correo no es válido.") from None
    return email


def _describe(email: str, grant: Grant) -> str:
    roles = ", ".join(sorted(r.value for r in grant.roles)) or "-"
    return f"{email}  edición={'sí' if grant.edit else 'no'}  roles={roles}"


def _update(ctx: typer.Context, change) -> dict[str, Grant]:
    """Lee, aplica `change` y escribe, con el lock del ciclo que toman los demás ajustes."""
    cli = _cli()
    settings = cli.resolved_settings(ctx)
    try:
        with cli.exclusive_cycle(settings):
            store = cli.open_store(settings)
            users = _read_users(store)
            change(users)
            store.write_text(ROLES_KEY, dump_roles(users))
    except WorkspaceError as exc:
        raise _fail(f"Error: {exc}") from None
    return users


@roles_app.command("list")
def roles_list(ctx: typer.Context) -> None:
    """Lista lo asignado."""
    try:
        users = _read_users(_cli().open_workspace(ctx))
    except WorkspaceError as exc:
        raise _fail(f"Error: {exc}") from None
    if not users:
        typer.echo("Sin roles asignados: la interfaz está en solo lectura para todo el mundo.")
    for email, grant in sorted(users.items()):
        typer.echo(_describe(email, grant))


@roles_app.command("grant")
def roles_grant(
    ctx: typer.Context,
    email: EmailArg,
    edit: Annotated[bool, typer.Option("--edit", help="Permite editar.")] = False,
    role: Annotated[list[Role] | None, typer.Option("--role", help="Rol ENS (repetible).")] = None,
) -> None:
    """Concede edición y/o roles ENS (suma a lo que ya tenía)."""
    if not edit and not role:
        raise _fail("Indica --edit, --role o ambos.")
    address = _clean_email(email)

    def change(users: dict[str, Grant]) -> None:
        current = users.get(address, Grant())
        users[address] = Grant(current.edit or edit, current.roles | frozenset(role or ()))

    users = _update(ctx, change)
    typer.echo(_describe(address, users[address]))


@roles_app.command("revoke")
def roles_revoke(
    ctx: typer.Context,
    email: EmailArg,
    role: Annotated[
        list[Role] | None, typer.Option("--role", help="Quita solo este rol (repetible).")
    ] = None,
    no_edit: Annotated[bool, typer.Option("--no-edit", help="Quita solo la edición.")] = False,
) -> None:
    """Sin opciones quita todo a la persona; con `--role` o `--no-edit`, solo eso."""
    address = _clean_email(email)
    removed_all = not role and not no_edit

    def change(users: dict[str, Grant]) -> None:
        current = users.get(address)
        if current is None:
            raise _fail("Esa persona no tiene nada asignado.")
        if removed_all:
            del users[address]
        else:
            users[address] = Grant(
                current.edit and not no_edit, current.roles - frozenset(role or ())
            )

    users = _update(ctx, change)
    typer.echo(
        f"{address}: sin nada asignado"
        if address not in users
        else _describe(address, users[address])
    )
