"""Órdenes `ens-evidence benchmark …`. Miden la pre-auditoría; nunca declaran conformidad."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from ens_evidence.benchmark.access import HoldoutLocked, open_partition
from ens_evidence.benchmark.build import Build, build_bench_profile, inspect
from ens_evidence.benchmark.cases import (
    BenchError,
    case_paths,
    cases_dir,
    load_case,
    validate_bench,
)
from ens_evidence.benchmark.model import Partition
from ens_evidence.benchmark.mutations import build_case

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


@app.command("build")
def build(
    target: Annotated[
        str, typer.Argument(help="Un caso (`<partición>/<caso>`) o un perfil (`<perfil>`).")
    ],
    cases: CasesOption = BENCH_DIR,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Directorio nuevo o vacío; por defecto, uno temporal."),
    ] = None,
    measurement: MeasurementOption = False,
) -> None:
    """Construye el workspace de un caso o de un perfil y le pasa solo la capa determinista de
    la pre-auditoría. Imprime el expediente, la huella del payload y los hallazgos; no envía
    nada. Sale con 2 si no se puede construir."""
    if not cases.is_dir():
        raise _fail(f"Error: no existe el banco {cases}")
    if out is not None:
        _report(_build(cases, target, out, measurement))
        return
    with tempfile.TemporaryDirectory(prefix="ens-benchmark-") as scratch:
        _report(_build(cases, target, Path(scratch) / "ws", measurement))


def _build(root: Path, target: str, destination: Path, measurement: bool) -> Build:
    partition_name, slash, case_id = target.partition("/")
    try:
        if not slash:
            return build_bench_profile(root, target, destination)
        try:
            partition = Partition(partition_name)
        except ValueError:
            raise _fail(f"Error: partición desconocida «{partition_name}»: dev o holdout") from None
        open_partition(root, partition, action="build", measurement=measurement, now=clock())
        path = cases_dir(root, partition) / f"{case_id}.yaml"
        if not path.is_file():
            raise _fail(f"Error: no existe el caso {target}")
        return build_case(root, load_case(path, root), destination)
    except HoldoutLocked as exc:
        raise _fail(f"Error: {exc}") from None
    except BenchError as exc:
        for problem in exc.problems:
            typer.echo(problem, err=True)
        raise _fail(f"No se construye {target}") from None


def _report(build: Build) -> None:
    inspection = inspect(build)
    typer.echo(f"expediente: {inspection.dossier}")
    shown = inspection.payload_sha256 or "no aplica (caso solo determinista)"
    typer.echo(f"payload_sha256: {shown}")
    for problem in inspection.integrity:
        typer.echo(f"integridad: {problem}")
    typer.echo(f"hallazgos deterministas: {len(inspection.findings)}")
    for finding in inspection.findings:
        typer.echo(f"  {finding.criterion} {finding.measure}: {finding.reason}")
