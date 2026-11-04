import functools
import json
import os
import re
import shutil
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, tzinfo
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
import yaml
from pydantic import ValidationError

from ens_evidence import schemas
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
from ens_evidence.assessment import (
    assessment_status,
    count_statuses,
    render_status_json,
    render_status_text,
)
from ens_evidence.assessment.dossier import (
    TransitionRejected,
    build_snapshot,
    current_inputs,
    dossier_status,
    entries,
    record_transition,
    render_snapshot_md,
    scan_transitions,
)
from ens_evidence.assessment.dossier.build import DossierRejected
from ens_evidence.assessment.dossier.derived import (
    has_markdown,
    markdown_problems,
    render_view,
    save_view,
)
from ens_evidence.assessment.dossier.model import SECTION_TITLES, DossierSnapshot
from ens_evidence.assessment.dossier.package import (
    INDEX_NAME,
    PACKAGE_DIR,
    PackageRejected,
    has_package,
    save_package,
)
from ens_evidence.assessment.dossier.sections_md import category_name
from ens_evidence.assessment.dossier.status import (
    NOT_READY_NOTICE,
    UNKNOWN,
    incomplete_sections,
    next_step,
    snapshot_readiness_blocked,
)
from ens_evidence.assessment.dossier.store import (
    ASSESSMENTS_PREFIX,
    DossierUnreadable,
    list_dossiers,
    load_snapshot,
    save_markdown,
    save_snapshot,
)
from ens_evidence.assessment.dossier.verification import package_required, verification_problems
from ens_evidence.backup import (
    IMMUTABILITY_PREFIX,
    BackupError,
    BackupFailure,
    BackupRun,
    BackupSecrets,
    Restic,
    SetupFailureRecorder,
    backup_status,
    check_scope,
    dump,
    failed_check_recorder,
    failed_restore_recorder,
    list_runs,
    load_secrets,
    optional_repository_id,
    parse_s3_repository,
    prepare_restic,
    record_restore_test,
    restore_recorded,
    run_check,
    run_configured_backup,
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
from ens_evidence.checks.engine import CONFIG_INVALID, ConnectorOutcome, OutcomeKind, run_all
from ens_evidence.checks.inputs import NO_EVIDENCE
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.checks.store import scan_runs
from ens_evidence.connectors.config import connector_config_key
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
from ens_evidence.incidents import (
    ActionKind,
    IncidentRejected,
    IncidentState,
    IncidentStatus,
    Recorded,
    RecordKind,
    TriState,
    check_all_incidents,
    check_incident,
    next_incident_id,
    record_history,
    record_incident,
    simulation_procedure,
    verify_all_incidents,
)
from ens_evidence.incidents.model import INCIDENT_ID_PATTERN, ReviewResult
from ens_evidence.incidents.present import RECORD_LABELS, SIMULATION_LABEL, list_line, show_lines
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.matrix import (
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
    INCIDENT_REGISTER_NOTICE,
    READINESS_LABELS,
    SIMULATION_REVIEW_NOTICE,
    blocker_lines,
    collect_step,
    obligation_label,
    role_label,
)
from ens_evidence.models.dossier import CorrectiveMeasure
from ens_evidence.models.enums import Category, CheckStatus, CollectionStatus, DossierState, Role
from ens_evidence.models.preaudit import (
    PROPOSAL_NOTICE,
    DeterministicRule,
    DiscardReason,
    DispositionState,
    Finding,
    Layer,
    LayerStatus,
    PreAuditRecord,
    Provider,
    Ref,
    RefKind,
)
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
from ens_evidence.preaudit import service as preaudit
from ens_evidence.preaudit.deterministic import RULE_LABELS
from ens_evidence.preaudit.findings import FindingState, FindingsView, findings_view
from ens_evidence.preaudit.review import ReviewRejected, review
from ens_evidence.reports.daily import build_daily
from ens_evidence.reports.model import DailyReport, Unavailable
from ens_evidence.reports.model import render_json as render_report_json
from ens_evidence.reports.monthly import build_monthly, previous_month
from ens_evidence.reports.render_html import render_daily_html, render_monthly_html
from ens_evidence.reports.store import load_json_strict, save_pair
from ens_evidence.risk import (
    RiskApprovalRejected,
    RiskState,
    approve_risk,
    init_risk,
    risk_status,
    validate_risk,
)
from ens_evidence.scheduling.calendar import JobKind
from ens_evidence.scheduling.clock import SystemClock
from ens_evidence.scheduling.collect import (
    CollectOutcome,
    Plugins,
    collect_all,
    collect_one,
    load_plugins,
)
from ens_evidence.scheduling.config import (
    SCHEDULE_KEY,
    CollectConfig,
    ScheduleConfig,
    default_schedule_yaml,
    load_schedule_config,
)
from ens_evidence.scheduling.cycle import run_cycle
from ens_evidence.scheduling.scheduler import (
    CycleExecutor,
    Scheduler,
    file_heartbeat,
    render_status,
    scheduler_status,
    stop_on_signals,
    workspace_done_periods,
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
from ens_evidence.scope.edit import ScopeEditRejected, add_asset, parse_field, set_system
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.models import AssetType, Deployment
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.scope.sync import QUESTIONNAIRE_KEY, SCOPE_KEYS, synced_keys
from ens_evidence.workspace import (
    AlreadyExists,
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
from ens_evidence.workspace.cycle_lock import CycleBusy, CycleContext, CycleLock

app = typer.Typer(help="Prepara evidencias para el ENS. No certifica.", no_args_is_help=True)
report_app = typer.Typer(help="Informes.", no_args_is_help=True)
scheduler_app = typer.Typer(help="Planificador de informes.", no_args_is_help=True)
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
incident_app = typer.Typer(
    help="Registro de incidentes de seguridad (op.exp.7 y op.exp.9).", no_args_is_help=True
)
dossier_app = typer.Typer(
    help="Expediente de autoevaluación: lo genera la herramienta; las personas lo avanzan.",
    no_args_is_help=True,
)
preaudit_app = typer.Typer(
    help="Pre-auditoría del expediente: propone hallazgos; nunca dictamina.", no_args_is_help=True
)
assessment_app.add_typer(dossier_app, name="dossier")
app.add_typer(report_app, name="report")
app.add_typer(scheduler_app, name="scheduler")
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
app.add_typer(incident_app, name="incident")
app.add_typer(preaudit_app, name="preaudit")

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


@scope_app.command("add-asset")
@handled
def scope_add_asset(
    ctx: typer.Context,
    asset_id: Annotated[str, typer.Option("--id", help="Identificador único del activo.")],
    asset_type: Annotated[AssetType, typer.Option("--type", help="Tipo de activo.")],
    fields: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help="Campo del activo, campo=valor (repetible; listas separadas por comas).",
        ),
    ] = None,
    planned: Annotated[
        bool, typer.Option("--planned", help="El activo aún no está desplegado.")
    ] = False,
    include: Annotated[bool, typer.Option("--include", help="Inclúyelo en el alcance.")] = False,
) -> None:
    """Añade un activo al inventario, sin tocar los demás. No aprueba nada."""
    values: dict[str, Any] = {
        "id": asset_id,
        "type": asset_type.value,
        "deployment": (Deployment.PLANNED if planned else Deployment.DEPLOYED).value,
    }
    try:
        for item in fields or []:
            name, separator, value = item.partition("=")
            if not separator or not name.strip():
                raise ScopeEditRejected([f"--set {item!r}: usa campo=valor"])
            values[name.strip()] = parse_field(name.strip(), value)
        revoked = add_asset(open_workspace(ctx), values, include, datetime.now(UTC))
    except ScopeEditRejected as exc:
        typer.echo("No se añade el activo:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo(f"Activo {asset_id} añadido{' e incluido en el alcance' if include else ''}.")
    if revoked:
        typer.echo("Se revoca la aprobación del alcance; hay que aprobarlo de nuevo.")
    typer.echo("Valida con inventory validate y aprueba con scope approve.")


@scope_app.command("set-system")
@handled
def scope_set_system(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Nombre del sistema.")],
    description: Annotated[str, typer.Option("--description", help="Descripción del sistema.")],
) -> None:
    """Rellena el nombre y la descripción del sistema. No aprueba nada."""
    try:
        revoked = set_system(open_workspace(ctx), name, description, datetime.now(UTC))
    except ScopeEditRejected as exc:
        typer.echo("No se cambia el sistema:", err=True)
        print_errors(exc.errors)
        raise typer.Exit(1) from None
    typer.echo("Sistema actualizado.")
    if revoked:
        typer.echo("Se revoca la aprobación del alcance; hay que aprobarlo de nuevo.")


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


def check_source() -> StoredCheckResults:
    """Las ejecuciones del motor de checks que leen la matriz y la preparación."""
    return StoredCheckResults(reference_now=clock())


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
    evaluation = assess(store, now, checks=check_source()) if annotated else None
    text = render_declaration(store, now, evaluation=evaluation)
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
        restic, secrets = prepare_restic(configured)
    except BackupError:
        record_setup_failure(ctx, recorder, configured.restic_repository, "invalid_credentials")
        raise
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
    settings = resolved_settings(ctx)
    store = open_store(settings)
    with exclusive_cycle(settings) as context:
        outcome = run_configured_backup(
            store, settings.backup, os.environ, clock=clock, context=context
        )
    run = outcome.run
    if outcome.status == "ok":
        assert run is not None
        typer.echo(
            f"Copia {run.run_id} completada. Snapshot: {run.snapshot_id}; "
            f"{len(run.inventory or {})} ficheros en el inventario."
        )
        return
    if run is not None:
        typer.echo(f"La copia {run.run_id} ha fallado: {run.error}", err=True)
        raise typer.Exit(1)
    raise fail(BACKUP_RUN_MESSAGES[outcome.reason])


BACKUP_RUN_MESSAGES: dict[BackupFailure | Literal["not_configured"] | None, str] = {
    "not_configured": (
        "Faltan los ajustes de la copia: añade backup.restic_repository al fichero de ajustes."
    ),
    "invalid_credentials": (
        "Credenciales de la copia no válidas: deben estar completas y ser distintas de las del "
        "workspace; revisa las variables ENS_BACKUP_*."
    ),
    "previous_run_unfinished": (
        "Hay una copia anterior sin finalizar; revísala con `backup status` antes de repetir."
    ),
    "workspace_unreadable": "No se pueden leer los registros de la copia del workspace.",
    "record_not_persisted": (
        "No se pudo escribir el registro de la copia; la ejecución queda como interrumpida."
    ),
    "restic_failed": "La copia ha fallado; revisa `backup status`.",
    None: "La copia no se ha podido completar.",
}


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
    if plan.simulated:
        typer.echo(f"  Aviso: {SIMULATION_REVIEW_NOTICE}", err=True)
    for note in plan.notes:
        typer.echo(f"  Aviso: {note}", err=True)


def warn_if_register_has_problems(store: WorkspaceStore) -> None:
    """Un aviso, no un bloqueo (ADR 0013): remite a `incident verify`."""
    if problems := verify_all_incidents(store):
        typer.echo(f"Aviso: {INCIDENT_REGISTER_NOTICE.format(n=len(problems))}.", err=True)


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
            open_workspace(ctx),
            clock(),
            measure,
            owner_name=owner,
            owner_role=owner_role,
            every_days=every,
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
            checks=check_source(),
        )
    except MatrixRejected as exc:
        raise matrix_rejected(f"No se registra la revisión de {measure}:", exc) from None
    if plan.row.obligations:
        typer.echo(f"Cobertura atribuida a {measure}:")
        print_coverage(plan)
        if plan.incident_history:
            warn_if_register_has_problems(store)
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
    for line in status.check_lines:
        typer.echo(f"    {line}")
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
    store = open_workspace(ctx)
    result, rows = matrix_status(store, instant, checks=check_source())
    shown = [s for s in rows if measure is None or s.row.spec.measure_id == measure]
    if measure is not None and not shown:
        raise fail(f"{measure} no es una medida de la Declaración de Aplicabilidad.", 1)
    typer.echo(f"Declaración de Aplicabilidad: {DECLARATION_LABELS[result.declaration_state]}.")
    for status in shown:
        print_row_status(status)
    print_errors(list(result.errors))
    if any(s.incident_history for s in shown):
        warn_if_register_has_problems(store)
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
    evaluation = assess(store, parse_instant("--at", at) or generated_at, checks=check_source())
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


DECLARED_NAME_NOTICE = "El nombre es declarado; no se autentica."
OP_EXP_9 = "op.exp.9"
OP_EXP_7 = "op.exp.7"


def incident_rejected(title: str, errors: list[str]) -> typer.Exit:
    typer.echo(title, err=True)
    print_errors(errors)
    return typer.Exit(1)


def incident_id_argument(text: str) -> str:
    if not re.fullmatch(INCIDENT_ID_PATTERN, text):
        raise fail("ID: usa el formato INC-AAAA-NNN (o SIM-AAAA-NNN en un simulacro).")
    return text


IncidentIdArgument = Annotated[str, typer.Argument(help="p. ej. INC-2026-001")]
By = Annotated[str, typer.Option("--by", help="Nombre de quien registra (declarado).")]


def record_or_reject(
    store: WorkspaceStore, state: IncidentState | ValidationError, kind: RecordKind, by: str
) -> Recorded:
    """Escribe un registro; un rechazo imprime los campos (nunca los valores) y sale con 1."""
    if isinstance(state, ValidationError):
        raise incident_rejected(
            "No se registra el incidente:", describe_validation("incidente", state)
        )
    try:
        return record_incident(store, state, kind=kind, by=by, now=clock())
    except IncidentRejected as exc:
        raise incident_rejected(f"No se registra {state.id}:", exc.errors) from None


def print_recorded(recorded: Recorded) -> None:
    state = recorded.record.state
    label = f" ({SIMULATION_LABEL})" if state.simulated else ""
    kind = RECORD_LABELS[recorded.record.kind]
    typer.echo(f"{state.id}{label}: registro {recorded.record.seq} ({kind}).")
    typer.echo(f"  Clave: {recorded.key}")
    typer.echo(f"  Evidencia: {recorded.evidence_id}")
    typer.echo(f"  sha256: {recorded.sha256}")
    if recorded.history_evidence_id:
        typer.echo(f"  Historial: {recorded.history_evidence_id}")
    typer.echo(DECLARED_NAME_NOTICE)
    for warning in recorded.warnings:
        typer.echo(f"Aviso: {warning}", err=True)


def missing_history(recorded: Recorded) -> typer.Exit:
    """El cierre está escrito y es definitivo: se dice qué falta y cómo completarlo."""
    incident_id = recorded.record.state.id
    typer.echo(
        f"El cierre de {incident_id} está registrado, pero su historial no se generó:", err=True
    )
    print_errors(recorded.history_problems)
    typer.echo(
        "Los registros no se tocan. Para completar el historial: "
        f'ens-evidence incident history {incident_id} --by "NOMBRE"',
        err=True,
    )
    return typer.Exit(1)


def print_link_steps(history_evidence_id: str, *, simulated: bool) -> None:
    mark = " (es un simulacro: la matriz lo mostrará)" if simulated else ""
    typer.echo(
        f"Para sustentar {OP_EXP_9}{mark} y {OP_EXP_7} (estos comandos no se han ejecutado):"
    )
    typer.echo(
        f"  ens-evidence matrix link {OP_EXP_9} --evidence {history_evidence_id} --covers base"
    )
    typer.echo(
        f"  ens-evidence matrix link {OP_EXP_7} --evidence {history_evidence_id} "
        f"--covers {OP_EXP_7}.1"
    )
    typer.echo("Solo la revisión de una persona lo confirma (matrix review).")


def last_incident_state(store: WorkspaceStore, incident_id: str) -> IncidentState:
    scan = scan_incident(store, incident_id)
    if not scan.records:
        raise incident_rejected(f"No se registra {incident_id}:", [f"{incident_id} no existe"])
    return scan.records[-1].record.state


def changed_state(state: IncidentState, changes: dict[str, Any]) -> IncidentState | ValidationError:
    try:
        return IncidentState.model_validate({**state.model_dump(mode="json"), **changes})
    except ValidationError as exc:
        return exc


@incident_app.command("new")
@handled
def incident_new(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title", help="Título breve, sin datos personales.")],
    detected_at: Annotated[str, typer.Option("--detected-at", help="Instante de detección.")],
    by: By,
    severity: Annotated[
        str | None, typer.Option("--severity", help="Nivel declarado en org-docs.")
    ] = None,
    asset: Annotated[
        list[str] | None, typer.Option("--asset", help="Id de activo del inventario.")
    ] = None,
    source_system: Annotated[
        str | None, typer.Option("--source-system", help="Sistema que originó la alerta.")
    ] = None,
    alert_id: Annotated[
        list[str] | None, typer.Option("--alert-id", help="Id opaco de la alerta.")
    ] = None,
    personal_data: Annotated[
        TriState, typer.Option("--personal-data", help="¿Afecta a datos personales?")
    ] = TriState.UNKNOWN,
    summary: Annotated[str | None, typer.Option("--summary", help="Análisis inicial.")] = None,
    simulated: Annotated[
        bool, typer.Option("--simulated", help="Simulacro: exige el procedimiento aprobado.")
    ] = False,
) -> None:
    """Da de alta un incidente (reporte inicial, op.exp.9.1)."""
    store = open_workspace(ctx)
    detected = parse_instant("--detected-at", detected_at)
    assert detected is not None
    if alert_id and not source_system:
        raise fail("--source-system: obligatorio con --alert-id.")
    try:
        procedure = simulation_procedure(store) if simulated else None
        incident_id = next_incident_id(store, simulated=simulated, year=detected.year)
    except IncidentRejected as exc:
        raise incident_rejected("No se registra el incidente:", exc.errors) from None
    try:
        state = IncidentState.model_validate(
            {
                "id": incident_id,
                "simulated": simulated,
                "title": title,
                "severity": severity,
                "detected_at": detected,
                "assets": asset or [],
                "source": {"system": source_system, "alert_ids": alert_id or []}
                if source_system
                else None,
                "personal_data": personal_data,
                "analysis": {"summary": summary},
                "procedure": procedure,
            }
        )
    except ValidationError as exc:
        state = exc
    print_recorded(record_or_reject(store, state, RecordKind.OPENED, by))


@incident_app.command("update")
@handled
def incident_update(
    ctx: typer.Context,
    incident: IncidentIdArgument,
    by: By,
    severity: Annotated[str | None, typer.Option("--severity")] = None,
    summary: Annotated[str | None, typer.Option("--summary", help="Sustituye el análisis.")] = None,
    asset: Annotated[list[str] | None, typer.Option("--asset", help="Añade un activo.")] = None,
    personal_data: Annotated[TriState | None, typer.Option("--personal-data")] = None,
    action: Annotated[str | None, typer.Option("--action", help="Actuación realizada.")] = None,
    action_kind: Annotated[ActionKind, typer.Option("--action-kind")] = ActionKind.OTHER,
    action_at: Annotated[
        str | None,
        typer.Option("--action-at", help="Instante de la actuación (por defecto, ahora)."),
    ] = None,
    evidence: Annotated[
        list[str] | None, typer.Option("--evidence", help="Añade una evidencia EV-… relacionada.")
    ] = None,
) -> None:
    """Añade un reporte intermedio (op.exp.9.1). Sin cambios, se rechaza."""
    store = open_workspace(ctx)
    incident_id = incident_id_argument(incident)
    before = last_incident_state(store, incident_id)
    at = parse_instant("--action-at", action_at)
    if action is None and (at or action_kind is not ActionKind.OTHER):
        raise fail("--action-kind y --action-at necesitan --action.")
    changes: dict[str, Any] = {}
    if severity is not None:
        changes["severity"] = severity
    if summary is not None:
        changes["analysis"] = {**before.analysis.model_dump(mode="json"), "summary": summary}
    if asset:
        changes["assets"] = list(dict.fromkeys([*before.assets, *asset]))
    if personal_data is not None:
        changes["personal_data"] = personal_data
    if action is not None:
        new_action = {"kind": action_kind, "at": at or clock(), "description": action}
        changes["actions"] = [*before.model_dump(mode="json")["actions"], new_action]
    if evidence:
        changes["evidence_ids"] = list(dict.fromkeys([*before.evidence_ids, *evidence]))
    after = changed_state(before, changes)
    if after == before:
        raise incident_rejected(f"No se registra {incident_id}:", ["la orden no cambia nada"])
    print_recorded(record_or_reject(store, after, RecordKind.UPDATED, by))


@incident_app.command("close")
@handled
def incident_close(
    ctx: typer.Context,
    incident: IncidentIdArgument,
    by: By,
    conclusion: Annotated[str, typer.Option("--conclusion", help="Conclusión del análisis.")],
    auditable_events_result: Annotated[
        ReviewResult,
        typer.Option(
            "--auditable-events-result",
            help=(
                "Resultado de revisar los eventos auditables tras el análisis (op.exp.9.3): "
                "changed si hubo que cambiarlos, no_change si no."
            ),
        ),
    ],
    auditable_events_detail: Annotated[
        str, typer.Option("--auditable-events-detail", help="Qué cambió o por qué no cambia.")
    ],
    legal_relevance: Annotated[
        TriState, typer.Option("--legal-relevance", help="op.exp.9.2: ¿relevancia jurisdiccional?")
    ],
    legal_advice: Annotated[
        str | None, typer.Option("--legal-advice", help="Quién da el asesoramiento legal.")
    ] = None,
    preserved_evidence: Annotated[
        list[str] | None, typer.Option("--preserved-evidence", help="EV-… preservada.")
    ] = None,
    closed_at: Annotated[
        str | None, typer.Option("--closed-at", help="Instante del cierre (por defecto, ahora).")
    ] = None,
) -> None:
    """Cierra el incidente (reporte final) y genera su historial. El cierre es definitivo."""
    store = open_workspace(ctx)
    incident_id = incident_id_argument(incident)
    before = last_incident_state(store, incident_id)
    closure = {
        "closed_at": parse_instant("--closed-at", closed_at) or clock(),
        "conclusion": conclusion,
        "legal_relevance": legal_relevance,
        "legal_advice": legal_advice,
        "preserved_evidence_ids": preserved_evidence or [],
        "auditable_events_review": {
            "result": auditable_events_result,
            "detail": auditable_events_detail,
        },
    }
    after = changed_state(before, {"status": IncidentStatus.CLOSED, "closure": closure})
    recorded = record_or_reject(store, after, RecordKind.CLOSED, by)
    print_recorded(recorded)
    if not recorded.history_evidence_id:
        raise missing_history(recorded)
    print_link_steps(recorded.history_evidence_id, simulated=recorded.record.state.simulated)


@incident_app.command("history")
@handled
def incident_history(ctx: typer.Context, incident: IncidentIdArgument, by: By) -> None:
    """Genera el historial de un incidente cerrado al que le falta (p. ej. tras un corte al
    cerrar). No modifica ningún registro."""
    store = open_workspace(ctx)
    incident_id = incident_id_argument(incident)
    scan = scan_incident(store, incident_id)
    if not scan.records:
        raise fail(f"{incident_id} no existe.", 1)
    try:
        manifest = record_history(store, incident_id, by=by, now=clock())
    except IncidentRejected as exc:
        raise incident_rejected(
            f"No se genera el historial de {incident_id}:", exc.errors
        ) from None
    typer.echo(f"{incident_id}: historial generado.")
    typer.echo(f"  Historial: {manifest.id}")
    typer.echo(DECLARED_NAME_NOTICE)
    print_link_steps(manifest.id, simulated=scan.records[-1].record.state.simulated)


@incident_app.command("list")
@handled
def incident_list(ctx: typer.Context) -> None:
    """Lista los incidentes con su último estado."""
    store = open_workspace(ctx)
    checks = check_all_incidents(store)
    if not checks:
        typer.echo("No hay incidentes registrados.")
    for incident_id, check in checks.items():
        records = check.scan.records
        state = records[-1].record.state if records else None
        typer.echo(list_line(incident_id, state, bool(check.problems)))


@incident_app.command("show")
@handled
def incident_show(
    ctx: typer.Context,
    incident: IncidentIdArgument,
    as_json: Annotated[bool, typer.Option("--json", help="Salida en JSON.")] = False,
) -> None:
    """Muestra un incidente y su historia de registros."""
    store = open_workspace(ctx)
    incident_id = incident_id_argument(incident)
    check = check_incident(store, incident_id)
    scan, problems = check.scan, list(check.problems)
    if not scan.records and not scan.problems:
        raise fail(f"{incident_id} no existe.", 1)
    if as_json:
        records = [
            {"key": s.key, "sha256": s.sha256, **s.record.model_dump(mode="json")}
            for s in scan.records
        ]
        document = {
            "incident_id": incident_id,
            "simulated": scan.records[-1].record.state.simulated if scan.records else None,
            "problems": problems,
            "records": records,
        }
        typer.echo(json.dumps(document, indent=2, ensure_ascii=False))
        return
    for line in show_lines(scan, problems):
        typer.echo(line)


@incident_app.command("verify")
@handled
def incident_verify(
    ctx: typer.Context,
    incident: Annotated[str | None, typer.Argument(help="Por defecto, todos.")] = None,
) -> None:
    """Verifica claves, secuencia, cadena, evidencias e historial. Sale con 1 si hay problemas."""
    store = open_workspace(ctx)
    if incident is None:
        found = verify_all_incidents(store)
    else:
        incident_id = incident_id_argument(incident)
        check = check_incident(store, incident_id)
        if not check.scan.records and not check.scan.problems:
            raise fail(f"{incident_id} no existe.", 1)
        found = {incident_id: list(check.problems)} if check.problems else {}
    if found:
        typer.echo("La verificación de incidentes ha fallado:", err=True)
        for incident_id, problems in found.items():
            typer.echo(f"{incident_id}:", err=True)
            print_errors(problems)
        raise typer.Exit(1)
    typer.echo("Incidentes verificados: íntegros." if incident is None else f"{incident}: íntegro.")


def connector_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.load_entry_points()
    return registry


class SystemWaitClock(SystemClock):
    """El reloj de la CLI: `clock()` (sustituible en tests) y una espera real."""

    def __init__(self) -> None:
        super().__init__(threading.Event())

    def now(self) -> datetime:
        return clock()


@contextmanager
def exclusive_cycle(settings: Settings) -> Iterator[CycleContext]:
    """El lock del ciclo para una entrada externa; ocupado, sale con 2."""
    lock = CycleLock.for_settings(settings)
    try:
        lock.acquire()
    except CycleBusy:
        raise fail("Hay un ciclo en curso para este workspace; espera a que termine.") from None
    except OSError:
        raise fail(
            "No se puede tomar el lock del ciclo (directorio de estado no privado o no escribible)."
        ) from None
    try:
        yield CycleContext(lock)
    finally:
        lock.release()


@app.command()
@handled
def collect(
    ctx: typer.Context,
    source: Annotated[str | None, typer.Argument(help="Conector a ejecutar.")] = None,
    all_sources: Annotated[bool, typer.Option("--all", help="Todos los conectores.")] = False,
) -> None:
    """Recoge evidencias. Un fallo del conector queda registrado como evidencia en ERROR.
    Salida: 0 todo recogido, 3 parcial (secciones o estado global sin permiso/no soportado),
    1 error, 2 configuración inválida o ciclo en curso."""
    if all_sources == (source is not None):
        raise fail("Indica un conector o --all.")
    if source is not None:
        try:
            plugins = Plugins(connector_registry())
        except ConnectorLoadError:
            raise fail(
                "No se pueden cargar los conectores instalados: hay un plugin defectuoso. "
                "Revisa los paquetes que declaran el grupo ens_evidence.connectors."
            ) from None
        if source not in plugins.names():
            raise fail(
                f"Conector desconocido: {source}. Disponibles: {', '.join(plugins.names())}."
            )
    else:
        plugins = load_plugins()
        for broken in plugins.broken:
            typer.echo(f"Plugin defectuoso, no se carga: {broken}", err=True)
    settings = resolved_settings(ctx)
    store = open_store(settings)
    with exclusive_cycle(settings):
        if source is not None:
            outcomes = [
                collect_one(
                    store, settings, plugins, source, clock(), clock=SystemWaitClock(),
                    cfg=CollectConfig(retries=0), isolated=False,
                )
            ]  # fmt: skip
        else:
            cfg = load_schedule_config(store).config.collect.model_copy(update={"retries": 0})
            outcomes = collect_all(store, settings, plugins, clock(), cfg, SystemWaitClock())
    if not outcomes:
        typer.echo("Ningún conector configurado: no se ha recogido nada.")
        raise typer.Exit(3)
    codes = [print_collect_outcome(outcome, labelled=source is None) for outcome in outcomes]
    raise typer.Exit(2 if 2 in codes else 1 if 1 in codes else 3 if 3 in codes else 0)


def print_collect_outcome(outcome: CollectOutcome, *, labelled: bool) -> int:
    prefix = f"{outcome.connector}: " if labelled else ""
    result = outcome.result
    if result is None:
        typer.echo(f"{prefix}no se ha recogido: {outcome.problem}", err=True)
        return 2
    typer.echo(f"{prefix}Evidencia registrada: {outcome.evidence_id}")
    if outcome.problem is not None:
        typer.echo(f"{prefix}configuración rechazada: {outcome.problem}", err=True)
        return 2
    sections = result.data.get("sections", {})
    for name, section in sections.items():
        typer.echo(f"  {name}: {section['status']}")
    return collect_exit_code(result.collection_status, result.data, sections)


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


CHECK_FOOTER = (
    "Un check en PASS no da por implantada ninguna medida; "
    "lo valora una persona con `matrix review`."
)


CHECK_NEXT_STEP = (
    "Siguiente paso: corrige lo que falla, vuelve a recoger y ejecuta `ens-evidence check --all`; "
    "el detalle por medida está en `ens-evidence assessment status`."
)


def check_next_steps(outcomes: list[ConnectorOutcome]) -> list[str]:
    """Qué hacer tras `check --all`, según lo que se evaluó de verdad: nada si no se evaluó
    ninguna ejecución; recoger si solo falta evidencia; corregir si algo falla."""
    evaluated = [
        o for o in outcomes if o.run and o.kind in (OutcomeKind.RECORDED, OutcomeKind.UNCHANGED)
    ]
    results = [
        (o.connector, r)
        for o in evaluated
        for r in o.run.results
        if r.status is not CheckStatus.PASS
    ]
    if any(r.reason_code != NO_EVIDENCE for _, r in results):
        return [CHECK_NEXT_STEP]
    missing = sorted({connector for connector, _ in results})
    return [
        f"Siguiente paso: recoge con `ens-evidence {collect_step(c)}` y ejecuta "
        "`ens-evidence check --all`."
        for c in missing
    ]


def check_exit_code(outcomes: list[ConnectorOutcome]) -> int:
    """2 si algún conector no se pudo evaluar (config, historial, rechazo); 4 con algún FAIL;
    3 con UNKNOWN, ERROR, intento incompleto o nada configurado; 0 si todo es PASS."""
    runs = [o.run for o in outcomes if o.run is not None]
    broken = any(
        o.kind in (OutcomeKind.HISTORY_UNREADABLE, OutcomeKind.REJECTED)
        or (o.run is not None and any(e.startswith(CONFIG_INVALID) for e in o.run.errors))
        for o in outcomes
    )
    statuses = {r.status for run in runs for r in run.results}
    if broken:
        return 2
    if CheckStatus.FAIL in statuses:
        return 4
    if not runs or statuses - {CheckStatus.PASS} or not all(run.complete for run in runs):
        return 3
    return 0


def print_check_outcome(outcome: ConnectorOutcome, last_key: str | None) -> None:
    name = outcome.connector
    if outcome.kind is OutcomeKind.NOT_CONFIGURED:
        typer.echo(f"{name}: no configurado")
        return
    if outcome.kind in (OutcomeKind.HISTORY_UNREADABLE, OutcomeKind.REJECTED):
        typer.echo(f"{name}: no se registra la ejecución:", err=True)
        print_errors(list(outcome.detail))
        return
    run = outcome.run
    assert run is not None
    written = (
        f"ejecución escrita {outcome.key}"
        if outcome.kind is OutcomeKind.RECORDED
        else f"sin cambios desde {last_key}"
    )
    counts = count_statuses(run.results)
    typer.echo(f"{name}: {written}")
    typer.echo("  " + "  ".join(f"{s.value} {n}" for s, n in counts.items()))
    for result in run.results:
        if result.status is not CheckStatus.PASS:
            subject = result.subject or result.asset_id
            code = f" ({result.reason_code})" if result.reason_code else ""
            typer.echo(f"  {result.status.value} {result.check_id} {subject}{code}")
    for error in run.errors:
        typer.echo(f"  ERROR {error}")


@app.command()
@handled
def check(
    ctx: typer.Context,
    all_checks: Annotated[
        bool, typer.Option("--all", help="Evalúa los checks de todos los conectores configurados.")
    ] = False,
    clock_corrected: Annotated[
        bool,
        typer.Option(
            "--clock-corrected",
            help="Confirma que la hora del equipo ya es la correcta y que las ejecuciones con "
            "fecha futura se guardaron con el reloj adelantado: se registra una ejecución nueva "
            "que las sustituye. No borra nada.",
        ),
    ] = False,
) -> None:
    """Evalúa los checks sobre lo ya recogido, sin contactar con los servicios de los conectores,
    y registra la ejecución. Sale con 0 si
    todo es PASS, 4 con algún FAIL, 3 con UNKNOWN, ERROR, ejecución incompleta o ningún conector
    configurado, y 2 si no se pudo evaluar (configuración inválida, historial ilegible, ejecución
    rechazada o alcance no aprobado)."""
    if not all_checks:
        raise fail("Indica --all para evaluar los checks de todos los conectores configurados.")
    settings = resolved_settings(ctx)
    store = open_store(settings)
    with exclusive_cycle(settings):
        try:
            outcomes = run_all(store, clock(), accept_clock_correction=clock_corrected)
        except ScopeNotApproved as exc:
            report_missing_context(exc)
            raise typer.Exit(2) from None
        scan = scan_runs(store)
        for outcome in outcomes:
            last = scan.last(outcome.connector)
            print_check_outcome(outcome, last.key if last else None)
            if outcome.kind is OutcomeKind.RECORDED and last and last.run.clock_correction:
                replaced = ", ".join(last.run.clock_correction.superseded)
                typer.echo(
                    "  corrección del reloj: las ejecuciones con fecha futura ya no cuentan y "
                    f"siguen guardadas ({replaced})"
                )
        if not any(o.run for o in outcomes):
            typer.echo("Ningún conector configurado: no se ha evaluado nada.")
        code = check_exit_code(outcomes)
        for step in check_next_steps(outcomes):
            typer.echo(step)
        if any(o.kind in (OutcomeKind.RECORDED, OutcomeKind.UNCHANGED) for o in outcomes):
            typer.echo(CHECK_FOOTER)
        raise typer.Exit(code)


class ReportFormat(StrEnum):
    JSON = "json"
    HTML = "html"


def report_exit_code(store: WorkspaceStore, report: DailyReport | None) -> int:
    """3 si algún conector configurado está en error o sin ejecución en el informe diario."""
    if report is None:
        return 0
    for entry in report.connectors:
        if not store.exists(connector_config_key(entry.connector)):
            continue
        collection = entry.collection
        if isinstance(collection, Unavailable) or collection.value is not CollectionStatus.SUCCESS:
            return 3
    return 0


def local_period(kind: JobKind, instant: datetime, zone: tzinfo) -> str:
    day = instant.astimezone(zone).date()
    return day.isoformat() if kind == "daily" else f"{day.year:04d}-{day.month:02d}"


def write_report(
    store: WorkspaceStore, kind: JobKind, cfg: ScheduleConfig, instant: datetime, stdout: bool,
    fmt: ReportFormat,
) -> tuple[str, DailyReport | None]:  # fmt: skip
    """Genera el informe del estado actual a `instant`: nada de red ni de plugins."""
    checks = StoredCheckResults(reference_now=instant)
    if kind == "daily":
        period = local_period("daily", instant, cfg.zone)
        daily_report = build_daily(
            store,
            generated_at=instant,
            period=period,
            tz=cfg.zone,
            cycle=None,
            checks=checks,
            windows=cfg.expiry_warnings.windows(),
        )
        model, html = daily_report, render_daily_html(daily_report)
    else:
        period = previous_month(local_period("monthly", instant, cfg.zone))
        monthly_report = build_monthly(
            store,
            generated_at=instant,
            period=period,
            tz=cfg.zone,
            checks=checks,
            expiry_windows=cfg.expiry_warnings.windows(),
        )
        model, html = monthly_report, render_monthly_html(monthly_report, cfg.zone)
    daily_model = model if isinstance(model, DailyReport) else None
    if stdout:
        text = html if fmt is ReportFormat.HTML else render_report_json(model).decode("utf-8")
        return text, daily_model
    pair = save_pair(
        store, kind, period, render_report_json(model), html.encode("utf-8"), at=clock()
    )
    return pair.json_key, daily_model


def report_command(
    ctx: typer.Context, kind: JobKind, cycle: bool, at: str | None, stdout: bool,
    fmt: ReportFormat | None,
) -> None:  # fmt: skip
    if cycle and at is not None:
        raise fail("--at solo se admite sin --cycle: el ciclo usa siempre el reloj real.")
    if at is not None and not stdout:
        raise fail(
            "--at exige --stdout: un informe de un instante pasado no se guarda, porque "
            "rellenaría días sin ciclo."
        )
    if fmt is not None and not stdout:
        raise fail("--format solo se admite con --stdout.")
    chosen = fmt or ReportFormat.JSON
    instant = parse_instant("--at", at) or clock()
    settings = resolved_settings(ctx)
    store = open_store(settings)
    cfg = load_schedule_config(store).config
    if not cycle:
        output, daily_model = write_report(store, kind, cfg, instant, stdout, chosen)
        typer.echo(output, nl=not stdout)
        raise typer.Exit(report_exit_code(store, daily_model))
    plugins = load_plugins()
    for broken in plugins.broken:
        typer.echo(f"Plugin defectuoso, no se carga: {broken}", err=True)
    with exclusive_cycle(settings) as context:
        outcome = run_cycle(
            context, store, settings, os.environ, kind, local_period(kind, instant, cfg.zone),
            instant, clock=SystemWaitClock(), cfg=cfg, trigger="manual", plugins=plugins,
        )  # fmt: skip
        if not outcome.completed:
            raise fail(f"El ciclo no ha publicado el informe ({outcome.failure}); reintenta.")
        daily_bytes = store.read_bytes(outcome.reports[0]) if kind == "daily" else None
    daily_model = DailyReport.model_validate(load_json_strict(daily_bytes)) if daily_bytes else None
    last = outcome.reports[-1]
    if stdout:
        key = last if chosen is ReportFormat.JSON else last.removesuffix(".json") + ".html"
        typer.echo(store.read_text(key), nl=False)
    else:
        typer.echo("\n".join(outcome.reports))
    if outcome.backup_stage == "backup_failed":
        typer.echo("La copia ha fallado: se verá en el siguiente informe.", err=True)
    raise typer.Exit(report_exit_code(store, daily_model))


ReportAt = Annotated[
    str | None, typer.Option("--at", help="Instante del informe (sin --cycle; exige --stdout).")
]
ReportCycle = Annotated[
    bool, typer.Option("--cycle", help="Recoge, evalúa, informa y copia (toma el lock).")
]
ReportStdout = Annotated[bool, typer.Option("--stdout", help="Imprime sin escribir.")]
ReportFormatOption = Annotated[
    ReportFormat | None, typer.Option("--format", help="Formato con --stdout (json por defecto).")
]


@report_app.command("daily")
@handled
def report_daily(
    ctx: typer.Context,
    cycle: ReportCycle = False,
    at: ReportAt = None,
    stdout: ReportStdout = False,
    fmt: ReportFormatOption = None,
) -> None:
    """Informe diario. Sin --cycle, del estado actual (sin red ni plugins). Salida: 0 escrito,
    3 escrito con conectores en error o sin ejecución, 2 no escrito o ciclo en curso."""
    report_command(ctx, "daily", cycle, at, stdout, fmt)


@report_app.command("monthly")
@handled
def report_monthly(
    ctx: typer.Context,
    cycle: ReportCycle = False,
    at: ReportAt = None,
    stdout: ReportStdout = False,
    fmt: ReportFormatOption = None,
) -> None:
    """Informe mensual del mes natural anterior. Con --cycle solo genera el mensual (sin recoger
    ni evaluar; el diario es otro trabajo). Salida: 0 escrito, 2 no escrito o ciclo en curso."""
    report_command(ctx, "monthly", cycle, at, stdout, fmt)


@scheduler_app.command("init-config")
@handled
def scheduler_init_config(ctx: typer.Context) -> None:
    """Escribe reports/schedule.yaml con los valores por defecto si no existe."""
    store = open_workspace(ctx)
    try:
        store.create_bytes(SCHEDULE_KEY, default_schedule_yaml().encode("utf-8"))
    except AlreadyExists:
        typer.echo(f"{SCHEDULE_KEY} ya existe; no se toca.")
        return
    typer.echo(f"Escrito {SCHEDULE_KEY} con los valores por defecto.")


@scheduler_app.command("run")
@handled
def scheduler_run(ctx: typer.Context) -> None:
    """Proceso de larga duración: ciclo diario y mensual según reports/schedule.yaml. Recarga la
    configuración en cada vuelta. Sale con 2 si la configuración es inválida al arrancar y con 0
    tras SIGTERM o SIGINT."""
    settings = resolved_settings(ctx)
    store = open_store(settings)
    load_schedule_config(store)
    stop = threading.Event()
    system_clock = SystemClock(stop)
    heartbeat = file_heartbeat(os.environ)
    scheduler = Scheduler(
        system_clock,
        CycleExecutor(store, settings, os.environ, system_clock, stop, heartbeat=heartbeat),
        lambda: load_schedule_config(store).config,
        workspace_done_periods(store, system_clock),
        stop,
        heartbeat=heartbeat,
    )
    with stop_on_signals(stop):
        scheduler.run()


@scheduler_app.command("status")
@handled
def scheduler_show_status(ctx: typer.Context) -> None:
    """Configuración efectiva, próximos disparos, últimos informes, ciclos interrumpidos y días
    sin informe del mes. Solo lectura: no toma el lock ni escribe."""
    store = open_workspace(ctx)
    status = scheduler_status(store, load_schedule_config(store), clock())
    typer.echo(render_status(status), nl=False)


class StatusFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


@assessment_app.command("status")
@handled
def assessment_show_status(
    ctx: typer.Context,
    at: Annotated[str | None, typer.Option("--at", help="Instante a evaluar.")] = None,
    fmt: Annotated[
        StatusFormat, typer.Option("--format", help="Formato de la salida.")
    ] = StatusFormat.TEXT,
) -> None:
    """Preparación del sistema para la autoevaluación. Lectura pura: no escribe nada. Sale con 0
    si está preparada y sin errores, 1 si no. La herramienta no declara conformidad."""
    instant = parse_instant("--at", at) or clock()
    status = assessment_status(open_workspace(ctx), instant, check_source())
    typer.echo(
        render_status_json(status) if fmt is StatusFormat.JSON else render_status_text(status),
        nl=False,
    )
    if not status.is_ready:
        raise typer.Exit(1)


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


# --- assessment dossier ------------------------------------------------------------------------

DECLARED_NAME_NOTICE = "El nombre es declarado; no se autentica."
DOSSIER_FOOTER = "La herramienta no declara conformidad: solo registra lo que las personas hacen."
DossierIdArgument = Annotated[str, typer.Argument(help="Id del expediente (ver `dossier list`).")]
AtOption = Annotated[
    str | None,
    typer.Option("--at", help="Cuándo hizo la persona el acto (por defecto, ahora)."),
]


class SelfAssessRole(StrEnum):
    SYSTEM_ADMINISTRATOR = "system_administrator"
    SYSTEM_ADMINISTRATOR_DELEGATE = "system_administrator_delegate"


class ViewFormat(StrEnum):
    MD = "md"
    JSON = "json"


def dossier_rejected(title: str, errors: list[str]) -> typer.Exit:
    typer.echo(title, err=True)
    print_errors(errors)
    return typer.Exit(1)


def load_dossier(store: WorkspaceStore, dossier: str) -> DossierSnapshot:
    try:
        return load_snapshot(store, dossier)
    except DossierUnreadable as exc:
        raise dossier_rejected(f"El expediente {dossier} no se puede leer:", exc.problems) from None


def dossier_step(dossier: str, snapshot: DossierSnapshot, state: DossierState) -> str:
    """El siguiente paso tras llegar a `state`, nombrando quién lo da."""
    step = next_step(
        snapshot.category,
        state,
        dossier,
        incomplete_sections(snapshot),
        readiness_blocked=snapshot_readiness_blocked(snapshot),
    )
    return f"Siguiente paso: {step}."


def print_caveats(snapshot: DossierSnapshot, state: DossierState) -> None:
    """Lo que la organización debe saber antes del siguiente paso, sin esperar a declarar."""
    if state is not DossierState.DECLARED and snapshot_readiness_blocked(snapshot):
        typer.echo(NOT_READY_NOTICE)
    incomplete = incomplete_sections(snapshot)
    if incomplete:
        typer.echo(f"Secciones incompletas: {'; '.join(incomplete)}.")


def print_missing_markdown(store: WorkspaceStore, dossier: str) -> None:
    if not has_markdown(store, dossier):
        typer.echo(f"dossier.md no está; recréalo con `assessment dossier render {dossier}`.")


def print_notice(snapshot: DossierSnapshot) -> None:
    if snapshot.notice:
        typer.echo(snapshot.notice)


@dossier_app.command("generate")
@handled
def dossier_generate(ctx: typer.Context) -> None:
    """Genera la instantánea del expediente con el estado actual del workspace.

    Exige categorización vigente y Declaración de Aplicabilidad legible.

    Se puede generar aunque el sistema aún no esté preparado: `generate` y `status` lo avisan.

    En MEDIA/ALTA genera también el paquete por medida; en BÁSICA, `dossier package` lo genera bajo demanda."""  # noqa: E501
    store = open_workspace(ctx)
    now = clock()
    try:
        snapshot = build_snapshot(
            store, current_inputs(now), checks=StoredCheckResults(reference_now=now)
        )
    except CategorizationNotCurrent as exc:
        raise report_missing_context(exc) from None
    except DossierRejected as exc:
        raise dossier_rejected("No se genera el expediente:", exc.errors) from None
    try:
        dossier = save_snapshot(store, snapshot)
    except AlreadyExists:
        raise fail(
            "Ya existe un expediente con ese contenido y ese instante; reintenta.", 1
        ) from None
    save_markdown(store, dossier, render_snapshot_md(snapshot))
    typer.echo(f"Expediente {dossier} generado ({category_name(snapshot.category)}).")
    typer.echo(snapshot.commitment)
    print_notice(snapshot)
    if snapshot.category is not Category.BASICA:
        try:
            save_package(store, dossier, snapshot)
        except PackageRejected as exc:
            typer.echo(
                f"El paquete por medida no se ha guardado: ejecuta `dossier package {dossier}`.",
                err=True,
            )
            raise dossier_rejected("Paquete rechazado:", exc.errors) from None
        typer.echo("Paquete por medida guardado.")
    print_caveats(snapshot, DossierState.GENERATED)
    typer.echo(dossier_step(dossier, snapshot, DossierState.GENERATED))


@dossier_app.command("list")
@handled
def dossier_list(ctx: typer.Context) -> None:
    """Lista los expedientes con su categoría y su estado."""
    store = open_workspace(ctx)
    found = list_dossiers(store)
    failed = False
    if not found:
        typer.echo("No hay expedientes. Genera uno con `assessment dossier generate`.")
    for item in found:
        if item.snapshot is None:
            typer.echo(f"{item.id}  ILEGIBLE ({'; '.join(item.problems)})")
            continue
        scan = scan_transitions(store, item.id)
        state = scan.dossier.state.value if scan.dossier else "ROTO"
        altered = markdown_problems(store, item.id, item.snapshot)
        typer.echo(
            f"{item.id}  {category_name(item.snapshot.category)}  {state}"
            + ("  ALTERADO (ejecuta `dossier verify`)" if altered else "")
        )
        failed = failed or bool(altered) or scan.dossier is None
    if failed:
        raise typer.Exit(1)


@dossier_app.command("status")
@handled
def dossier_show_status(ctx: typer.Context, dossier: DossierIdArgument) -> None:
    """Estado, integridad, deriva por sección, siguiente paso y huellas de un expediente.

    Sale con 1 si hay deriva (el expediente ya no describe el estado actual), problemas de integridad (también del paquete) o, listo para declarar, un sistema que ya no está preparado."""  # noqa: E501
    store = open_workspace(ctx)
    status = dossier_status(store, dossier, now=clock())
    if not status.known:
        raise fail(UNKNOWN, 1)
    category = category_name(status.category) if status.category else "desconocida"
    typer.echo(f"Expediente {dossier} ({category})")
    if status.state:
        typer.echo(f"Estado: {status.state.value}")
    elif status.category is None:
        typer.echo("Estado: no fiable (instantánea ilegible o alterada)")
    else:
        typer.echo("Estado: no fiable (cadena rota)")
    if status.readiness_notice:
        typer.echo(status.readiness_notice)
    if status.incomplete_sections:
        typer.echo(f"Secciones incompletas: {'; '.join(status.incomplete_sections)}.")
    if status.notice:
        typer.echo(status.notice)
    if status.integrity:
        typer.echo("Integridad: PROBLEMAS", err=True)
        print_errors(list(status.integrity))
    elif has_markdown(store, dossier):
        typer.echo("Integridad: íntegro.")
    else:
        typer.echo("Integridad: incompleto; falta dossier.md, sin alteraciones en lo demás.")
    if status.drift is None:
        typer.echo("Deriva: no se puede comprobar (instantánea ilegible).", err=True)
    elif status.drift.current:
        typer.echo("Deriva: ninguna; el expediente describe el estado actual.")
    else:
        typer.echo("Deriva: el expediente ya no describe el estado actual.", err=True)
        for section, lines in status.drift.drift.items():
            typer.echo(f"  {section}:", err=True)
            for line in lines:
                typer.echo(f"    - {line}", err=True)
    if status.declaration_blocked:
        typer.echo(f"Siguiente paso: {status.next_step}", err=True)
    else:
        typer.echo(f"Siguiente paso: {status.next_step}")
    print_dossier_preaudit(findings_view(store, clock()), dossier)
    prints = status.fingerprints
    for label, value in (
        ("instantánea", prints.snapshot),
        ("análisis", prints.analysis),
        ("análisis citado por la declaración", prints.cited_analysis),
        ("última transición", prints.last_transition),
    ):
        typer.echo(f"Huella {label}: {value or '—'}")
    print_missing_markdown(store, dossier)
    typer.echo(DOSSIER_FOOTER)
    if (
        status.integrity
        or status.drift is None
        or not status.drift.current
        or status.declaration_blocked
    ):
        raise typer.Exit(1)


@dossier_app.command("show")
@handled
def dossier_show(
    ctx: typer.Context,
    dossier: DossierIdArgument,
    fmt: Annotated[
        ViewFormat, typer.Option("--format", help="Formato de la salida.")
    ] = ViewFormat.MD,
    save: Annotated[
        bool,
        typer.Option("--save", help="Guarda también la vista junto al expediente (solo adición)."),
    ] = False,
) -> None:
    """Vista del expediente con las secciones 10 (conclusiones) y 11 (aprobación) al día."""
    store = open_workspace(ctx)
    snapshot = load_dossier(store, dossier)
    try:
        chain = entries(store, dossier)
    except TransitionRejected as exc:
        raise dossier_rejected(f"La cadena de {dossier} no se verifica:", exc.errors) from None
    typer.echo(render_view(snapshot, chain, fmt.value), nl=False)
    if save:
        key, created = save_view(store, dossier, snapshot, chain, fmt.value)
        message = "Vista guardada en" if created else "Ya estaba guardada:"
        typer.echo(f"{message} {key}", err=True)


def record_dossier_transition(
    ctx: typer.Context,
    dossier: str,
    to_state: DossierState,
    *,
    by: str,
    role: Role,
    at: str | None,
    **details: Any,
) -> None:
    store = open_workspace(ctx)
    now = clock()
    instant = parse_instant("--at", at) or now
    try:
        recorded = record_transition(
            store, dossier, to_state, by=by, role=role, at=instant, now=now, **details
        )
    except TransitionRejected as exc:
        raise dossier_rejected(f"No se registra {to_state.value}:", exc.errors) from None
    record = recorded.entry.record
    typer.echo(
        f"{to_state.value} registrado en {dossier} por {record.actor} ({role_label(record.role)})."
    )
    typer.echo(f"Huella del registro: {recorded.entry.sha256}")
    typer.echo(DECLARED_NAME_NOTICE)
    if recorded.notice:
        typer.echo(recorded.notice)
    snapshot = load_dossier(store, dossier)
    print_caveats(snapshot, to_state)
    typer.echo(dossier_step(dossier, snapshot, to_state))


@dossier_app.command("self-assess")
@handled
def dossier_self_assess(
    ctx: typer.Context,
    dossier: DossierIdArgument,
    by: By,
    role: Annotated[
        SelfAssessRole, typer.Option("--role", help="Quién la registra (anexo III 2.1.a).")
    ],
    at: AtOption = None,
) -> None:
    """Registra la autoevaluación por quien administra el sistema o su delegado.

    Exige una revisión humana en cada medida de la matriz."""
    record_dossier_transition(
        ctx, dossier, DossierState.SELF_ASSESSED, by=by, role=Role(role.value), at=at
    )


@dossier_app.command("analyze")
@handled
def dossier_analyze(
    ctx: typer.Context,
    dossier: DossierIdArgument,
    by: By,
    conclusion: Annotated[str, typer.Option("--conclusion", help="Conclusiones del análisis.")],
    elevated_to: Annotated[
        str,
        typer.Option(
            "--elevated-to", help="Persona del responsable del sistema a quien se elevan."
        ),
    ],
    elevated: Annotated[
        bool,
        typer.Option(
            "--elevated",
            help=(
                "Confirmas que has elevado las conclusiones al responsable del sistema; la "
                "herramienta no envía comunicaciones (anexo III 2.1.b)."
            ),
        ),
    ] = False,
    manual_judgment_review: Annotated[
        bool,
        typer.Option(
            "--manual-judgment-review",
            help=(
                "Confirmas que una persona ha revisado el juicio manual pendiente "
                "(medidas organizativas); sin esto no se podrá declarar."
            ),
        ),
    ] = False,
    corrective_measure: Annotated[
        list[str] | None,
        typer.Option(
            "--corrective-measure", help="Medida correctora para el responsable del sistema."
        ),
    ] = None,
    at: AtOption = None,
) -> None:
    """Registra el análisis del responsable de la seguridad y la elevación de sus conclusiones."""
    measures = [
        CorrectiveMeasure(description=text, addressee=Role.SYSTEM_OWNER)
        for text in corrective_measure or []
    ]
    record_dossier_transition(
        ctx,
        dossier,
        DossierState.ANALYZED,
        by=by,
        role=Role.SECURITY_OFFICER,
        at=at,
        conclusions=conclusion,
        corrective_measures=measures,
        manual_judgment_review=manual_judgment_review,
        elevated_to=elevated_to,
        elevation_confirmed=elevated,
    )


@dossier_app.command("declare")
@handled
def dossier_declare(
    ctx: typer.Context,
    dossier: DossierIdArgument,
    by: By,
    evidence: Annotated[
        str | None,
        typer.Option(
            "--evidence",
            help=(
                "Evidencia manual (EV-…) con el documento firmado; solo se verifica su "
                "integridad, la firma y el formato los revisa una persona."
            ),
        ),
    ] = None,
    acknowledge_incomplete: Annotated[
        bool,
        typer.Option(
            "--acknowledge-incomplete",
            help=(
                "Reconoces que declaras sobre un expediente con secciones INCOMPLETE; "
                "quedan listadas en el registro."
            ),
        ),
    ] = False,
    at: AtOption = None,
) -> None:
    """Registra la declaración de conformidad que hace la dirección (solo categoría BÁSICA).

    La herramienta solo registra el acto de la organización y no declara nada por sí sola. No \
    redacta la declaración ni genera el distintivo. En MEDIA/ALTA se rechaza (art. 38.1)."""
    record_dossier_transition(
        ctx,
        dossier,
        DossierState.DECLARED,
        by=by,
        role=Role.MANAGEMENT,
        at=at,
        declaration_evidence_id=evidence,
        acknowledge_incomplete=acknowledge_incomplete,
    )
    typer.echo(
        "Es un acto de la organización: la herramienta solo lo registra. "
        "El documento y el distintivo no los genera ella."
    )


@dossier_app.command("verify")
@handled
def dossier_verify(ctx: typer.Context, dossier: DossierIdArgument) -> None:
    """Reproduce la instantánea con sus entradas congeladas y verifica el paquete.

    Sale con 1 si los bytes no coinciden o el paquete no se verifica."""
    store = open_workspace(ctx)
    snapshot = load_dossier(store, dossier)
    problems = verification_problems(store, dossier, snapshot)
    checked_package = package_required(store, dossier, snapshot)
    if problems:
        raise dossier_rejected(f"La verificación de {dossier} ha fallado:", problems)
    if not has_markdown(store, dossier):
        typer.echo(
            f"{dossier}: reproducible con sus entradas congeladas y sin alteraciones, pero "
            "incompleto: falta dossier.md."
        )
        print_missing_markdown(store, dossier)
        return
    typer.echo(
        f"{dossier}: reproducible con sus entradas congeladas, cadena y derivados íntegros"
        + (" y paquete íntegro." if checked_package else "; sin paquete.")
    )


@dossier_app.command("package")
@handled
def dossier_package(ctx: typer.Context, dossier: DossierIdArgument) -> None:
    """Genera el paquete por medida (en BÁSICA, bajo demanda). Nunca sobrescribe."""
    store = open_workspace(ctx)
    snapshot = load_dossier(store, dossier)
    try:
        save_package(store, dossier, snapshot)
    except AlreadyExists:
        raise fail(
            f"{dossier} ya tiene paquete; no se sobrescribe (usa `dossier verify`).", 1
        ) from None
    except PackageRejected as exc:
        raise dossier_rejected("No se guarda el paquete:", exc.errors) from None
    typer.echo(f"Paquete de {dossier} guardado. Verifícalo con `assessment dossier verify`.")


@dossier_app.command("render")
@handled
def dossier_render(ctx: typer.Context, dossier: DossierIdArgument) -> None:
    """Recupera dossier.md y, en MEDIA/ALTA, el paquete si faltan. Nunca sobrescribe."""
    store = open_workspace(ctx)
    snapshot = load_dossier(store, dossier)
    base = f"{ASSESSMENTS_PREFIX}/{dossier}"
    recovered: list[str] = []
    if not has_markdown(store, dossier):
        save_markdown(store, dossier, render_snapshot_md(snapshot))
        recovered.append("dossier.md")
    index_key = f"{base}/{PACKAGE_DIR}/{INDEX_NAME}"
    if snapshot.category is not Category.BASICA and not store.exists(index_key):
        if has_package(store, dossier):
            raise dossier_rejected(
                "El paquete está incompleto y no se sobrescribe:",
                [f"restaura {base}/package desde la copia y ejecuta `dossier verify {dossier}`"],
            )
        try:
            save_package(store, dossier, snapshot)
        except PackageRejected as exc:
            raise dossier_rejected("No se recupera el paquete:", exc.errors) from None
        recovered.append("package")
    typer.echo(
        f"Recuperado: {', '.join(recovered)}." if recovered else "No falta nada; no se escribe."
    )


# --- preaudit ----------------------------------------------------------------------------------

PREAUDIT_FOOTER = (
    f"Cada hallazgo es una {PROPOSAL_NOTICE}. La herramienta no dictamina ni declara conformidad."
)
JUDGMENT_PENDING = "capa de juicio no ejecutada; revisión humana pendiente"
REDACTION_LIMIT = (
    "Revisa el payload antes de consentir: los nombres de personas que no constan en ningún "
    "registro del workspace no se redactan."
)
PLAN_RECIPIENT = {Provider.CLAUDE_CODE: "Anthropic", Provider.CODEX: "OpenAI"}
"""Quién recibe los datos cuando se envían con la sesión del plan contratado (CLI)."""
REF_LABELS = {
    RefKind.DOCUMENT: "Documento",
    RefKind.EVIDENCE: "Evidencia",
    RefKind.SECTION: "Sección",
}
DISCARD_LABELS = {
    DiscardReason.UNKNOWN_CRITERION: "criterio desconocido",
    DiscardReason.PROPOSAL_NOT_ALLOWED: "clase no admitida por el criterio",
    DiscardReason.UNKNOWN_MEASURE: "medida desconocida o fuera del expediente",
    DiscardReason.MEASURE_NOT_APPLICABLE: "medida no aplicable",
    DiscardReason.MEASURE_NOT_JUSTIFIED: "medida sin justificación que revisar",
    DiscardReason.MISSING_CITATION: "no cita lo que pide el criterio",
    DiscardReason.UNLINKED_REF: "cita algo que no está enlazado a la medida",
    DiscardReason.QUOTE_NOT_FOUND: "cita no literal o demasiado corta",
    DiscardReason.EMPTY_REASON: "sin motivo",
    DiscardReason.SENSITIVE_TEXT: "texto con un secreto o un dato personal",
    DiscardReason.UNDISPLAYABLE_TEXT: "texto que no se puede mostrar tal cual",
    DiscardReason.DUPLICATE: "duplicado; se queda la clase más grave",
}
SHA256_ARG = re.compile(r"[0-9a-f]{64}")
ProviderOption = Annotated[
    Provider | None,
    typer.Option("--provider", help="Confirma el proveedor de los ajustes (`preaudit`)."),
]


def preaudit_rejected(title: str, exc: preaudit.PreAuditRejected) -> typer.Exit:
    return dossier_rejected(title, exc.errors)


@preaudit_app.command("prepare")
@handled
def preaudit_prepare(
    ctx: typer.Context, dossier: DossierIdArgument, provider: ProviderOption = None
) -> None:
    """Prepara lo que se enviaría al proveedor y lo muestra. No envía nada.

    Guarda el payload (minimizado y redactado) y su huella en `evidence/preaudits/`; para
    enviarlo hay que consentir esa huella con `preaudit run --consent`."""
    settings = resolved_settings(ctx)
    store = open_store(settings)
    try:
        prepared = preaudit.prepare(store, dossier, provider, settings.preaudit, now=clock())
    except preaudit.PreAuditUnconfigured as exc:
        raise fail(f"Error: {exc}") from None
    except preaudit.PreAuditRejected as exc:
        raise preaudit_rejected("No se prepara el envío:", exc) from None
    typer.echo(json.dumps(prepared.payload.data, indent=2, ensure_ascii=False, sort_keys=True))
    typer.echo(f"Expediente: {dossier}")
    typer.echo(f"Destino: {prepared.destination} (modelo {prepared.model})")
    if recipient := PLAN_RECIPIENT.get(prepared.provider):
        typer.echo(f"Los datos los recibe {recipient} con tu plan.")
    typer.echo(f"Audit pack: {prepared.pack_version}")
    typer.echo(f"Huella del payload (sha256): {prepared.payload.sha256}")
    typer.echo(f"Preparado en {prepared.key}; el payload no ha salido de este equipo.")
    typer.echo(REDACTION_LIMIT)
    shell = " --accept-shell-risk" if prepared.requires_shell_warning else ""
    if prepared.requires_shell_warning:
        typer.echo(f"Aviso: {preaudit.SHELL_RISK}.")
    typer.echo(
        "Para enviarlo, consiente esta huella: "
        f"`preaudit run {dossier} --consent {prepared.payload.sha256} --by <nombre>{shell}`."
    )
    typer.echo(DECLARED_NAME_NOTICE)


@preaudit_app.command("run")
@handled
def preaudit_run(
    ctx: typer.Context,
    dossier: DossierIdArgument,
    provider: ProviderOption = None,
    consent: Annotated[
        str | None,
        typer.Option("--consent", help="Huella sha256 que muestra `preaudit prepare`."),
    ] = None,
    by: Annotated[str | None, typer.Option("--by", help="Quién consiente el envío.")] = None,
    accept_shell_risk: Annotated[
        bool,
        typer.Option("--accept-shell-risk", help="Acepta el aviso de shell del destino (codex)."),
    ] = False,
) -> None:
    """Ejecuta la pre-auditoría: la capa determinista siempre; la de juicio solo con `--consent`.

    Toma el lock del ciclo durante toda la operación. Sale con 1 si se rechaza o si la capa de
    juicio termina en ERROR, y con 2 si hay un ciclo en curso."""
    if consent is not None and not SHA256_ARG.fullmatch(consent):
        raise fail("--consent: indica la huella sha256 (64 caracteres hexadecimales).")
    settings = resolved_settings(ctx)
    store = open_store(settings)
    try:
        with exclusive_cycle(settings):
            result = preaudit.run(
                store,
                dossier,
                provider,
                consent,
                by,
                settings=settings.preaudit,
                now=clock(),
                accept_shell_risk=accept_shell_risk,
            )
    except preaudit.PreAuditUnconfigured as exc:
        raise fail(f"Error: {exc}") from None
    except preaudit.PreAuditRejected as exc:
        raise preaudit_rejected("No se ejecuta la pre-auditoría:", exc) from None
    record = result.record
    typer.echo(f"Pre-auditoría {record.seq} de {dossier} registrada.")
    print_preaudit_layers(record)
    if consent is None:
        typer.echo(
            "Sin --consent no se envía nada: prepara el envío con `preaudit prepare` y consiente "
            "su huella."
        )
    typer.echo(f"Detalle: `preaudit show {record.seq}`.")
    typer.echo(PREAUDIT_FOOTER)
    if record.run.judgment_status is LayerStatus.ERROR:
        raise typer.Exit(1)


@preaudit_app.command("show")
@handled
def preaudit_show(
    ctx: typer.Context,
    run: Annotated[int, typer.Argument(help="Número de la ejecución (ver `preaudit run`).")],
) -> None:
    """Muestra una ejecución: cada hallazgo con su propuesta, sus citas y su fuente."""
    store = open_workspace(ctx)
    try:
        stored = preaudit.show(store, run)
    except preaudit.PreAuditRejected as exc:
        raise preaudit_rejected("No se puede mostrar la pre-auditoría:", exc) from None
    view = findings_view(store, clock())
    if view.problems:
        raise dossier_rejected("El registro de disposiciones no se verifica:", list(view.problems))
    record = stored.record
    executed = record.run
    typer.echo(f"Pre-auditoría {record.seq} ({stored.key})")
    typer.echo(f"Expediente: {executed.dossier} (huella {executed.dossier_sha256})")
    typer.echo(f"Audit pack: {executed.pack_version} (huella {executed.pack_sha256})")
    typer.echo(f"Ejecutada: {executed.at.isoformat()}")
    if executed.consent is not None:
        consent = executed.consent
        typer.echo(f"Destino: {consent.destination} (modelo {executed.model})")
        typer.echo(
            f"Consentimiento: {consent.by}, {consent.at.isoformat()}, huella "
            f"{consent.payload_sha256}"
            + ("; riesgo de shell aceptado" if consent.shell_risk_accepted else "")
        )
    print_preaudit_layers(record)
    for finding in record.findings:
        typer.echo("")
        print_finding(finding)
        typer.echo(f"  Id: {finding.id}")
        typer.echo(f"  {disposition_line(finding.layer, view.state_of(finding.id))}")
        typer.echo(f"  {PROPOSAL_NOTICE[0].upper()}{PROPOSAL_NOTICE[1:]}.")
    for grouping in record.groupings:
        typer.echo("")
        typer.echo(
            f"Agrupación propuesta [{grouping.proposal.value}] {', '.join(grouping.measures)}: "
            f"{grouping.justification} ({grouping.source})"
        )
    typer.echo("")
    typer.echo(PREAUDIT_FOOTER)


def print_finding(finding: Finding) -> None:
    """Un hallazgo en lenguaje llano; los códigos de regla y de bloqueo quedan en el registro."""
    deterministic = finding.layer is Layer.DETERMINISTIC
    rule = RULE_LABELS[DeterministicRule(finding.criterion)] if deterministic else finding.criterion
    typer.echo(f"[{finding.proposal.value}] {finding.measure} — {rule} ({finding.layer.value})")
    typer.echo(f"  Motivo: {finding.reason}")
    for ref in finding.refs:
        typer.echo(f"  {REF_LABELS[ref.kind]}: {shown_ref(ref, deterministic)}")
    for line in (finding.quote or "").splitlines():
        typer.echo(f"    {line}")
    if deterministic:
        typer.echo(f"  Fuente de la clasificación: {finding.source}")
        return
    typer.echo(f"  Fuente del criterio: {finding.source}")
    if finding.classification_source:
        typer.echo(f"  Fuente de la clasificación: {finding.classification_source}")


def shown_ref(ref: Ref, deterministic: bool) -> str:
    """Una sección del expediente con su título; el resto de refs, tal cual."""
    if not (deterministic and ref.kind is RefKind.SECTION):
        return ref.id
    number = ref.id.split(":", 1)[0]
    if number.isdigit() and 1 <= int(number) <= len(SECTION_TITLES):
        return f"{number}. {SECTION_TITLES[int(number) - 1]}"
    return number


DISPOSITION_LABELS = {
    DispositionState.OPEN: "abierto, sin disponer",
    DispositionState.ACCEPTED: "reconocido, sigue abierto",
    DispositionState.RESOLVED: "resuelto",
    DispositionState.DISMISSED: "descartado",
}


def disposition_line(layer: Layer, state: FindingState | None) -> str:
    """El estado de un hallazgo como vista derivada: la ejecución que lo propuso no cambia."""
    if layer is Layer.DETERMINISTIC or state is None:
        return "Disposición: no se dispone; sus hechos ya bloquean la preparación por sí mismos."
    line = f"Disposición: {state.state.value} ({DISPOSITION_LABELS[state.state]})"
    if state.disposition is not None:
        decided = state.disposition
        line += f", {decided.by}, {decided.at.isoformat()}"
        if decided.evidence_id:
            line += f", evidencia {decided.evidence_id}"
        if decided.reason:
            line += f", motivo: «{decided.reason}»"
    return line + ("; bloquea la preparación." if state.blocks else "; no bloquea.")


class Decision(StrEnum):
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


@preaudit_app.command("review")
@handled
def preaudit_review(
    ctx: typer.Context,
    finding: Annotated[str, typer.Argument(help="Id del hallazgo (ver `preaudit show`).")],
    by: Annotated[str, typer.Option("--by", help="Quién decide (nombre declarado).")],
    decision: Annotated[
        Decision,
        typer.Option(
            "--decision",
            help="accepted (reconocido; sigue bloqueando), resolved (con --evidence) o "
            "dismissed (con --reason).",
        ),
    ],
    evidence: Annotated[
        str | None, typer.Option("--evidence", help="Evidencia que lo corrige (EV-…).")
    ] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="Motivo de la decisión.")] = None,
) -> None:
    """Registra la disposición humana de un hallazgo de juicio (solo adición).

    Solo `resolved` y `dismissed` dejan de bloquear la preparación. Toma el lock del ciclo; con
    un ciclo en curso sale con 2 sin escribir nada."""
    if not SHA256_ARG.fullmatch(finding):
        raise fail("FINDING: indica el id del hallazgo (64 caracteres hexadecimales).")
    settings = resolved_settings(ctx)
    store = open_store(settings)
    try:
        with exclusive_cycle(settings):
            stored, state = review(
                store,
                finding,
                DispositionState(decision.name),
                by=by,
                now=clock(),
                evidence_id=evidence,
                reason=reason,
            )
    except ReviewRejected as exc:
        raise dossier_rejected("No se registra la disposición:", exc.errors) from None
    typer.echo(f"{state.state.value} registrado para {finding} por {by} ({stored.key}).")
    typer.echo(f"Huella del registro: {stored.sha256}")
    typer.echo(disposition_line(Layer.JUDGMENT, state))
    typer.echo(DECLARED_NAME_NOTICE)
    typer.echo(PREAUDIT_FOOTER)


