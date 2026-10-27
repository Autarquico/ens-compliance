from pathlib import Path
from typing import Annotated

import typer

from ens_evidence import schemas

app = typer.Typer(help="Prepara evidencias para el ENS. No certifica.", no_args_is_help=True)
report_app = typer.Typer(help="Informes.", no_args_is_help=True)
assessment_app = typer.Typer(help="Evaluación.", no_args_is_help=True)
evidence_app = typer.Typer(help="Evidencias.", no_args_is_help=True)
schema_app = typer.Typer(help="Esquemas de los contratos.", no_args_is_help=True)
app.add_typer(report_app, name="report")
app.add_typer(assessment_app, name="assessment")
app.add_typer(evidence_app, name="evidence")
app.add_typer(schema_app, name="schema")

SCHEMAS_DIR = Path("schemas")


def not_implemented() -> None:
    typer.echo("not implemented", err=True)
    raise typer.Exit(2)


@app.command()
def inventory() -> None:
    """Inventario de activos."""
    not_implemented()


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


@evidence_app.command("verify")
def evidence_verify(evidence_id: str) -> None:
    """Verifica la integridad de una evidencia."""
    not_implemented()


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
