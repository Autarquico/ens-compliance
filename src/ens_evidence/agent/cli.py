"""Órdenes `ens-evidence agent …`: servidor osquery y gestión de equipos. Sin dictamen."""

import functools
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated

import typer

from ens_evidence.agent.install import render_install_script
from ens_evidence.agent.node_key import issue_enroll_token, normalize_host_identifier
from ens_evidence.agent.osquery_config import pack_queries
from ens_evidence.agent.registry import (
    AgentRegistry,
    load_registry,
    record_admission,
    record_revocation,
)
from ens_evidence.agent.secrets import AgentSecrets, AgentSecretsError, load_agent_secrets
from ens_evidence.agent.state import AgentState
from ens_evidence.connectors.config import load_connector_config
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError
from ens_evidence.workspace.cycle_lock import CycleLock

agent_app = typer.Typer(
    help="Servidor osquery y gestión de equipos: recoge evidencias, nunca dictamina.",
    no_args_is_help=True,
)
query_app = typer.Typer(
    help="Consultas bajo demanda del paquete a los equipos.", no_args_is_help=True
)
agent_app.add_typer(query_app, name="query")

MAX_ADMIT_HOURS = 168
EXPIRES = re.compile(r"^(\d{1,4})([hd])$")

Asset = Annotated[
    str, typer.Option("--asset", help="Identificador del activo (connectors/endpoint.yaml).")
]
By = Annotated[str, typer.Option("--by", help="Nombre de quien registra (declarado).")]


def _cli():
    import ens_evidence.cli as cli  # import tardío: cli.py monta este grupo

    return cli


def _fail(message: str, code: int = 2) -> typer.Exit:
    return _cli().fail(message, code)


