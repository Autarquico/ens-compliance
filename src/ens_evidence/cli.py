import functools
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from pydantic import ValidationError

from ens_evidence import schemas
from ens_evidence.backup import (
    IMMUTABILITY_PREFIX,
    BackupError,
    BackupRun,
    BackupSecrets,
    Restic,
    backup_status,
    dump,
    list_runs,
    load_secrets,
    parse_s3_repository,
    restore_snapshot,
    run_backup,
    verify_immutability,
)
from ens_evidence.backup.environment import ACCESS_KEY_VAR, SECRET_KEY_VAR, redact
from ens_evidence.backup.transfer import export_all, import_all
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import MeasuresFileError, init_measures
from ens_evidence.catalog.model import Catalog, Group, MeasureCard
from ens_evidence.evidence import (
    MANUAL_SOURCE,
    import_manual,
    list_manifests,
    verify_all,
    verify_one,
)
from ens_evidence.models.enums import Role
from ens_evidence.scope import (
    ObservedInputError,
    ScopeApprovalRejected,
    ScopeState,
    approve_scope,
    load_assets,
    load_inventory,
    parse_observed,
    reconcile,
    revoke_approval,
    scope_status,
)
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.models import Deployment
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.scope.sync import QUESTIONNAIRE_KEY, SYNCED_KEYS
from ens_evidence.workspace import (
    BackupSettings,
    LocalStore,
    NotText,
    SettingsError,
    WorkspaceError,
    WorkspaceStore,
    is_inside_product,
    load_settings,
    open_store,
    resolve_settings_path,
)

app = typer.Typer(help="Prepara evidencias para el ENS. No certifica.", no_args_is_help=True)
report_app = typer.Typer(help="Informes.", no_args_is_help=True)
assessment_app = typer.Typer(help="Evaluación.", no_args_is_help=True)
evidence_app = typer.Typer(help="Evidencias.", no_args_is_help=True)
schema_app = typer.Typer(help="Esquemas de los contratos.", no_args_is_help=True)
inventory_app = typer.Typer(help="Inventario de activos.", no_args_is_help=True)
workspace_app = typer.Typer(
    help="Sincroniza el workspace con una carpeta local.", no_args_is_help=True
)
scope_app = typer.Typer(help="Alcance del sistema y su aprobación.", no_args_is_help=True)
backup_app = typer.Typer(help="Copia independiente de las evidencias.", no_args_is_help=True)
catalog_app = typer.Typer(help="Catálogo de medidas del anexo II.", no_args_is_help=True)
controls_app = typer.Typer(help="Estado de las medidas de la organización.", no_args_is_help=True)
app.add_typer(report_app, name="report")
app.add_typer(assessment_app, name="assessment")
app.add_typer(evidence_app, name="evidence")
app.add_typer(schema_app, name="schema")
app.add_typer(inventory_app, name="inventory")
app.add_typer(workspace_app, name="workspace")
app.add_typer(scope_app, name="scope")
app.add_typer(backup_app, name="backup")
app.add_typer(catalog_app, name="catalog")
app.add_typer(controls_app, name="controls")

SCHEMAS_DIR = Path("schemas")


SettingsOption = Annotated[
    Path | None,
    typer.Option(
        "--settings",
        envvar="ENS_SETTINGS",
        help="Fichero de ajustes (por defecto ~/.config/ens-evidence/settings.yaml).",
    ),
]

SCOPE_SKELETON = {
    "scope/assets.yaml": "# Un activo por elemento; ver scope/questionnaire.md.\nassets: []\n",
    "scope/services.yaml": "services: []\n",
    "scope/dependencies.yaml": "dependencies: []\n",
    "scope/system.yaml": (
        "# Rellena nombre, descripción y los activos y servicios incluidos o excluidos.\n"
        'name: ""\ndescription: ""\n'
        "included_assets: []\nincluded_services: []\nexclusions: []\n"
    ),
}


