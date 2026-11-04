"""Órdenes `ens-evidence benchmark …`. Miden la pre-auditoría; nunca declaran conformidad."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from ens_evidence.benchmark.access import HoldoutLocked, open_partition
from ens_evidence.benchmark.cases import case_paths, validate_bench
from ens_evidence.benchmark.model import Partition

BENCH_DIR = Path("benchmark")
INVALID = 2

app = typer.Typer(
    help="Banco de defectos sembrados: mide la pre-auditoría; no aprueba expedientes.",
    no_args_is_help=True,
)

CasesOption = Annotated[
    Path, typer.Option("--cases", help="Raíz del banco (profiles/, cases/, recordings/).")
]
PartitionOption = Annotated[Partition, typer.Option("--partition", help="Partición.")]
MeasurementOption = Annotated[
    bool,
    typer.Option("--measurement", help="Necesario para la reservada; queda anotado."),
]


def clock() -> datetime:
    return datetime.now(UTC)


def _fail(message: str, code: int = INVALID) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


@app.command("validate")
def validate(
    cases: CasesOption = BENCH_DIR,
    partition: PartitionOption = Partition.DEV,
    measurement: MeasurementOption = False,
) -> None:
    """Valida casos y perfiles: esquema, citas literales del anexo II, ids únicos, particiones
    disjuntas y datos ficticios. Sale con 2 si algo no vale."""
    if not cases.is_dir():
        raise _fail(f"Error: no existe el banco {cases}")
    try:
        open_partition(cases, partition, action="validate", measurement=measurement, now=clock())
    except HoldoutLocked as exc:
        raise _fail(f"Error: {exc}") from None
    problems = validate_bench(cases, partition)
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise _fail(f"{len(problems)} problemas en la partición {partition}")
    typer.echo(f"banco válido: {len(case_paths(cases, partition))} casos en {partition}")
