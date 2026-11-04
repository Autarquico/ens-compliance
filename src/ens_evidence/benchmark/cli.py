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
from ens_evidence.benchmark.recordings import (
    BenchCase,
    PreparedPartition,
    RecordingRefused,
    load_judgment_cases,
    prepare_cases,
    record_case,
    recording_path,
    write_recording,
)
from ens_evidence.benchmark.replay import Recorder
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.pack import PackError, load_pack
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.workspace import PreAuditSettings

BENCH_DIR = Path("benchmark")
REFUSED = 1
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
ProviderOption = Annotated[Provider, typer.Option("--provider", help="Destino de juicio.")]
ModelOption = Annotated[
    str, typer.Option("--model", help="Modelo configurado; sin valor por defecto.")
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


@app.command("prepare")
def prepare(
    provider: ProviderOption,
    model: ModelOption,
    partition: PartitionOption = Partition.DEV,
    cases: CasesOption = BENCH_DIR,
    measurement: MeasurementOption = False,
) -> None:
    """Construye y prepara cada caso de juicio de la partición, sin red. Imprime la huella del
    payload de cada caso y la huella combinada que `record --consent` debe repetir."""
    settings, judged = _recordable(cases, partition, provider, model, "prepare", measurement)
    with tempfile.TemporaryDirectory(prefix="ens-benchmark-") as scratch:
        prepared = _prepare(cases, judged, settings, Path(scratch), None)
    typer.echo(f"destino: {prepared.destination}")
    if prepared.requires_shell_warning:
        typer.echo(f"aviso: {service.SHELL_RISK}")
    for case in prepared.cases:
        typer.echo(f"{case.bench.case.key}  {case.prepared.payload.sha256}")
    typer.echo(f"huella combinada (sha256): {prepared.fingerprint}")
    typer.echo(
        f"Para grabar: ens-evidence benchmark record --partition {partition} "
        f"--provider {provider} --model {settings.model} --consent {prepared.fingerprint} "
        "--by <nombre>"
    )


@app.command("record")
def record(
    provider: ProviderOption,
    model: ModelOption,
    consent: Annotated[
        str, typer.Option("--consent", help="Huella combinada que imprimió `prepare`.")
    ],
    by: Annotated[str, typer.Option("--by", help="Quién consiente el envío (nombre declarado).")],
    partition: PartitionOption = Partition.DEV,
    repetitions: Annotated[int, typer.Option("--repetitions", min=1, help="Envíos por caso.")] = 3,
    accept_shell_risk: Annotated[
        bool,
        typer.Option("--accept-shell-risk", help="Necesario si el destino conserva una shell."),
    ] = False,
    replace: Annotated[
        bool, typer.Option("--replace", help="Sustituye las grabaciones que ya existan.")
    ] = False,
    cases: CasesOption = BENCH_DIR,
    measurement: MeasurementOption = False,
) -> None:
    """Envía de verdad cada caso de juicio por la pre-auditoría (`--repetitions` veces) y graba
    lo que devuelve el proveedor. Sin la huella combinada de `prepare` no envía nada. Sale con 1
    si se rechaza el envío o una grabación, y con 2 si la entrada no vale."""
    if errors := check_declared_name(by):
        raise _fail("\n".join(f"Error: {e}" for e in errors))
    settings, judged = _recordable(cases, partition, provider, model, "record", measurement)
    if not replace and (existing := _existing(cases, judged, settings)):
        raise _fail(f"Error: ya grabado: {', '.join(existing)}; usa --replace", REFUSED)
    recorder = Recorder()
    with tempfile.TemporaryDirectory(prefix="ens-benchmark-") as scratch:
        prepared = _prepare(cases, judged, settings, Path(scratch), recorder)
        if prepared.fingerprint != consent:
            raise _fail(
                "Error: la huella no coincide con lo que se enviaría; ejecuta `benchmark "
                "prepare` y revisa. No se ha enviado nada",
                REFUSED,
            )
        if prepared.requires_shell_warning and not accept_shell_risk:
            raise _fail(f"Error: {service.SHELL_RISK}. No se ha enviado nada", REFUSED)
        refused = _record_all(
            cases, prepared, settings, by, repetitions, recorder, replace, accept_shell_risk
        )
    if refused:
        raise _fail(f"{refused} grabaciones no se escribieron", REFUSED)


def _recordable(
    root: Path,
    partition: Partition,
    provider: Provider,
    model: str,
    action: str,
    measurement: bool,
) -> tuple[PreAuditSettings, list[BenchCase]]:
    if not root.is_dir():
        raise _fail(f"Error: no existe el banco {root}")
    if not model.strip():
        raise _fail("Error: --model: indica el modelo; no hay valor por defecto")
    settings = PreAuditSettings(provider=provider, model=model)
    try:
        pack_sha256 = load_pack().sha256
    except PackError as exc:
        raise _fail(f"Error: el audit pack no es íntegro: {exc}") from None
    try:
        open_partition(
            root,
            partition,
            action=action,
            measurement=measurement,
            now=clock(),
            pack_sha256=pack_sha256,
            provider=provider,
            model=settings.model,
        )
        judged, skipped = load_judgment_cases(root, partition)
    except HoldoutLocked as exc:
        raise _fail(f"Error: {exc}") from None
    except BenchError as exc:
        raise _fail("\n".join(exc.problems)) from None
    if skipped:
        typer.echo(f"solo deterministas, sin juicio que grabar: {', '.join(skipped)}")
    return settings, judged


def _existing(root: Path, judged: list[BenchCase], settings: PreAuditSettings) -> list[str]:
    return [
        bench.case.key
        for bench in judged
        if recording_path(root, bench.case.key, settings.provider, settings.model).exists()
    ]


def _prepare(
    root: Path,
    judged: list[BenchCase],
    settings: PreAuditSettings,
    scratch: Path,
    recorder: Recorder | None,
) -> PreparedPartition:
    try:
        return prepare_cases(root, judged, settings, scratch, client_factory=recorder)
    except BenchError as exc:
        raise _fail("\n".join(exc.problems)) from None
    except service.PreAuditRejected as exc:
        raise _fail("\n".join(f"Error: {e}" for e in exc.errors)) from None


def _record_all(
    root: Path,
    prepared: PreparedPartition,
    settings: PreAuditSettings,
    by: str,
    repetitions: int,
    recorder: Recorder,
    replace: bool,
    accept_shell_risk: bool,
) -> int:
    """Graba caso a caso; lo ya escrito se queda aunque otro caso se rechace. Devuelve cuántas
    grabaciones no se escribieron."""
    refused = 0
    for case in prepared.cases:
        key = case.bench.case.key
        try:
            recording = record_case(
                case,
                settings,
                by=by,
                repetitions=repetitions,
                accept_shell_risk=accept_shell_risk,
                recorder=recorder,
                clock=clock,
            )
            path = write_recording(root, recording, replace=replace)
        except service.PreAuditRejected as exc:
            raise _fail("\n".join(f"Error: {key}: {e}" for e in exc.errors), REFUSED) from None
        except RecordingRefused as exc:
            refused += 1
            for problem in exc.problems:
                typer.echo(f"no se graba: {problem}", err=True)
            continue
        outcomes = ", ".join(r.outcome.value for r in recording.repetitions)
        typer.echo(f"{key}: {path.relative_to(root).as_posix()} ({outcomes})")
    return refused
