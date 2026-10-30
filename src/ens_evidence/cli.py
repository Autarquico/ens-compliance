import functools
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from pydantic import ValidationError

from ens_evidence import __version__, schemas
from ens_evidence.applicability import (
    ApplicabilityRejected,
    DeclarationRejected,
    DeclarationState,
    Exclusion,
    approve_declaration,
    declaration_status,
    propose_applicability,
    validate_applicability,
)
from ens_evidence.backup import (
    IMMUTABILITY_PREFIX,
    BackupError,
    BackupRun,
    BackupSecrets,
    Restic,
    SetupFailureRecorder,
    backup_status,
    check_scope,
    dump,
    failed_check_recorder,
    failed_restore_recorder,
    failed_run_recorder,
    list_runs,
    load_secrets,
    parse_s3_repository,
    record_restore_test,
    restore_recorded,
    run_backup,
    run_check,
    verify_immutability,
)
from ens_evidence.backup.environment import ACCESS_KEY_VAR, SECRET_KEY_VAR, redact
from ens_evidence.backup.records import SetupFailure
from ens_evidence.backup.transfer import export_all, import_all
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import MeasuresFileError, init_measures
from ens_evidence.catalog.model import Catalog, Group, MeasureCard
from ens_evidence.categorization import (
    CategorizationApprovalRejected,
    CategorizationNotCurrent,
    CategorizationState,
    approve_categorization,
    categorization_status,
    commitment,
    commitment_notice,
    init_categorization,
)
from ens_evidence.connectors.config import load_connector_config
from ens_evidence.connectors.registry import ConnectorLoadError, ConnectorRegistry
from ens_evidence.endpoint.bundle import BundleTargetError, write_bundle
from ens_evidence.endpoint.evaluate import evaluate_workspace
from ens_evidence.endpoint.importer import ENDPOINT_SOURCE, import_endpoint
from ens_evidence.endpoint.pack import pack_sha256
from ens_evidence.endpoint.package import MAX_PACKAGE_BYTES
from ens_evidence.evidence import (
    MANUAL_SOURCE,
    import_manual,
    list_manifests,
    verify_all,
    verify_one,
)
from ens_evidence.evidence.importer import import_collected
from ens_evidence.matrix import (
    CHECKS_PENDING,
    DECLARATION_REPORTS_PREFIX,
    MATRIX_REPORTS_PREFIX,
    ExportRejected,
    MatrixRejected,
    ReviewPlan,
    RowKind,
    RowStatus,
    add_link,
    assess,
    build_export,
    build_link,
    init_matrix,
    link_label,
    matrix_status,
    parse_covers,
    prepare_review,
    record_review,
    remove_link,
    render_declaration,
    render_json,
    render_markdown,
    save_report,
    set_entry,
)
from ens_evidence.matrix.links import CHECK, DOCUMENT, EVIDENCE
from ens_evidence.matrix.messages import (
    DECLARATION_LABELS,
    READINESS_LABELS,
    blocker_lines,
    obligation_label,
)
from ens_evidence.models.enums import CollectionStatus, Role
from ens_evidence.orgdocs import (
    ANSWERS_KEY,
    AnswersInvalid,
    BuildStatus,
    DocumentApprovalRejected,
    DocumentState,
    TemplateError,
    UnknownDocument,
    all_questions,
    all_statuses,
    approve_document,
    build_documents,
    load_templates,
    requirement_label,
    skeleton,
)
from ens_evidence.risk import (
    RiskApprovalRejected,
    RiskState,
    approve_risk,
    init_risk,
    risk_status,
    validate_risk,
)
from ens_evidence.scope import (
    ObservedInputError,
    ScopeApprovalRejected,
    ScopeNotApproved,
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
from ens_evidence.scope.sync import QUESTIONNAIRE_KEY, SCOPE_KEYS, synced_keys
from ens_evidence.workspace import (
    BackupSettings,
    LocalStore,
    NotText,
    Settings,
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
categorization_app = typer.Typer(
    help="Categorización del sistema y su aprobación.", no_args_is_help=True
)
applicability_app = typer.Typer(
    help="Aplicabilidad de las medidas y Declaración de Aplicabilidad.", no_args_is_help=True
)
backup_app = typer.Typer(help="Copia independiente de las evidencias.", no_args_is_help=True)
restore_test_app = typer.Typer(
    help="Pruebas de restauración declaradas por una persona.", no_args_is_help=True
)
backup_app.add_typer(restore_test_app, name="restore-test")
catalog_app = typer.Typer(help="Catálogo de medidas del anexo II.", no_args_is_help=True)
controls_app = typer.Typer(help="Estado de las medidas de la organización.", no_args_is_help=True)
endpoint_app = typer.Typer(help="Evidencia de endpoints con script guiado.", no_args_is_help=True)
docs_app = typer.Typer(help="Documentación organizativa.", no_args_is_help=True)
risk_app = typer.Typer(help="Análisis de riesgos y su aprobación anual.", no_args_is_help=True)
matrix_app = typer.Typer(
    help="Matriz de cumplimiento por medida: responsable, periodicidad, fuentes y revisiones.",
    no_args_is_help=True,
)
app.add_typer(report_app, name="report")
app.add_typer(assessment_app, name="assessment")
app.add_typer(evidence_app, name="evidence")
app.add_typer(schema_app, name="schema")
app.add_typer(inventory_app, name="inventory")
app.add_typer(workspace_app, name="workspace")
app.add_typer(scope_app, name="scope")
app.add_typer(categorization_app, name="categorization")
app.add_typer(applicability_app, name="applicability")
app.add_typer(backup_app, name="backup")
app.add_typer(catalog_app, name="catalog")
app.add_typer(controls_app, name="controls")
app.add_typer(endpoint_app, name="endpoint")
app.add_typer(docs_app, name="docs")
app.add_typer(risk_app, name="risk")
app.add_typer(matrix_app, name="matrix")

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
        except TemplateError as exc:
            raise fail(f"Las plantillas empaquetadas no son válidas: {exc}") from None
        except (OSError, ValueError) as exc:
            raise fail(f"Error al leer o escribir ficheros: {type(exc).__name__}") from None

    return run


@app.callback()
def main(ctx: typer.Context, settings: SettingsOption = None) -> None:
    ctx.obj = settings


def resolved_settings(ctx: typer.Context) -> Settings:
    return load_settings(resolve_settings_path(ctx.obj))


def open_workspace(ctx: typer.Context) -> WorkspaceStore:
    return open_store(resolved_settings(ctx))


def reject_inside_product(directory: Path) -> None:
    if is_inside_product(directory):
        raise fail(
            f"{directory} está dentro del repo del producto; "
            "los datos de una organización no pueden guardarse ahí. Elige otra carpeta.",
            1,
        )


def _same_file(candidate: Path, root: Path) -> bool:
    try:
        return os.path.samefile(candidate, root)
    except OSError:
        return False


def reject_inside_workspace(ctx: typer.Context, directory: Path) -> None:
    local = resolved_settings(ctx).workspace.local
    if local is None:
        return
    root = Path(local.path).resolve()
    resolved = directory.resolve()
    if resolved.is_relative_to(root) or any(
        _same_file(candidate, root) for candidate in (resolved, *resolved.parents)
    ):
        raise fail(
            f"{directory} está dentro del workspace en uso ({root}); "
            "restaura en una carpeta aislada fuera de él.",
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
    keys = synced_keys(source)
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
    keys = synced_keys(source)
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
    scope_changed = bool(changing & SCOPE_KEYS)
    if scope_changed and revoke_approval(destination, "workspace push", datetime.now(UTC)):
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


@endpoint_app.command("script")
def endpoint_script(
    out: Annotated[
        Path, typer.Option("--out", help="Directorio vacío o inexistente donde escribir el script.")
    ],
) -> None:
    """Escribe collect-endpoint.sh y el paquete de consultas fijo en un directorio."""
    try:
        written = write_bundle(out)
    except BundleTargetError as exc:
        raise fail(f"Error: {exc}") from None
    except OSError as exc:
        raise fail(f"Error al escribir ficheros: {type(exc).__name__}") from None
    typer.echo(f"{len(written)} ficheros en {out}")
    typer.echo(f"Hash del paquete de consultas: {pack_sha256()}")


ENDPOINT_CHECK_EXIT = 4


@endpoint_app.command("check")
@handled
def endpoint_check(
    ctx: typer.Context,
    at: Annotated[
        str | None, typer.Option("--at", help="Momento de la evaluación (por defecto, ahora).")
    ] = None,
) -> None:
    """Evalúa HOST-001…007 por activo. Salida: 0 sin FAIL ni evidencia caducada, 4 si hay
    algún FAIL o evidencia caducada, 2 error de configuración o de lectura."""
    evaluated_at = parse_instant("--at", at) or datetime.now(UTC)
    evaluation = evaluate_workspace(open_workspace(ctx), evaluated_at)
    for result in evaluation.results:
        typer.echo(f"{result.asset_id}  {result.check_id}  {result.status}  {result.reason}")
    for asset_id in evaluation.expired_assets:
        typer.echo(f"{asset_id}: evidencia caducada; bloquearía la preparación hasta renovarla.")
    if evaluation.has_failures or evaluation.expired_assets:
        raise typer.Exit(ENDPOINT_CHECK_EXIT)


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


@categorization_app.command("init")
@handled
def categorization_init(ctx: typer.Context) -> None:
    """Crea scope/categorization.yaml con los servicios del alcance aprobado, sin sobrescribir."""
    try:
        created = init_categorization(open_workspace(ctx))
    except ScopeNotApproved:
        typer.echo("No se crea: el alcance no está aprobado ahora (scope approve).", err=True)
        raise typer.Exit(1) from None
    if created:
        typer.echo(
            "Creado scope/categorization.yaml; rellena las valoraciones "
            "(los criterios del anexo I, 3 están en los comentarios)."
        )
    else:
        typer.echo("Se conserva scope/categorization.yaml.")


@categorization_app.command("approve")
@handled
def categorization_approve(
    ctx: typer.Context,
    by: Annotated[str, typer.Option("--by", help="Nombre del responsable de la seguridad.")],
) -> None:
    """Aprueba la categorización. Solo la aprueba el responsable de la seguridad."""
    store = open_workspace(ctx)
    try:
        approval = approve_categorization(store, by, clock())
    except ScopeNotApproved:
        typer.echo("No se aprueba: el alcance no está aprobado ahora (scope approve).", err=True)
        raise typer.Exit(1) from None
    except CategorizationApprovalRejected as exc:
        typer.echo("No se aprueba la categorización:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(
        f"Categoría {approval.category.value} aprobada por {approval.approved_by} "
        f"({approval.role.value}); vigente hasta {approval.valid_until.date().isoformat()}.\n"
        "El nombre es declarado; no se autentica."
    )


@categorization_app.command("status")
@handled
def categorization_show_status(ctx: typer.Context) -> None:
    """Muestra niveles, categoría, vigencia y compromiso. Sale con 1 si no está vigente."""
    status = categorization_status(open_workspace(ctx), clock())
    typer.echo(f"{status.state.value}")
    if status.computation is not None:
        for item in status.computation.levels:
            level = item.level.value if item.level else "sin nivel"
            by = f" (determinado por {', '.join(item.determined_by)})" if item.determined_by else ""
            typer.echo(f"  {item.dimension.value}: {level}{by}")
        category = status.computation.category
        typer.echo(f"Categoría calculada: {category.value if category else 'indeterminada'}")
        if category is not None and status.state is CategorizationState.CURRENT:
            typer.echo(f"Compromiso: {commitment(category)}. {commitment_notice(category)}")
        else:
            typer.echo("Compromiso: sin compromiso hasta aprobar la categorización.")
    if status.approval is not None:
        typer.echo(
            f"Aprobada por {status.approval.approved_by} el "
            f"{status.approval.approved_at.date().isoformat()}; válida hasta "
            f"{status.approval.valid_until.date().isoformat()}."
        )
    print_errors(status.pending)
    print_errors(status.errors)
    if status.state is not CategorizationState.CURRENT:
        raise typer.Exit(1)


def clock() -> datetime:
    return datetime.now(UTC)


def report_missing_context(exc: ScopeNotApproved | CategorizationNotCurrent) -> typer.Exit:
    if isinstance(exc, ScopeNotApproved):
        typer.echo("El alcance no está aprobado ahora (scope approve).", err=True)
    else:
        typer.echo(
            f"La categorización no está vigente ({exc.status.state.value}); "
            "ver categorization status.",
            err=True,
        )
        print_errors([*exc.status.errors, *exc.status.pending])
    return typer.Exit(1)


@applicability_app.command("propose")
@handled
def applicability_propose(
    ctx: typer.Context,
    confirm: Annotated[
        list[str] | None,
        typer.Option("--confirm", help="Medida cuya decisión humana revisada se confirma."),
    ] = None,
) -> None:
    """Marca APPLICABLE lo que el anexo II exige para la categorización vigente.

    Nunca marca NOT_APPLICABLE ni decide por una persona: una decisión sin fundamento registrado
    o con fundamento distinto queda pendiente hasta confirmarla con --confirm.
    """
    try:
        result = propose_applicability(open_workspace(ctx), clock(), confirm or [])
    except (ScopeNotApproved, CategorizationNotCurrent) as exc:
        raise report_missing_context(exc) from None
    except ApplicabilityRejected as exc:
        typer.echo("No se propone la aplicabilidad:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(
        f"Propuestas {len(result.proposed)} medidas exigidas, "
        f"actualizadas {len(result.refreshed)}, "
        f"retiradas {len(result.retracted)}, "
        f"confirmadas {len(result.confirmed)}; sin cambios {result.unchanged}. "
        "Es una selección, no una conformidad: las demás las decide una persona."
    )
    if result.unconfirmed:
        typer.echo(
            "No confirmadas: "
            + ", ".join(result.unconfirmed)
            + " (no existen o no tienen revisión pendiente).",
            err=True,
        )
    if result.pending_review:
        typer.echo(
            "Revisión pendiente (decisión sin fundamento o con fundamento distinto): "
            + ", ".join(result.pending_review)
            + ". Revísalas y confírmalas con `applicability propose --confirm <id>`.",
            err=True,
        )


def print_exclusions(exclusions: list[Exclusion]) -> None:
    """Las exigidas por el anexo II que se dejan sin aplicar, a la vista antes de firmar."""
    if not exclusions:
        return
    typer.echo("Exigidas por el anexo II y excluidas por ausencia del tipo de activo (2.1.a):")
    for item in exclusions:
        typer.echo(f"  - {item.id} {item.name} [{item.asset_type}]: {item.justification}")


@applicability_app.command("validate")
@handled
def applicability_validate(ctx: typer.Context) -> None:
    """Toda medida con estado y justificación; lo exigido, nunca NOT_APPLICABLE. Falla con 1."""
    try:
        validation = validate_applicability(open_workspace(ctx), clock())
    except (ScopeNotApproved, CategorizationNotCurrent) as exc:
        raise report_missing_context(exc) from None
    if validation.errors:
        typer.echo("La aplicabilidad no es válida:", err=True)
        print_errors(validation.errors)
        raise typer.Exit(1)
    assert validation.measures is not None
    for warning in validation.warnings:
        typer.echo(f"Aviso: {warning}", err=True)
    print_exclusions(validation.exclusions)
    typer.echo(f"Válida: {len(validation.measures.measures)} medidas con estado y justificación.")


@applicability_app.command("approve")
@handled
def applicability_approve(
    ctx: typer.Context,
    by: Annotated[str, typer.Option("--by", help="Nombre del responsable de la seguridad.")],
) -> None:
    """Firma la Declaración de Aplicabilidad (art. 28.2) si la aplicabilidad es válida."""
    store = open_workspace(ctx)
    try:
        validation = validate_applicability(store, clock())
        for warning in validation.warnings:
            typer.echo(f"Aviso: {warning}", err=True)
        print_exclusions(validation.exclusions)
        declaration = approve_declaration(store, by, clock())
    except (ScopeNotApproved, CategorizationNotCurrent) as exc:
        raise report_missing_context(exc) from None
    except DeclarationRejected as exc:
        typer.echo("No se firma la Declaración de Aplicabilidad:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(
        f"Declaración firmada por {declaration.approved_by} ({declaration.role.value}): "
        f"{len(declaration.selected)} medidas seleccionadas, "
        f"{len(declaration.compensations)} compensadas, "
        f"{len(declaration.required_but_absent_assets)} exigidas sin aplicar por ausencia del "
        "tipo de activo (revísalas en la Declaración antes de darla por buena).\n"
        "El nombre es declarado; no se autentica."
    )


@applicability_app.command("status")
@handled
def applicability_show_status(ctx: typer.Context) -> None:
    """Muestra si la Declaración de Aplicabilidad está vigente. Sale con 1 si no lo está."""
    status = declaration_status(open_workspace(ctx), clock())
    hints = {
        DeclarationState.CURRENT: "La Declaración está firmada y nada ha cambiado.",
        DeclarationState.NOT_APPROVED: "Falta firmar la Declaración (applicability approve).",
        DeclarationState.STALE: (
            "Algo cambió después de firmarla (abajo, qué). Para volver a firmarla, en orden: "
            "scope approve → categorization approve → risk approve → applicability propose y "
            "validate → applicability approve (salta los pasos que no necesites):"
        ),
        DeclarationState.INVALID: "La Declaración no es válida:",
    }
    typer.echo(f"{status.state.value}: {hints[status.state]}")
    print_errors(status.errors)
    if status.state is not DeclarationState.CURRENT:
        raise typer.Exit(1)


@applicability_app.command("show")
@handled
def applicability_show(
    ctx: typer.Context,
    annotated: Annotated[
        bool, typer.Option("--annotated", help="Añade el estado de cada medida en la matriz.")
    ] = False,
    save: Annotated[
        bool, typer.Option("--save", help="Guarda una copia en evidence/reports/declaration/.")
    ] = False,
) -> None:
    """Muestra la Declaración de Aplicabilidad en forma legible. Sale con 1 si no está vigente."""
    store = open_workspace(ctx)
    now = clock()
    status = declaration_status(store, now)
    text = render_declaration(store, now, evaluation=assess(store, now) if annotated else None)
    typer.echo(text, nl=False)
    if save and status.declaration is not None:
        key = save_report(store, DECLARATION_REPORTS_PREFIX, now, "md", text)
        typer.echo(f"Guardada en {key}", err=True)
    elif save:
        typer.echo("No hay Declaración firmada y legible: no se guarda.", err=True)
    if status.state is not DeclarationState.CURRENT:
        raise typer.Exit(1)


def backup_settings(ctx: typer.Context) -> BackupSettings:
    configured = load_settings(resolve_settings_path(ctx.obj)).backup
    if configured is None:
        raise fail(
            "Faltan los ajustes de la copia: añade backup.restic_repository al fichero de ajustes."
        )
    return configured


def record_setup_failure(
    ctx: typer.Context,
    recorder: SetupFailureRecorder | None,
    repository: str,
    failure: SetupFailure,
) -> None:
    """Deja el intento como `failed` si el workspace se abre; si no, no hay dónde registrarlo."""
    if recorder is None:
        return
    try:
        recorder(open_workspace(ctx), repository, failure)
    except (WorkspaceError, OSError):
        return


def backup_restic(
    ctx: typer.Context, recorder: SetupFailureRecorder | None = None
) -> tuple[Restic, BackupSettings, BackupSecrets]:
    """Restic y sus secretos; `recorder` deja constancia del intento si falta algo."""
    try:
        configured = backup_settings(ctx)
    except typer.Exit:
        record_setup_failure(ctx, recorder, "", "invalid_settings")
        raise
    try:
        secrets = load_secrets(configured.restic_repository)
    except BackupError:
        record_setup_failure(ctx, recorder, configured.restic_repository, "invalid_credentials")
        raise
    restic = Restic(configured.restic_repository, secrets, region=configured.region)
    return restic, configured, secrets


def optional_repository_id(restic: Restic) -> str | None:
    """Id del repositorio, o None si no se puede leer (el registro lo dejará sin id)."""
    try:
        return restic.repository_id()
    except BackupError:
        return None


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
    restic, configured, secrets = backup_restic(ctx, failed_run_recorder(clock))
    run = run_backup(
        open_workspace(ctx),
        restic,
        configured.restic_repository,
        secrets,
        clock,
        optional_repository_id(restic),
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
    reject_inside_workspace(ctx, target)
    restic, configured, _ = backup_restic(ctx, failed_restore_recorder(target, clock))
    recorded: list[BackupRun] = []
    store = None
    try:
        store = open_workspace(ctx)
        recorded, _unreadable = list_runs(store)
    except WorkspaceError:
        store = None
        typer.echo(
            "No se puede leer el workspace; la restauración no se comparará ni se registrará.",
            err=True,
        )
    if store is not None and not recorded:
        store = None  # workspace vacío: el import --all posterior debe recibir solo el snapshot
        typer.echo("El workspace no tiene copias registradas; esta restauración no se registra.")
    report = restore_recorded(
        store,
        restic,
        snapshot,
        target,
        recorded,
        clock,
        optional_repository_id(restic),
    )
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


@backup_app.command("check")
@handled
def backup_check(
    ctx: typer.Context,
    read_data_subset: Annotated[
        str | None,
        typer.Option(help="Lee este porcentaje de los datos del repositorio (1%-100%)."),
    ] = None,
    read_data: Annotated[bool, typer.Option(help="Lee todos los datos del repositorio.")] = False,
) -> None:
    """Ejecuta `restic check` y registra el intento y su alcance exacto.

    Toma el lock del repositorio y puede tardar; sin opciones solo comprueba metadatos.
    """
    scope = check_scope(read_data_subset, read_data)
    restic, _, _ = backup_restic(ctx, failed_check_recorder(scope, clock))
    check = run_check(
        open_workspace(ctx),
        restic,
        optional_repository_id(restic),
        clock,
        read_data_subset=read_data_subset,
        read_data=read_data,
    )
    detail = f"alcance {check.scope}, {check.read_data_percent}% de los datos leído"
    if check.status == "failed":
        typer.echo(
            f"La comprobación {check.check_id} ha fallado ({check.failure}; "
            f"errores: {check.num_errors}, packs rotos: {check.broken_packs}): {detail}.",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(f"Comprobación {check.check_id} completada sin errores: {detail}.")


@restore_test_app.command("record")
@handled
def backup_restore_test_record(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Fichero YAML con la prueba de restauración.")],
) -> None:
    """Registra una prueba de restauración atada a una restauración hecha con `backup restore`.

    Guarda el registro en evidence/restore-tests y lo importa como evidencia manual; el revisor
    es declarado, no se autentica.
    """
    try:
        data = file.read_bytes()
    except OSError:
        raise fail("No se puede leer el fichero indicado.") from None
    restic, _, _ = backup_restic(ctx)
    try:
        record = record_restore_test(
            open_workspace(ctx), restic.repository_id(), data, datetime.now(UTC)
        )
    except ValidationError as exc:
        typer.echo("No se registra el restore test:", err=True)
        print_errors(describe_validation("restore-test record", exc))
        raise typer.Exit(1) from None
    except BackupError as exc:
        raise fail(f"No se registra el restore test: {exc}") from None
    typer.echo(
        f"Restore test {record.test_id} registrado y guardado como evidencia manual.\n"
        "El revisor es declarado; no se autentica."
    )


_IMMUTABILITY_MARKS = {
    "protected": "protegido",
    "unprotected": "NO protegido",
    "inconclusive": "NO CONCLUYENTE",
}


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
        typer.echo(f"{result.prefix}/: {_IMMUTABILITY_MARKS[result.status]} ({result.detail})")
    typer.echo(check.note)
    if check.status == "unprotected":
        typer.echo("El destino NO es inmutable: se pudo borrar o sobrescribir algún canario.")
    elif check.status == "inconclusive":
        typer.echo(
            "Prueba no concluyente: algún canario no se pudo leer (error, timeout o permisos). "
            "Un error de lectura no prueba protección; corrige el acceso y repite."
        )
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


def print_scope_block(exc: ScopeNotApproved) -> None:
    state = exc.status.state
    match state:
        case ScopeState.INVALID:
            typer.echo("  El alcance no es válido; corrige estos errores:", err=True)
        case ScopeState.STALE:
            typer.echo(
                "  El alcance cambió después de aprobarse; apruébalo de nuevo con "
                "`ens-evidence scope approve`.",
                err=True,
            )
        case _:
            typer.echo(
                "  El alcance está pendiente de aprobación; apruébalo con "
                "`ens-evidence scope approve`.",
                err=True,
            )
    print_errors(exc.status.errors)


@docs_app.command("questions")
@handled
def docs_questions() -> None:
    """Lista las preguntas que las plantillas piden a la organización."""
    templates = load_templates()
    for question in all_questions(templates):
        documents = [
            t.id for t in templates if any(q.id == question.id for q in t.template.questions)
        ]
        typer.echo(
            f"{question.id} [{question.type.value}, {requirement_label(question)}] {question.text}"
        )
        typer.echo(f"  cláusula {question.clause}; documentos: {', '.join(documents)}")
        if question.help:
            typer.echo(f"  {question.help}")


@docs_app.command("init")
@handled
def docs_init(ctx: typer.Context) -> None:
    """Crea org/answers.yaml con todas las preguntas vacías; no sobrescribe."""
    if write_if_absent(open_workspace(ctx), ANSWERS_KEY, skeleton(load_templates())):
        typer.echo(f"Creado: {ANSWERS_KEY}. Rellénalo y ejecuta `ens-evidence docs build`.")
    else:
        typer.echo(f"{ANSWERS_KEY} ya existe y se conserva.")


@docs_app.command("build")
@handled
def docs_build(ctx: typer.Context) -> None:
    """Genera los documentos en org/docs/. Exige alcance aprobado y respuestas completas."""
    try:
        report = build_documents(open_workspace(ctx))
    except ScopeNotApproved as exc:
        typer.echo("No se genera nada:", err=True)
        print_scope_block(exc)
        raise typer.Exit(1) from None
    except AnswersInvalid as exc:
        typer.echo("No se genera nada: faltan respuestas o no son válidas.", err=True)
        print_errors(exc.report.errors())
        raise typer.Exit(1) from None
    for warning in report.warnings:
        typer.echo(f"Aviso: {warning}", err=True)
    labels = {
        BuildStatus.CREATED: "creado",
        BuildStatus.UPDATED: "actualizado",
        BuildStatus.UNCHANGED: "sin cambios",
    }
    for result in report.documents:
        typer.echo(f"{result.name}: {labels[result.status]} (sha256 {result.document_sha256})")
        if result.edited_by_hand:
            typer.echo(
                f"  Aviso: {result.name} no coincidía con su registro (edición manual o "
                "escritura interrumpida); la versión anterior queda en org/docs/history/.",
                err=True,
            )


@docs_app.command("approve")
@handled
def docs_approve(
    ctx: typer.Context,
    document: Annotated[str, typer.Argument(help="Documento (ver `docs status`).")],
    by: Annotated[str, typer.Option("--by", help="Nombre de quien aprueba (declarado).")],
    role: Annotated[Role, typer.Option(help="Rol de quien aprueba.")],
) -> None:
    """Aprueba un documento generado. El rol exigido depende del documento."""
    try:
        approval = approve_document(open_workspace(ctx), document, by, role, clock())
    except UnknownDocument:
        raise fail(f"Documento desconocido: {document}") from None
    except ScopeNotApproved as exc:
        typer.echo("No se aprueba el documento:", err=True)
        print_scope_block(exc)
        raise typer.Exit(1) from None
    except DocumentApprovalRejected as exc:
        typer.echo("No se aprueba el documento:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(
        f"{document} aprobado por {approval.approved_by} ({approval.role.value}) "
        f"el {approval.approved_at.isoformat()}. sha256 {approval.document_sha256}\n"
        "El nombre es declarado; no se autentica. Aprobar un documento no cambia el estado "
        "de ninguna medida."
    )


@docs_app.command("status")
@handled
def docs_status(ctx: typer.Context) -> None:
    """Estado de cada documento: sin generar, generado, aprobado (vigente) u obsoleto."""
    labels = {
        DocumentState.NOT_GENERATED: "sin generar",
        DocumentState.GENERATED: "generado, sin aprobar",
        DocumentState.APPROVED: "aprobado y vigente",
        DocumentState.OBSOLETE: "aprobado pero obsoleto",
    }
    statuses = all_statuses(open_workspace(ctx))
    for status in statuses:
        typer.echo(f"{status.name}: {labels[status.state]}")
        if status.approval:
            approval = status.approval
            typer.echo(
                f"  aprobado por {approval.approved_by} ({approval.role.value}) "
                f"el {approval.approved_at.isoformat()}"
            )
        for reason in status.reasons:
            typer.echo(f"  - {reason}")
    if any(s.state is not DocumentState.APPROVED for s in statuses):
        raise typer.Exit(1)


@risk_app.command("init")
@handled
def risk_init(ctx: typer.Context) -> None:
    """Crea las plantillas de risk/ sin sobrescribir lo existente. No genera valores de riesgo."""
    created, kept = init_risk(open_workspace(ctx))
    for key in created:
        typer.echo(f"Creado {key} (propuesta editable).")
    for key in kept:
        typer.echo(f"Se conserva {key}.")
    typer.echo("Siguiente paso: declara tu metodología en risk/methodology.yaml.")


@risk_app.command("validate")
@handled
def risk_validate(ctx: typer.Context) -> None:
    """Valida risk/ contra metodología, inventario y catálogo. Sale con 1 si hay errores."""
    try:
        analysis, errors = validate_risk(open_workspace(ctx), clock())
    except ScopeNotApproved:
        typer.echo("No se valida: el alcance no está aprobado ahora (scope approve).", err=True)
        raise typer.Exit(1) from None
    if analysis is None:
        typer.echo("El análisis de riesgos no es válido:", err=True)
        print_errors(errors)
        raise typer.Exit(1)
    typer.echo(
        f"Válido: {len(analysis.register.risks)} riesgos, {len(analysis.plan.actions)} acciones. "
        "La herramienta no evalúa si los valores son acertados."
    )


@risk_app.command("approve")
@handled
def risk_approve(
    ctx: typer.Context,
    by: Annotated[str, typer.Option("--by", help="Nombre del responsable de la seguridad.")],
) -> None:
    """Registra la revisión anual del análisis. Solo la hace el responsable de la seguridad."""
    try:
        approval = approve_risk(open_workspace(ctx), by, clock())
    except ScopeNotApproved:
        typer.echo("No se aprueba: el alcance no está aprobado ahora (scope approve).", err=True)
        raise typer.Exit(1) from None
    except RiskApprovalRejected as exc:
        typer.echo("No se aprueba el análisis de riesgos:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(
        f"Análisis de riesgos aprobado por {approval.approved_by} ({approval.role.value}); "
        f"vigente hasta {approval.valid_until.date()}.\n"
        "El nombre es declarado; no se autentica."
    )


@risk_app.command("status")
@handled
def risk_show_status(ctx: typer.Context) -> None:
    """Muestra la vigencia de la aprobación del análisis. Sale con 1 si no está vigente."""
    status = risk_status(open_workspace(ctx), clock())
    until = status.approval.valid_until.date() if status.approval else ""
    hints = {
        RiskState.APPROVED: f"El análisis está aprobado y vigente hasta {until}.",
        RiskState.NOT_APPROVED: "Falta aprobar el análisis (risk approve).",
        RiskState.STALE: "ya no vale lo que se aprobó; hay que aprobarlo de nuevo:",
        RiskState.EXPIRED: f"La aprobación caducó el {until}; revisa y aprueba de nuevo.",
        RiskState.INVALID: "La aprobación no es válida:",
    }
    typer.echo(f"{status.state.value}: {hints[status.state]}")
    print_errors(status.errors)
    if status.state is not RiskState.APPROVED:
        raise typer.Exit(1)


def matrix_rejected(title: str, exc: MatrixRejected) -> typer.Exit:
    typer.echo(title, err=True)
    print_errors(exc.errors)
    return typer.Exit(1)


def print_coverage(plan: ReviewPlan) -> None:
    for obligation in plan.attribution:
        typer.echo(f"  {obligation_label(obligation.obligation)}:")
        for clause in obligation.clauses:
            sources = ", ".join(clause.sources) or "sin fuente que cuente"
            typer.echo(f"    {clause.unit}  <- {sources}")
    for note in plan.notes:
        typer.echo(f"  Aviso: {note}", err=True)


@matrix_app.command("init")
@handled
def matrix_init(ctx: typer.Context) -> None:
    """Crea una entrada por cada medida de la Declaración vigente; conserva lo existente."""
    try:
        outcome = init_matrix(open_workspace(ctx), clock())
    except MatrixRejected as exc:
        raise matrix_rejected("No se inicializa la matriz:", exc) from None
    typer.echo(
        f"Matriz inicializada: {len(outcome.created)} entradas nuevas, {outcome.kept} conservadas."
    )
    if outcome.extra:
        typer.echo(
            "Aviso: sobran entradas de medidas que ya no están en la Declaración: "
            + ", ".join(outcome.extra)
            + " (se conservan).",
            err=True,
        )
    if outcome.catalog_updated:
        typer.echo(
            "matrix.yaml pasa de la versión del catálogo "
            f"{outcome.file_catalog.consolidation_date} a la actual "
            f"{outcome.current_catalog.consolidation_date}."
        )


@matrix_app.command("set")
@handled
def matrix_set(
    ctx: typer.Context,
    measure: Annotated[str, typer.Argument(help="p. ej. op.acc.5")],
    owner: Annotated[str | None, typer.Option("--owner", help="Nombre del responsable.")] = None,
    owner_role: Annotated[str | None, typer.Option("--owner-role", help="Rol.")] = None,
    every: Annotated[
        int | None, typer.Option("--every", help="Periodicidad de revisión en días (1 a 365).")
    ] = None,
) -> None:
    """Fija el responsable y la periodicidad de revisión de una medida."""
    try:
        entry = set_entry(
            open_workspace(ctx), measure, owner_name=owner, owner_role=owner_role, every_days=every
        )
    except MatrixRejected as exc:
        raise matrix_rejected(f"No se actualiza {measure}:", exc) from None
    who = f"{entry.owner.name} ({entry.owner.role.value})" if entry.owner else "sin responsable"
    days = f"cada {entry.review_every_days} días" if entry.review_every_days else "sin periodicidad"
    typer.echo(f"{measure}: {who}; revisión {days}.")


def source_target(evidence: str | None, check: str | None, document: str | None) -> tuple[str, str]:
    given = [(k, v) for k, v in ((EVIDENCE, evidence), (CHECK, check), (DOCUMENT, document)) if v]
    if len(given) != 1:
        raise fail("Indica exactamente una fuente: --evidence, --check o --document.")
    return given[0]


@matrix_app.command("link")
@handled
def matrix_link(
    ctx: typer.Context,
    measure: Annotated[str, typer.Argument(help="p. ej. op.acc.5")],
    covers: Annotated[
        str, typer.Option("--covers", help="Qué cubre: base, R1, compensation o ids de cláusula.")
    ],
    evidence: Annotated[str | None, typer.Option("--evidence", help="Id EV-… manual.")] = None,
    check: Annotated[str | None, typer.Option("--check", help="Id de un check.")] = None,
    document: Annotated[str | None, typer.Option("--document", help="Documento de docs.")] = None,
) -> None:
    """Vincula una fuente (evidencia manual, check o documento) a las cláusulas que cubre."""
    kind, target = source_target(evidence, check, document)
    try:
        link = build_link(kind, target, parse_covers(covers))
        outcome = add_link(open_workspace(ctx), clock(), measure, link)
    except MatrixRejected as exc:
        raise matrix_rejected(f"No se vincula a {measure}:", exc) from None
    typer.echo(f"Vinculado {link_label(link)} a {measure}.")
    typer.echo(f"Cubre: {', '.join(outcome.declared)}.")
    typer.echo(
        f"Sin cubrir por ningún enlace: {', '.join(outcome.pending)}."
        if outcome.pending
        else "Todas las cláusulas tienen algún enlace declarado."
    )
    typer.echo("Es lo que se declara cubrir; la revisión de una persona lo confirma.")


@matrix_app.command("unlink")
@handled
def matrix_unlink(
    ctx: typer.Context,
    measure: Annotated[str, typer.Argument(help="p. ej. op.acc.5")],
    covers: Annotated[
        str | None,
        typer.Option("--covers", help="Quita solo estas coberturas (por defecto, todo el enlace)."),
    ] = None,
    evidence: Annotated[str | None, typer.Option("--evidence", help="Id EV-… manual.")] = None,
    check: Annotated[str | None, typer.Option("--check", help="Id de un check.")] = None,
    document: Annotated[str | None, typer.Option("--document", help="Documento de docs.")] = None,
) -> None:
    """Desvincula una fuente de una medida. Las revisiones anteriores quedan caducadas."""
    kind, target = source_target(evidence, check, document)
    try:
        outcome = remove_link(
            open_workspace(ctx),
            clock(),
            measure,
            kind,
            target,
            parse_covers(covers) if covers is not None else None,
        )
    except MatrixRejected as exc:
        raise matrix_rejected(f"No se desvincula de {measure}:", exc) from None
    typer.echo(
        f"{'Desvinculado' if outcome.removed else 'Coberturas quitadas de'} {target} en {measure}."
    )
    if outcome.pending:
        typer.echo(f"Sin cubrir por ningún enlace: {', '.join(outcome.pending)}.")


@matrix_app.command("review")
@handled
def matrix_review(
    ctx: typer.Context,
    measure: Annotated[str, typer.Argument(help="p. ej. op.acc.5")],
    by: Annotated[str, typer.Option("--by", help="Nombre de quien revisa (declarado).")],
    role: Annotated[
        str, typer.Option("--role", help="system_administrator o system_administrator_delegate.")
    ],
    conclusion: Annotated[str, typer.Option("--conclusion", help="Conclusión de la revisión.")],
    assessment: Annotated[
        str | None,
        typer.Option(
            "--assessment",
            help="COMPLIANT, NON_COMPLIANT o REQUIRES_REVIEW (solo en medidas aplicables).",
        ),
    ] = None,
    at: Annotated[str | None, typer.Option("--at", help="Cuándo se revisó (no futuro).")] = None,
) -> None:
    """Registra la revisión de una persona sobre una medida. La herramienta no declara
    conformidad: `COMPLIANT` se rechaza si falta responsable, periodicidad o cobertura."""
    reviewed_at = parse_instant("--at", at)
    store = open_workspace(ctx)
    try:
        plan = prepare_review(
            store,
            clock(),
            measure,
            by=by,
            role=role,
            conclusion=conclusion,
            assessment=assessment,
            at=reviewed_at,
        )
    except MatrixRejected as exc:
        raise matrix_rejected(f"No se registra la revisión de {measure}:", exc) from None
    if plan.row.obligations:
        typer.echo(f"Cobertura atribuida a {measure}:")
        print_coverage(plan)
    if plan.problems:
        typer.echo(f"No se registra la revisión de {measure}:", err=True)
        print_errors(list(plan.problems))
        raise typer.Exit(1)
    record_review(store, plan)
    typer.echo(f"Revisión registrada para {measure}. El nombre es declarado; no se autentica.")


def print_row_status(status: RowStatus) -> None:
    spec = status.row.spec
    if spec.kind is RowKind.SELECTED:
        if status.implemented:
            implemented = "sí"
        else:
            implemented = "sin revisar" if status.row.review is None else "no"
        line = f"{spec.measure_id}  seleccionada  implantada: {implemented}  "
    else:
        line = f"{spec.measure_id}  no aplicable  "
    line += f"revisión regular: {'sí' if status.regularly_reviewed else 'no'}"
    if status.sources:
        line += f"  evidencias: {', '.join(status.sources)}"
    typer.echo(line)
    if status.has_check_links:
        typer.echo(f"    {CHECKS_PENDING}")
    for obligation in spec.obligations:
        if obligation.compensating_measures:
            typer.echo(
                "    compensada por: " + "; ".join(obligation.compensating_measures) + ". "
                "La evidencia ha de mostrar esas medidas, no las cláusulas que sustituyen."
            )
    for text in blocker_lines(status.blockers, spec.compensated_clauses):
        typer.echo(f"    bloqueo: {text}")


@matrix_app.command("status")
@handled
def matrix_show_status(
    ctx: typer.Context,
    measure: Annotated[
        str | None, typer.Argument(help="Solo esta medida (p. ej. op.acc.5); por defecto, todas.")
    ] = None,
    at: Annotated[str | None, typer.Option("--at", help="Instante a evaluar.")] = None,
) -> None:
    """Estado de cada medida y preparación. Sale con 1 si hay bloqueos o ficheros ilegibles."""
    instant = parse_instant("--at", at) or clock()
    result, rows = matrix_status(open_workspace(ctx), instant)
    shown = [s for s in rows if measure is None or s.row.spec.measure_id == measure]
    if measure is not None and not shown:
        raise fail(f"{measure} no es una medida de la Declaración de Aplicabilidad.", 1)
    typer.echo(f"Declaración de Aplicabilidad: {DECLARATION_LABELS[result.declaration_state]}.")
    typer.echo(f"Aviso: {CHECKS_PENDING}.")
    for status in shown:
        print_row_status(status)
    print_errors(list(result.errors))
    readiness = result.readiness
    if measure is None:
        count = len(readiness.blockers)
        blocked_by = f"{count} bloqueo" if count == 1 else f"{count} bloqueos"
        if result.declaration_state is not DeclarationState.CURRENT:
            blocked_by = "todas las medidas bloqueadas: la Declaración no está vigente"
        typer.echo(
            f"Preparación: {READINESS_LABELS[readiness.readiness]}, {blocked_by}. "
            "La herramienta no declara conformidad; la valoración es de la persona que revisa."
        )
    blocked = bool(readiness.blockers) if measure is None else any(s.blockers for s in shown)
    if blocked or result.errors:
        raise typer.Exit(1)


class ExportFormat(StrEnum):
    JSON = "json"
    MD = "md"


@matrix_app.command("export")
@handled
def matrix_export(
    ctx: typer.Context,
    fmt: Annotated[
        ExportFormat, typer.Option("--format", help="Formato de la exportación.")
    ] = ExportFormat.JSON,
    at: Annotated[str | None, typer.Option("--at", help="Instante a evaluar.")] = None,
    stdout: Annotated[
        bool, typer.Option("--stdout", help="Imprime en vez de guardar en el workspace.")
    ] = False,
) -> None:
    """Exporta la matriz con sus fuentes y marcas de tiempo a `evidence/reports/matrix/`.
    Falla si falta alguna fila de la Declaración. No declara conformidad."""
    store = open_workspace(ctx)
    generated_at = clock()
    evaluation = assess(store, parse_instant("--at", at) or generated_at)
    try:
        export = build_export(store, evaluation, generated_at=generated_at)
    except ExportRejected as exc:
        typer.echo("No se exporta la matriz:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    text = render_json(export) if fmt is ExportFormat.JSON else render_markdown(export)
    if stdout:
        typer.echo(text, nl=False)
        return
    typer.echo(save_report(store, MATRIX_REPORTS_PREFIX, generated_at, fmt.value, text))


def connector_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.load_entry_points()
    return registry


@app.command()
@handled
def collect(
    ctx: typer.Context,
    source: Annotated[str | None, typer.Argument(help="Conector a ejecutar.")] = None,
    all_sources: Annotated[bool, typer.Option("--all", help="Todos los conectores.")] = False,
) -> None:
    """Recoge evidencias. Salida: 0 todo recogido, 3 parcial (secciones o estado global
    sin permiso/no soportado), 1 error."""
    if all_sources or source is None:
        not_implemented()
    try:
        registry = connector_registry()
    except ConnectorLoadError:
        raise fail(
            "No se pueden cargar los conectores instalados: hay un plugin defectuoso. "
            "Revisa los paquetes que declaran el grupo ens_evidence.connectors."
        ) from None
    try:
        connector_class = registry.get(source)
    except KeyError:
        raise fail(
            f"Conector desconocido: {source}. Disponibles: {', '.join(registry.names())}."
        ) from None
    settings = resolved_settings(ctx)
    store = open_store(settings)
    config = load_connector_config(store, source, connector_class.config_model)
    connector = connector_class.from_settings(settings)
    result = connector.collect(connector.prepare(config, store))
    manifest = import_collected(store, result, collector_version=__version__, now=datetime.now(UTC))
    typer.echo(f"Evidencia registrada: {manifest.id}")
    sections = result.data.get("sections", {})
    for name, section in sections.items():
        typer.echo(f"  {name}: {section['status']}")
    raise typer.Exit(collect_exit_code(result.collection_status, result.data, sections))


def collect_exit_code(status: CollectionStatus, data: dict, sections: dict) -> int:
    match status:
        case CollectionStatus.SUCCESS:
            partial = any(s["status"] != CollectionStatus.SUCCESS for s in sections.values())
            return 3 if partial else 0
        case CollectionStatus.PERMISSION_DENIED:
            typer.echo("Recogida incompleta: permiso denegado.", err=True)
            return 3
        case CollectionStatus.NOT_SUPPORTED:
            typer.echo("Recogida incompleta: no soportada por el conector.", err=True)
            return 3
        case CollectionStatus.ERROR:
            typer.echo(f"Error: {data.get('error')}", err=True)
            return 1


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
    source: Annotated[str, typer.Option(help="Origen: manual o endpoint (paquete del script).")],
    asset: Annotated[str, typer.Option(help="Id del activo al que se refiere.")],
    by: Annotated[str, typer.Option("--by", help="Nombre de quien aporta (declarado).")],
    collected_at: Annotated[
        str | None, typer.Option(help="Momento al que se refiere (por defecto, ahora).")
    ] = None,
    valid_until: Annotated[str | None, typer.Option(help="Caducidad de la evidencia.")] = None,
) -> None:
    """Registra una evidencia manual con su hash, autor y fecha."""
    if source == ENDPOINT_SOURCE:
        import_endpoint_package(ctx, file, asset, by, collected_at, valid_until)
        return
    if source != MANUAL_SOURCE:
        raise fail(
            f"--source: solo se admite {MANUAL_SOURCE} o {ENDPOINT_SOURCE}; "
            "las demás las recoge un collector."
        )
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


def import_endpoint_package(
    ctx: typer.Context,
    file: Path,
    asset: str,
    by: str,
    collected_at: str | None,
    valid_until: str | None,
) -> None:
    if collected_at is not None or valid_until is not None:
        raise fail(
            "--collected-at y --valid-until no se admiten con --source endpoint: "
            "salen del paquete y de max_evidence_age_days."
        )
    try:
        with file.open("rb") as handle:
            data = handle.read(MAX_PACKAGE_BYTES + 1)
    except OSError:
        raise fail("No se puede leer el fichero indicado.") from None
    package, result = import_endpoint(
        open_workspace(ctx), data, asset_id=asset, submitted_by=by, now=datetime.now(UTC)
    )
    typer.echo(
        f"Evidencia registrada: {result.id} (paquete original: {package.id})\n"
        f"Huella sha256 del paquete: {package.artifact.sha256}\n"
        f"Válida hasta: {result.valid_until:%Y-%m-%dT%H:%M:%S%z}\n"
        "Es una atestación manual: el nombre de quien aporta es declarado; no se autentica."
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
