"""`ens-evidence ui serve`: el servicio web, detrás de Cloudflare Access."""

from datetime import UTC, datetime
from functools import partial

import typer

from ens_evidence.ui.access import load_jwks_file
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.config import UiConfigError, load_ui_config
from ens_evidence.workspace import (
    SettingsError,
    load_settings,
    open_store,
    resolve_settings_path,
)

ui_app = typer.Typer(help="Interfaz web (ADR 0021).", no_args_is_help=True)


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(2)


@ui_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Dirección de escucha."),
    port: int = typer.Option(8000, min=1, max=65535, help="Puerto de escucha."),
) -> None:
    """Sirve la UI; la autenticación la hace el JWT de Access en cada petición."""
    import uvicorn

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
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        server_header=False,
        access_log=False,
        proxy_headers=False,
    )