def not_implemented() -> None:
    typer.echo("not implemented", err=True)
    raise typer.Exit(2)


def fail(message: str, code: int = 2) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def handled[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """Convierte los errores del workspace en un mensaje claro y código 2."""

    @functools.wraps(command)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except WorkspaceError as exc:
            raise fail(f"Error: {exc}") from None
        except (OSError, ValueError) as exc:
            raise fail(f"Error al leer o escribir ficheros: {type(exc).__name__}") from None

    return run


@app.callback()
def main(ctx: typer.Context, settings: SettingsOption = None) -> None:
    ctx.obj = settings


def open_workspace(ctx: typer.Context) -> WorkspaceStore:
    return open_store(load_settings(resolve_settings_path(ctx.obj)))


def reject_inside_product(directory: Path) -> None:
    if is_inside_product(directory):
        raise fail(
            f"{directory} está dentro del repo del producto; "
            "los datos de una organización no pueden guardarse ahí. Elige otra carpeta.",
            1,
        )


def print_errors(errors: list[str]) -> None:
    for error in errors:
        typer.echo(f"  - {error}", err=True)


@app.command()
@handled
def init(
    ctx: typer.Context,
    settings: SettingsOption = None,
    path: Annotated[
        Path | None, typer.Option("--path", help="Carpeta local del workspace.")
    ] = None,
    s3_bucket: Annotated[str | None, typer.Option(help="Bucket S3-compatible.")] = None,
    s3_prefix: Annotated[str, typer.Option(help="Prefijo dentro del bucket.")] = "",
    s3_endpoint: Annotated[
        str | None, typer.Option(help="URL https del servicio (R2, etc.).")
    ] = None,
    s3_region: Annotated[str | None, typer.Option(help="Región del bucket.")] = None,
) -> None:
    """Crea los ajustes y el esqueleto del workspace sin sobrescribir lo existente.

    Las credenciales S3 se leen de las variables AWS_ACCESS_KEY_ID y
    AWS_SECRET_ACCESS_KEY; nunca se guardan en los ajustes.
    """
    settings_path = resolve_settings_path(settings or ctx.obj)
    if settings_path.exists():
        typer.echo(f"Los ajustes ya existen y se conservan: {settings_path}")
    else:
        write_settings(settings_path, path, s3_bucket, s3_prefix, s3_endpoint, s3_region)
        typer.echo(f"Ajustes creados: {settings_path}")
    store = open_store(load_settings(settings_path))
    template = files("ens_evidence").joinpath("templates/questionnaire.md").read_text("utf-8")
    skeleton = {**SCOPE_SKELETON, QUESTIONNAIRE_KEY: template}
    created = [key for key, text in skeleton.items() if write_if_absent(store, key, text)]
    for key in created:
        typer.echo(f"Creado: {key}")
    if not created:
        typer.echo("El workspace ya tenía todos los ficheros; no se ha cambiado nada.")
    typer.echo("Siguiente paso: rellena scope/ con ayuda de scope/questionnaire.md.")


def write_settings(
    settings_path: Path,
    path: Path | None,
    bucket: str | None,
    prefix: str,
    endpoint: str | None,
    region: str | None,
) -> None:
    if (path is None) == (bucket is None):
        raise fail("Indica el destino con --path (carpeta local) o con --s3-bucket (S3), no ambos.")
    if path is not None:
        reject_inside_product(path)
        workspace: dict[str, Any] = {
            "backend": "local",
            "local": {"path": str(path.expanduser().resolve())},
        }
    else:
        s3 = {"bucket": bucket, "prefix": prefix, "endpoint_url": endpoint, "region": region}
        workspace = {"backend": "s3", "s3": {k: v for k, v in s3.items() if v}}
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(yaml.safe_dump({"workspace": workspace}), encoding="utf-8")
    try:
        load_settings(settings_path)
    except SettingsError:
        settings_path.unlink(missing_ok=True)
        raise


def write_if_absent(store: WorkspaceStore, key: str, text: str) -> bool:
    if store.exists(key):
        return False
    store.write_text(key, text)
    return True


def differing_keys(source: WorkspaceStore, target: WorkspaceStore, keys: list[str]) -> list[str]:
    """Claves que existen en `target` con contenido distinto (o ilegible) respecto a `source`."""
    differing = []
    for key in keys:
        if not target.exists(key):
            continue
        try:
            same = target.read_text(key) == source.read_text(key)
        except NotText:
            same = False
        if not same:
            differing.append(key)
    return differing


@workspace_app.command("pull")
@handled
def workspace_pull(
    ctx: typer.Context,
    directory: Path,
    force: Annotated[
        bool, typer.Option("--force", help="Sobrescribe los ficheros locales distintos.")
    ] = False,
) -> None:
    """Copia el workspace remoto a una carpeta local para editarlo."""
    reject_inside_product(directory)
    source = open_workspace(ctx)
    target = LocalStore(directory)
    keys = [key for key in SYNCED_KEYS if source.exists(key)]
    if not force:
        differing = differing_keys(source, target, keys)
        if differing:
            typer.echo("No se copia nada: hay ediciones locales que se perderían:", err=True)
            print_errors(differing)
            typer.echo("Publícalas con workspace push o usa --force para sobrescribir.", err=True)
            raise typer.Exit(1)
    for key in keys:
        target.write_text(key, source.read_text(key))
    typer.echo(f"{len(keys)} ficheros copiados a {directory}")


@workspace_app.command("push")
@handled
def workspace_push(ctx: typer.Context, directory: Path) -> None:
    """Valida la carpeta local y, solo si es válida, la publica en el workspace.

    No publica scope/approval.yaml: la aprobación solo se escribe con scope approve.
    """
    if not directory.is_dir():
        typer.echo(
            f"La carpeta {directory} no existe; tráela con "
            f"`ens-evidence workspace pull {directory}`.",
            err=True,
        )
        raise typer.Exit(1)
    source = LocalStore(directory)
    loaded = load_inventory(source)
    if loaded.inventory is None:
        typer.echo("No se publica nada: el alcance no es válido.", err=True)
        print_errors(loaded.errors)
        raise typer.Exit(1)
    keys = [key for key in SYNCED_KEYS if source.exists(key)]
    contents = {key: source.read_text(key) for key in keys}
    leaked = [key for key, text in contents.items() if find_secrets(text)]
    if leaked:
        typer.echo("No se publica nada: estos ficheros parecen contener secretos:", err=True)
        print_errors(leaked)
        raise typer.Exit(1)
    destination = open_workspace(ctx)
    present = [key for key in keys if destination.exists(key)]
    changing = (set(keys) - set(present)) | set(differing_keys(source, destination, present))
    if not changing:
        typer.echo("Nada que publicar: el destino ya es idéntico.")
        return
    if revoke_approval(destination, "workspace push", datetime.now(UTC)):
        typer.echo("Se revoca la aprobación del destino; tras publicar hay que aprobar de nuevo.")
    for key, text in contents.items():
        destination.write_text(key, text)
    typer.echo(f"{len(keys)} ficheros publicados.")
    published = scope_status(destination)
    typer.echo(f"Estado del alcance en el destino: {published.state.value}")
    print_errors(published.errors)


@workspace_app.command("export")
@handled
def workspace_export(
    ctx: typer.Context,
    directory: Path,
    all_keys: Annotated[
        bool, typer.Option("--all", help="Todas las claves del workspace, por bytes.")
    ] = False,
) -> None:
    """Exporta el workspace completo a una carpeta local vacía.

    Si una clave cambia durante la copia, aborta y borra la exportación."""
    if not all_keys:
        raise fail("Indica --all: la exportación copia todo el workspace.")
    reject_inside_product(directory)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise fail(f"{directory} debe ser una carpeta vacía o no existir.", 1)
    try:
        keys = export_all(open_workspace(ctx), LocalStore(directory))
    except WorkspaceError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    typer.echo(f"{len(keys)} ficheros exportados a {directory}")


@workspace_app.command("import")
@handled
def workspace_import(
    ctx: typer.Context,
    directory: Path,
    all_keys: Annotated[
        bool, typer.Option("--all", help="Todas las claves de la carpeta, por bytes.")
    ] = False,
) -> None:
    """Importa una exportación completa al workspace, sin pisar evidencias distintas."""
    if not all_keys:
        raise fail("Indica --all: la importación copia toda la carpeta.")
    if not directory.is_dir():
        raise fail(f"La carpeta {directory} no existe.", 1)
    report = import_all(open_workspace(ctx), LocalStore(directory), datetime.now(UTC))
    if report.approval_revoked:
        typer.echo("Se revoca la aprobación del destino; tras importar hay que aprobar de nuevo.")
    if report.backup_note:
        typer.echo(report.backup_note)
    typer.echo(f"{report.written} ficheros importados, {report.unchanged} sin cambios.")


@inventory_app.command("validate")
@handled
def inventory_validate(ctx: typer.Context) -> None:
    """Comprueba que el inventario y el alcance están completos y son coherentes."""
    loaded = load_inventory(open_workspace(ctx))
    if loaded.inventory is None:
        typer.echo("El inventario no es válido:", err=True)
        print_errors(loaded.errors)
        raise typer.Exit(1)
    inventory = loaded.inventory
    planned = [a.id for a in inventory.assets.assets if a.deployment is Deployment.PLANNED]
    typer.echo(
        f"Inventario válido: {len(inventory.assets.assets)} activos, "
        f"{len(inventory.services.services)} servicios, "
        f"{len(inventory.dependencies.dependencies)} dependencias."
    )
    if planned:
        typer.echo(f"Activos planificados (aún no desplegados): {', '.join(planned)}")


@inventory_app.command("reconcile")
@handled
def inventory_reconcile(
    ctx: typer.Context,
    observed: Annotated[Path, typer.Option("--observed", help="JSON con los equipos observados.")],
) -> None:
    """Compara el inventario con los equipos observados. Es informativo."""
    try:
        text = observed.read_text(encoding="utf-8")
    except OSError:
        raise fail("No se puede leer el fichero indicado en --observed.") from None
    try:
        hosts = parse_observed(text)
    except ObservedInputError as exc:
        typer.echo("La entrada observada no es válida:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    assets, errors = load_assets(open_workspace(ctx))
    if assets is None:
        typer.echo(
            "El inventario de activos no es válido; corrígelo antes de reconciliar:", err=True
        )
        print_errors(errors)
        raise typer.Exit(1)
    report = reconcile(assets.assets, hosts)
    if report.input_errors:
        typer.echo("La entrada observada tiene errores:", err=True)
        print_errors(report.input_errors)
        raise typer.Exit(1)
    labels = {
        "not_in_inventory": "Observado pero no está en el inventario",
        "not_observed": "En el inventario (desplegado) pero no observado",
    }
    for item in report.discrepancies:
        typer.echo(f"{labels[item.kind.value]}: {item.identity}")
    typer.echo(
        f"{len(report.discrepancies)} discrepancias (informativo; no bloquea la aprobación)."
    )


@scope_app.command("approve")
@handled
def scope_approve(
    ctx: typer.Context,
    by: Annotated[str, typer.Option("--by", help="Nombre de quien aprueba (declarado).")],
    role: Annotated[Role, typer.Option(help="Rol de quien aprueba.")] = Role.SYSTEM_OWNER,
) -> None:
    """Aprueba el alcance vigente. Solo lo aprueba el responsable del sistema."""
    store = open_workspace(ctx)
    try:
        approval = approve_scope(store, by, role, datetime.now(UTC))
    except ScopeApprovalRejected as exc:
        typer.echo("No se aprueba el alcance:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(
        f"Alcance aprobado por {approval.approved_by} ({approval.role.value}). "
        f"Huella: {approval.fingerprint}\n"
        "El nombre es declarado; no se autentica."
    )


@scope_app.command("status")
@handled
def scope_show_status(ctx: typer.Context) -> None:
    """Muestra si el alcance está aprobado. Sale con 1 si no lo está."""
    status = scope_status(open_workspace(ctx))
    hints = {
        ScopeState.APPROVED: "El alcance está aprobado y no ha cambiado.",
        ScopeState.NOT_APPROVED: "El alcance es válido pero falta aprobarlo (scope approve).",
        ScopeState.STALE: "El alcance cambió después de aprobarse; hay que aprobarlo de nuevo.",
        ScopeState.INVALID: "El alcance no es válido:",
    }
    typer.echo(f"{status.state.value}: {hints[status.state]}")
    print_errors(status.errors)
    if status.state is not ScopeState.APPROVED:
        raise typer.Exit(1)


def backup_settings(ctx: typer.Context) -> BackupSettings:
    configured = load_settings(resolve_settings_path(ctx.obj)).backup
    if configured is None:
        raise fail(
            "Faltan los ajustes de la copia: añade backup.restic_repository al fichero de ajustes."
        )
    return configured


def backup_restic(ctx: typer.Context) -> tuple[Restic, BackupSettings, BackupSecrets]:
    configured = backup_settings(ctx)
    secrets = load_secrets(configured.restic_repository)
    restic = Restic(configured.restic_repository, secrets, region=configured.region)
    return restic, configured, secrets


@backup_app.command("init")
@handled
def backup_init(ctx: typer.Context) -> None:
    """Inicializa el repositorio restic; si ya existe, lo deja intacto.

    La región (backup.region en los ajustes) es opcional; Cloudflare R2 usa `auto`.
    """
    restic, configured, _ = backup_restic(ctx)
    if restic.init():
        typer.echo(f"Repositorio restic inicializado: {configured.restic_repository}")
    else:
        typer.echo(f"El repositorio ya existe, no se toca: {configured.restic_repository}")


@backup_app.command("run")
@handled
def backup_run(ctx: typer.Context) -> None:
    """Copia el workspace completo al repositorio restic y registra la ejecución."""
    restic, configured, secrets = backup_restic(ctx)
    run = run_backup(
        open_workspace(ctx),
        restic,
        configured.restic_repository,
        secrets,
        lambda: datetime.now(UTC),
    )
    if run.status == "failed":
        typer.echo(f"La copia {run.run_id} ha fallado: {run.error}", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Copia {run.run_id} completada. Snapshot: {run.snapshot_id}; "
        f"{len(run.inventory or {})} ficheros en el inventario."
    )


@backup_app.command("status")
@handled
def backup_show_status(ctx: typer.Context) -> None:
    """Muestra la última ejecución y las interrumpidas. Sale con 1 si hay algo que revisar."""
    status = backup_status(open_workspace(ctx))
    if status.last is None:
        typer.echo("No hay ninguna copia registrada.", err=True)
        raise typer.Exit(1)
    last = status.last
    typer.echo(
        f"Última ejecución: {last.run_id}  {last.status}  {last.started_at.isoformat()}"
        + (f"  snapshot {last.snapshot_id}" if last.snapshot_id else "")
        + ("  (reconstruido al restaurar)" if last.reconstructed_from_snapshot else "")
    )
    if last.error:
        typer.echo(f"Error: {last.error}", err=True)
    for run in status.interrupted:
        typer.echo(f"Interrumpida (sin registro final): {run.run_id}  {run.started_at.isoformat()}")
    for key in status.unreadable:
        typer.echo(f"Registro ilegible: {key}", err=True)
    if last.status != "completed" or status.interrupted or status.unreadable:
        raise typer.Exit(1)


@backup_app.command("restore")
@handled
def backup_restore(
    ctx: typer.Context,
    snapshot: Annotated[str, typer.Option(help="Id del snapshot o latest.")],
    target: Annotated[Path, typer.Option(help="Carpeta vacía donde restaurar.")],
) -> None:
    """Restaura un snapshot en una carpeta y lo compara con el inventario registrado.

    Después, `workspace import --all` lo devuelve a un workspace local o S3.
    """
    reject_inside_product(target)
    restic, configured, _ = backup_restic(ctx)
    recorded: list[BackupRun] = []
    try:
        recorded, _unreadable = list_runs(open_workspace(ctx))
    except WorkspaceError:
        typer.echo("No se puede leer el workspace; la restauración no se comparará.", err=True)
    report = restore_snapshot(restic, snapshot, target, recorded)
    typer.echo(f"Snapshot {report.snapshot_id} restaurado en {target}: {report.files} ficheros.")
    if not report.inventory_checked:
        typer.echo(
            "El snapshot no lleva su inventario (.ens-backup): restauración NO verificada. "
            "No la des por buena; ejecuta evidence verify --all sobre lo importado.",
            err=True,
        )
        raise typer.Exit(1)
    if report.mismatches:
        typer.echo("El inventario restaurado no coincide con el registrado:", err=True)
        print_errors(report.mismatches)
        raise typer.Exit(1)
    typer.echo("El inventario restaurado coincide con el registrado (claves y sha256).")


@backup_app.command("verify-immutability")
@handled
def backup_verify_immutability(ctx: typer.Context) -> None:
    """Prueba con objetos canario que el destino rechaza borrar y sobrescribir.

    Solo toca objetos canario nuevos, nunca los de restic. Ejecútalo a mano contra el bucket real.
    """
    configured = backup_settings(ctx)
    secrets = load_secrets(configured.restic_repository)
    endpoint, bucket, prefix = parse_s3_repository(configured.restic_repository)
    if secrets.access_key_id is None:
        raise fail(f"Faltan {ACCESS_KEY_VAR} y {SECRET_KEY_VAR}.")
    try:
        import boto3
    except ImportError:
        raise fail("Requiere el extra opcional: pip install 'ens-evidence[s3]'") from None
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=secrets.access_key_id,
        aws_secret_access_key=secrets.secret_access_key,
        region_name=configured.region,
    )
    try:
        check = verify_immutability(
            client, configured.restic_repository, bucket, prefix, datetime.now(UTC)
        )
    except BackupError as exc:
        raise fail(f"Error: {redact(str(exc), secrets)}") from None
    stamp = check.checked_at.strftime("%Y%m%dT%H%M%S%fZ")
    open_workspace(ctx).create_bytes(f"{IMMUTABILITY_PREFIX}/{stamp}.yaml", dump(check))
    for result in check.results:
        mark = "protegido" if result.protected else "NO protegido"
        typer.echo(f"{result.prefix}/: {mark} ({result.detail})")
    typer.echo(check.note)
    if not check.protected:
        raise typer.Exit(1)
@catalog_app.command("list")
def catalog_list(
    group: Annotated[Group | None, typer.Option("--group", help="Solo este grupo.")] = None,
) -> None:
    """Lista las medidas del catálogo."""
    measures = [m for m in load_catalog().measures if group is None or m.group is group]
    for measure in measures:
        typer.echo(f"{measure.id}  {measure.name}")
    typer.echo(f"{len(measures)} medidas.")


@catalog_app.command("show")
def catalog_show(measure_id: Annotated[str, typer.Argument(help="p. ej. op.acc.5")]) -> None:
    """Muestra la ficha de una medida: eje, niveles, base y refuerzos."""
    catalog = load_catalog()
    measure = catalog.get(measure_id)
    if measure is None:
        raise fail(f"La medida {measure_id} no está en el catálogo (catalog list las muestra).", 1)
    for line in describe_measure(measure, catalog):
        typer.echo(line)


def describe_measure(measure: MeasureCard, catalog: Catalog) -> list[str]:
    axis = {"category": "categoría", "dimension": "dimensión"}[measure.applies_by.value]
    lines = [
        f"{measure.id}  {measure.name}",
        f"Se aplica por {axis}. Dimensiones: {', '.join(d.value for d in measure.dimensions)}",
        f"Fuente: {measure.source.regulation}, anexo {measure.source.annex}, "
        f"apartado {measure.source.section} (consolidado {measure.source.consolidation_date})",
    ]
    for section in (catalog.group(measure.group), catalog.subgroup(measure.subgroup)):
        if section:
            lines += ["", f"{section.id}  {section.title}", *(f"  {p}" for p in section.text)]
    lines += ["", "Niveles:"]
    for level, requirement in measure.levels.items():
        shown = "n.a." if requirement.not_applicable else requirement.expression
        lines.append(f"  {level}: {shown}")
    if measure.preamble:
        lines += ["", *measure.preamble]
    lines += ["", "Base:", *(f"  {clause.text}" for clause in measure.base)]
    for reinforcement in measure.reinforcements:
        shown = [
            f"{label}: {', '.join(levels)}"
            for label, levels in (
                ("exigido en", reinforcement.required_at),
                ("alternativa en", reinforcement.alternative_at),
            )
            if levels
        ]
        lines += [
            "",
            f"{reinforcement.id}  {reinforcement.title} ({'; '.join(shown) or 'ningún nivel'})",
            *(f"  {clause.text}" for clause in reinforcement.clauses),
        ]
    if measure.notes:
        lines += ["", *(f"Nota: {note}" for note in measure.notes)]
    if measure.diagram_refs:
        lines += ["", *(f"Diagrama del anexo: {ref}" for ref in measure.diagram_refs)]
    return lines


@controls_app.command("init")
@handled
def controls_init(ctx: typer.Context) -> None:
    """Crea controls/measures.yaml con el estado inicial de cada medida del catálogo.

    Conserva el estado de las medidas que ya existen y rechaza IDs ajenos al catálogo.
    """
    try:
        result = init_measures(open_workspace(ctx))
    except MeasuresFileError as exc:
        typer.echo("No se cambia controls/measures.yaml:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    if result.created:
        typer.echo(
            f"Creadas {len(result.created)} medidas sin determinar (aplicabilidad "
            f"UNDETERMINED, evaluación NOT_ASSESSED); conservadas {result.kept}."
        )
    else:
        typer.echo(f"Las {result.kept} medidas ya existían; se conservan sin cambios.")
    if replaced := result.replaced_version:
        typer.echo(
            f"La versión del catálogo cambió (consolidado {replaced.consolidation_date} → "
            f"{result.version.consolidation_date}); revisa las medidas afectadas."
        )


@app.command()
def collect(
    source: Annotated[str | None, typer.Argument(help="Conector a ejecutar.")] = None,
    all_sources: Annotated[bool, typer.Option("--all", help="Todos los conectores.")] = False,
) -> None:
    """Recoge evidencias."""
    not_implemented()


@app.command()
def check(all_checks: Annotated[bool, typer.Option("--all")] = False) -> None:
    """Evalúa los checks."""
    not_implemented()


@report_app.command("daily")
def report_daily() -> None:
    """Informe diario."""
    not_implemented()


@report_app.command("monthly")
def report_monthly() -> None:
    """Informe mensual."""
    not_implemented()


@assessment_app.command("status")
def assessment_status() -> None:
    """Estado de la evaluación."""
    not_implemented()


def parse_instant(option: str, text: str | None) -> datetime | None:
    if text is None:
        return None
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        instant = None
    if instant is None or instant.tzinfo is None:
        raise fail(f"{option}: fecha y hora no válidas; usa el formato 2026-01-31T10:00:00+00:00")
    return instant


@evidence_app.command("import")
@handled
def evidence_import(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Fichero aportado por una persona.")],
    source: Annotated[str, typer.Option(help="Origen; solo se admite manual.")],
    asset: Annotated[str, typer.Option(help="Id del activo al que se refiere.")],
    by: Annotated[str, typer.Option("--by", help="Nombre de quien aporta (declarado).")],
    collected_at: Annotated[
        str | None, typer.Option(help="Momento al que se refiere (por defecto, ahora).")
    ] = None,
    valid_until: Annotated[str | None, typer.Option(help="Caducidad de la evidencia.")] = None,
) -> None:
    """Registra una evidencia manual con su hash, autor y fecha."""
    if source != MANUAL_SOURCE:
        raise fail(f"--source: solo se admite {MANUAL_SOURCE}; las demás las recoge un collector.")
    collected = parse_instant("--collected-at", collected_at)
    expires = parse_instant("--valid-until", valid_until)
    try:
        data = file.read_bytes()
    except OSError:
        raise fail("No se puede leer el fichero indicado.") from None
    try:
        manifest = import_manual(
            open_workspace(ctx),
            data,
            asset_id=asset,
            submitted_by=by,
            now=datetime.now(UTC),
            collected_at=collected,
            valid_until=expires,
        )
    except ValidationError as exc:
        typer.echo("No se importa la evidencia:", err=True)
        print_errors(describe_validation("evidence import", exc))
        raise typer.Exit(1) from None
    typer.echo(
        f"Evidencia registrada: {manifest.id}\n"
        f"Huella sha256: {manifest.artifact.sha256}\n"
        "El nombre de quien aporta es declarado; no se autentica."
    )


@evidence_app.command("list")
@handled
def evidence_list(ctx: typer.Context) -> None:
    """Lista las evidencias registradas."""
    listing = list_manifests(open_workspace(ctx))
    for manifest in listing.manifests:
        expiry = manifest.valid_until.isoformat() if manifest.valid_until else "sin caducidad"
        typer.echo(
            f"{manifest.id}  {manifest.source}  {manifest.asset_id}  "
            f"{manifest.collected_at.isoformat()}  {expiry}"
        )
    typer.echo(f"{len(listing.manifests)} evidencias.")
    if listing.errors:
        typer.echo("Manifiestos no válidos:", err=True)
        print_errors(listing.errors)
        raise typer.Exit(1)


@evidence_app.command("verify")
@handled
def evidence_verify(
    ctx: typer.Context,
    evidence_id: Annotated[str | None, typer.Argument(help="Id de la evidencia.")] = None,
    all_evidence: Annotated[
        bool, typer.Option("--all", help="Todas, y además huérfanos y referencias rotas.")
    ] = False,
) -> None:
    """Verifica la integridad de una evidencia o de todas.

    El hash prueba que el artefacto coincide con su manifiesto, no que sea auténtico.
    """
    if (evidence_id is None) == (not all_evidence):
        raise fail("Indica un id de evidencia o --all, no ambos.")
    store = open_workspace(ctx)
    report = verify_all(store) if all_evidence else verify_one(store, str(evidence_id))
    if not report.ok:
        typer.echo("La verificación ha fallado:", err=True)
        print_errors(report.problems)
        raise typer.Exit(1)
    typer.echo(f"{report.checked} evidencias verificadas: íntegras.")


@schema_app.command("export")
def schema_export(
    directory: Annotated[Path, typer.Option("--dir", help="Directorio de destino.")] = SCHEMAS_DIR,
    check_only: Annotated[
        bool, typer.Option("--check", help="No escribe; sale con 1 si hay deriva.")
    ] = False,
) -> None:
    """Exporta el JSON Schema de los contratos."""
    if not check_only:
        schemas.export(directory)
        typer.echo(f"exported {len(schemas.CONTRACTS)} schemas to {directory}")
        return
    problems = schemas.drift(directory)
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("schemas up to date")