def handled[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(command)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except WorkspaceError as exc:
            raise _fail(f"Error: {exc}") from None
        except ValueError as exc:
            raise _fail(f"Error: {exc}") from None
        except OSError as exc:
            raise _fail(f"Error al leer o escribir ficheros: {type(exc).__name__}") from None

    return run


def _secrets() -> AgentSecrets:
    try:
        return load_agent_secrets()
    except AgentSecretsError as exc:
        raise _fail(f"Secretos del servidor no válidos: {exc}") from None


def _endpoint_config(ctx: typer.Context) -> EndpointConfig:
    return load_connector_config(_cli().open_workspace(ctx), CONFIG_NAME, EndpointConfig)


def _enrolled(config: EndpointConfig, asset: str) -> str:
    if asset not in config.hosts:
        raise _fail(f"El activo {asset} no está inscrito en connectors/endpoint.yaml.")
    return asset


def _is_server(ctx: typer.Context, asset: str) -> bool:
    assets, errors = load_assets(_cli().open_workspace(ctx))
    if assets is None:
        raise _fail("Error: no se pudo leer el inventario de activos:\n" + "\n".join(errors))
    found = next((item for item in assets.assets if item.id == asset), None)
    if found is None:
        raise _fail(f"El activo {asset} no está en scope/assets.yaml; añádelo al inventario.")
    return found.type is AssetType.SERVER


def _expiry(raw: str, now: datetime) -> datetime:
    match = EXPIRES.match(raw)
    if not match or int(match[1]) < 1:
        raise _fail("--expires debe ser un número de horas o días, p. ej. 24h o 2d.")
    amount = int(match[1])
    span = timedelta(hours=amount) if match[2] == "h" else timedelta(days=amount)
    if span > timedelta(hours=MAX_ADMIT_HOURS):
        raise _fail(f"--expires admite como máximo {MAX_ADMIT_HOURS}h (7d).")
    return now + span


@contextmanager
def _writing(ctx: typer.Context) -> Iterator[CycleLock]:
    """El lock del ciclo, como los demás comandos que escriben: ocupado, sale con 2."""
    with _cli().exclusive_cycle(_cli().resolved_settings(ctx)) as cycle:
        yield cycle.lock


@agent_app.command()
@handled
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option("--host", help="Dirección de escucha.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535, help="Puerto.")] = 8000,
) -> None:
    """Sirve el protocolo remoto de osquery (con un proxy delante) y guarda lo recibido como
    evidencia. Exige un workspace local y las claves ENS_AGENT_ENROLL_KEY y
    ENS_AGENT_NODE_KEY_SECRET en el entorno."""
    secrets = _secrets()
    settings = _cli().resolved_settings(ctx)
    if settings.workspace.backend != "local":
        raise _fail("El servidor exige un workspace local; con S3 no se puede garantizar el lock.")
    import uvicorn  # extra `server`: sin él la CLI base sigue funcionando

    from ens_evidence.agent.protocol import AgentService
    from ens_evidence.agent.routes import routes
    from ens_evidence.http.app import create_app
    from ens_evidence.http.logging import uvicorn_log_config

    service = AgentService(
        _cli().open_workspace(ctx),
        secrets,
        AgentState(),
        lambda: CycleLock.for_settings(settings),
    )
    uvicorn.run(
        create_app(routes(service)),
        host=host,
        port=port,
        server_header=False,
        access_log=False,
        proxy_headers=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=10,
        h11_max_incomplete_event_size=16384,
        log_config=uvicorn_log_config(),
    )


def _state_of(registry: AgentRegistry, asset: str) -> str:
    record = registry.agents.get(asset)
    if record is None:
        return "sin alta"
    if record.revoked:
        return f"revocado (generación {record.generation})"
    return f"admitido (generación {record.generation})"


@agent_app.command()
@handled
def hosts(ctx: typer.Context) -> None:
    """Muestra, por equipo inscrito, el último contacto y su estado de alta."""
    config = _endpoint_config(ctx)
    registry = load_registry(_cli().open_workspace(ctx))
    contacts = {contact.asset_id: contact for contact in AgentState().list_hosts()}
    for asset in sorted(config.hosts):
        contact = contacts.get(asset)
        last = contact.last_contact.isoformat() if contact else "nunca"
        typer.echo(f"{asset}\túltimo contacto: {last}\t{_state_of(registry, asset)}")


@agent_app.command()
@handled
def admit(
    ctx: typer.Context,
    asset: Asset,
    by: By,
    hours: Annotated[
        int, typer.Option("--hours", min=1, max=MAX_ADMIT_HOURS, help="Validez del token.")
    ] = 24,
) -> None:
    """Da de alta un equipo inscrito y emite su token de alta, que caduca. El token es lo único
    que se imprime en la salida estándar."""
    secrets = _secrets()
    config = _endpoint_config(ctx)
    _enrolled(config, asset)
    host_identifier = normalize_host_identifier(config.hosts[asset].hardware_uuid)
    if host_identifier is None:
        raise _fail(f"El hardware_uuid de {asset} no es un UUID válido.")
    now = _cli().clock()
    expires_at = now + timedelta(hours=hours)
    with _writing(ctx) as lock:
        record = record_admission(
            _cli().open_workspace(ctx), lock, asset, by=by, now=now, expires_at=expires_at
        )
    token = issue_enroll_token(secrets, host_identifier, record.generation, expires_at)
    typer.echo(token)
    typer.echo(
        f"Alta de {asset} (generación {record.generation}) válida hasta {expires_at.isoformat()}.\n"
        "En el equipo, guarda el token en un fichero que solo lean root o los administradores "
        "y genera el instalador con `ens-evidence agent install` (--token-file). osquery lo "
        "lee con --enroll_secret_path: /etc/osquery/enroll_secret (macOS y Linux) o "
        "C:\\Program Files\\osquery\\enroll_secret (Windows).\n"
        "No lo pases con --enroll_secret_env ni lo guardes en el repo.",
        err=True,
    )


@agent_app.command()
@handled
def install(
    ctx: typer.Context,
    asset: Asset,
    os_name: Annotated[str, typer.Option("--os", help="macos, linux o windows.")],
    server: Annotated[str, typer.Option("--server", help="Nombre de host del servidor.")],
    token_file: Annotated[
        str, typer.Option("--token-file", help="Ruta, en el equipo, del fichero con el token.")
    ],
    package: Annotated[str | None, typer.Option("--package", help="Linux: deb o rpm.")] = None,
    arch: Annotated[str | None, typer.Option("--arch", help="Linux: amd64 o arm64.")] = None,
) -> None:
    """Imprime el script que instala osquery (versión fijada, sha256 verificado) y lo configura
    contra el servidor. Ejecútalo en el equipo como root o administrador. No lee ni escribe
    secretos: el token lo lee el script desde --token-file. En servidores Linux Debian/Ubuntu
    añade ClamAV con actualización de firmas (paquetes de la distribución)."""
    _enrolled(_endpoint_config(ctx), asset)
    with_antimalware = _is_server(ctx, asset)
    script = render_install_script(
        os_name, server, token_file, package, arch, with_antimalware=with_antimalware
    )
    typer.echo(script, nl=False)


@agent_app.command()
@handled
def revoke(
    ctx: typer.Context,
    asset: Asset,
    by: By,
    reason: Annotated[str, typer.Option("--reason", help="Motivo de la revocación.")],
) -> None:
    """Revoca el acceso de un equipo: sus claves dejan de valer y hace falta un alta nueva."""
    if not reason.strip():
        raise _fail("Indica el motivo con --reason.")
    _enrolled(_endpoint_config(ctx), asset)
    with _writing(ctx) as lock:
        record = record_revocation(
            _cli().open_workspace(ctx), lock, asset, by=by, reason=reason, now=_cli().clock()
        )
    typer.echo(f"Equipo {asset} revocado (generación {record.generation}).")


@query_app.command("run")
@handled
def query_run(
    ctx: typer.Context,
    by: By,
    query: Annotated[str, typer.Option("--query", help="Id de una consulta del paquete.")],
    asset: Annotated[str | None, typer.Option("--asset", help="Un activo inscrito.")] = None,
    all_assets: Annotated[bool, typer.Option("--all", help="Todos los equipos inscritos.")] = False,
    expires: Annotated[str, typer.Option("--expires", help="Validez: 24h, 2d…")] = "24h",
) -> None:
    """Encola una consulta del paquete (sin SQL libre) para que los equipos la contesten."""
    if all_assets == (asset is not None):
        raise _fail("Indica un activo con --asset o todos con --all.")
    if query not in pack_queries():
        raise _fail(f"Consulta desconocida: {query}. Disponibles: {', '.join(pack_queries())}.")
    config = _endpoint_config(ctx)
    targets = sorted(config.hosts) if asset is None else [_enrolled(config, asset)]
    now = _cli().clock()
    run = AgentState().enqueue(query, targets, _expiry(expires, now), by, now=now)
    typer.echo(
        f"{run.run_id}: {query} para {', '.join(run.targets)} hasta {run.expires_at.isoformat()}."
    )


@query_app.command("show")
@handled
def query_show(run_id: str) -> None:
    """Muestra, por equipo, si la consulta se entregó y se contestó, y la evidencia resultante."""
    status = AgentState().show_run(run_id)
    if status is None:
        raise _fail(f"No existe la consulta {run_id}.", 1)
    typer.echo(
        f"{status.run.run_id}: {status.run.query_id}, caduca {status.run.expires_at.isoformat()}"
    )
    for host in status.hosts:
        if host.answer is None:
            state = "entregada, sin respuesta" if host.delivered_at else "pendiente de entrega"
            typer.echo(f"{host.asset_id}\t{state}")
            continue
        answer = host.answer
        typer.echo(
            f"{host.asset_id}\tcontestada {answer.answered_at.isoformat()}\t"
            f"estado osquery: {answer.status}\tevidencia: {answer.evidence_id or '-'}"
        )


@query_app.command("list")
@handled
def query_list(
    include_expired: Annotated[
        bool, typer.Option("--all", help="Incluye también las caducadas.")
    ] = False,
) -> None:
    """Lista las consultas bajo demanda vigentes."""
    active_at = None if include_expired else _cli().clock()
    for run in AgentState().list_runs(active_at=active_at):
        typer.echo(
            f"{run.run_id}\t{run.query_id}\t{', '.join(run.targets)}\t"
            f"caduca {run.expires_at.isoformat()}"
        )