def print_dossier_preaudit(view: FindingsView, dossier: str) -> None:
    """Los hallazgos de juicio propuestos sobre `dossier` y su estado actual. Vista derivada: la
    instantánea no cambia."""
    found = [s for s in view.states if dossier in s.dossiers]
    typer.echo("Pre-auditoría (vista derivada; la instantánea no cambia):")
    if view.problems:
        typer.echo("  el registro de pre-auditorías no se verifica:", err=True)
        print_errors(list(view.problems))
        return
    if not found:
        typer.echo("  sin hallazgos de juicio registrados sobre este expediente.")
    for state in found:
        finding = state.finding
        typer.echo(
            f"  [{finding.proposal.value}] {finding.measure} — {finding.id} (ejecución "
            f"{state.run_seq}): {state.state.value}"
            + ("; bloquea la preparación" if state.blocks else "")
        )


def counted(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def discard_breakdown(discards: Mapping[DiscardReason, int]) -> str:
    """` (motivo: n; …)` en el orden de `DiscardReason`, o nada si no hay descartes."""
    parts = [f"{DISCARD_LABELS[r]}: {discards[r]}" for r in DiscardReason if discards.get(r)]
    return f" ({'; '.join(parts)})" if parts else ""


def print_preaudit_layers(record: PreAuditRecord) -> None:
    executed = record.run
    deterministic = [f for f in record.findings if f.layer is Layer.DETERMINISTIC]
    typer.echo(
        f"Capa determinista: {executed.deterministic_status.value}, "
        f"{counted(len(deterministic), 'hallazgo')}."
    )
    judged = len(record.findings) - len(deterministic)
    match executed.judgment_status:
        case LayerStatus.DONE:
            typer.echo(
                f"Capa de juicio: DONE, {counted(judged, 'hallazgo')}, "
                f"{counted(executed.discarded, 'descartado')} por la validación"
                + discard_breakdown(executed.discards)
                + "."
            )
        case LayerStatus.ERROR:
            typer.echo(
                f"Capa de juicio: ERROR ({executed.judgment_error}); sin hallazgos.", err=True
            )
            typer.echo("Revisión humana pendiente: la capa de juicio no dio resultado.")
        case LayerStatus.NOT_RUN:
            typer.echo("Capa de juicio: NOT_RUN.")
            typer.echo(f"Estado: {JUDGMENT_PENDING}.")
