"""Órdenes `ens-evidence benchmark …`. Miden la pre-auditoría; nunca declaran conformidad."""

import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from ens_evidence.benchmark.access import HoldoutLocked, append_measurement, open_partition
from ens_evidence.benchmark.baseline import (
    BASELINE_FILE,
    BaselineUnreadable,
    build_baseline,
    diff,
    empty_baseline,
    inventory,
    read_file,
    read_ref,
    working_tree_retired,
    write_baseline,
)
from ens_evidence.benchmark.build import Build, build_bench_profile, inspect
from ens_evidence.benchmark.cases import (
    BenchError,
    case_paths,
    cases_dir,
    load_case,
    load_profile,
    validate_bench,
)
from ens_evidence.benchmark.model import (
    Baseline,
    BenchmarkResult,
    Measurement,
    Partition,
    ProviderResult,
)
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
from ens_evidence.benchmark.replay import Recorder, RecordingStale
from ens_evidence.benchmark.report import assemble, render_text
from ens_evidence.benchmark.runner import (
    REQUIRED_PAIRS,
    Pair,
    PartitionRun,
    RecordingMissing,
    load_cases,
    recorded_pairs,
    recording_check,
    required_pair_problems,
    run_partition,
)
from ens_evidence.benchmark.score import (
    HOLDOUT_MINIMUM,
    Score,
    entry_of,
    sample_of,
    sample_problems,
)
from ens_evidence.catalog.model import SecurityLevel
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.pack import PackError, load_pack
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.workspace import PreAuditSettings

BENCH_DIR = Path("benchmark")
REFUSED = 1
INVALID = 2
STALE = 3

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
    recordings: Annotated[
        bool,
        typer.Option("--recordings", help="Comprueba también las grabaciones y su ligadura."),
    ] = False,
    measurement: MeasurementOption = False,
) -> None:
    """Valida casos y perfiles: esquema, citas literales del anexo II, ids únicos, particiones
    disjuntas y datos ficticios. Sale con 2 si algo no vale. Con `--recordings`, también las
    grabaciones: 2 si una no vale o lleva un dato detectable, 3 si está desligada del pack o
    del payload actuales."""
    if not cases.is_dir():
        raise _fail(f"Error: no existe el banco {cases}")
    try:
        open_partition(cases, partition, action="validate", measurement=measurement, now=clock())
    except HoldoutLocked as exc:
        raise _fail(f"Error: {exc}") from None
    problems = validate_bench(cases, partition)
    if measurement and partition is Partition.HOLDOUT and not problems:
        problems += _measurement_problems(cases)
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise _fail(f"{len(problems)} problemas en la partición {partition}")
    typer.echo(f"banco válido: {len(case_paths(cases, partition))} casos en {partition}")
    if recordings:
        _validate_recordings(cases, partition)


def _measurement_problems(root: Path) -> list[str]:
    """D7 de la reservada: imprime los recuentos y devuelve lo que falta para medirla."""
    cases = [load_case(path, root) for path in case_paths(root, Partition.HOLDOUT)]
    sample = sample_of(cases)
    typer.echo(
        f"reservada: {sample.judgment} defectos de juicio, {sample.deterministic} "
        f"deterministas, {sample.correct} expedientes correctos "
        f"(mínimo {HOLDOUT_MINIMUM.judgment}/{HOLDOUT_MINIMUM.deterministic}/"
        f"{HOLDOUT_MINIMUM.correct})"
    )
    problems = sample_problems(sample, HOLDOUT_MINIMUM)
    basic = {
        profile_id: all(level is SecurityLevel.BAJO for level in profile.categorization.values())
        for profile_id in sorted({c.profile for c in cases if c.profile})
        for profile in [load_profile(root, Partition.HOLDOUT, profile_id)]
    }
    problems += [f"perfil {p}: no es de categoría BÁSICA" for p, ok in basic.items() if not ok]
    return problems


def _validate_recordings(root: Path, partition: Partition) -> None:
    try:
        problems, stale, checked = recording_check(root, partition)
    except BenchError as exc:
        raise _fail("\n".join(exc.problems)) from None
    for problem in problems + stale:
        typer.echo(problem, err=True)
    if problems:
        raise _fail(f"{len(problems)} grabaciones no valen en {partition}")
    if stale:
        raise _fail(f"{len(stale)} grabaciones desligadas en {partition}; regraba", STALE)
    typer.echo(f"grabaciones ligadas: {checked} en {partition}")


@app.command("build")
def build(
    target: Annotated[
        str,
        typer.Argument(
            help="Un caso (`<partición>/<caso>`) o un perfil (`<perfil>`, de `--partition`)."
        ),
    ],
    cases: CasesOption = BENCH_DIR,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Directorio nuevo o vacío; por defecto, uno temporal."),
    ] = None,
    partition: PartitionOption = Partition.DEV,
    measurement: MeasurementOption = False,
) -> None:
    """Construye el workspace de un caso o de un perfil y le pasa solo la capa determinista de
    la pre-auditoría. Imprime el expediente, la huella del payload y los hallazgos; no envía
    nada. Sale con 2 si no se puede construir."""
    if not cases.is_dir():
        raise _fail(f"Error: no existe el banco {cases}")
    if out is not None:
        _report(_build(cases, target, out, partition, measurement))
        return
    with tempfile.TemporaryDirectory(prefix="ens-benchmark-") as scratch:
        _report(_build(cases, target, Path(scratch) / "ws", partition, measurement))


def _build(
    root: Path, target: str, destination: Path, profile_partition: Partition, measurement: bool
) -> Build:
    partition_name, slash, case_id = target.partition("/")
    try:
        if not slash:
            open_partition(
                root, profile_partition, action="build", measurement=measurement, now=clock()
            )
            return build_bench_profile(root, profile_partition, target, destination)
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
        "--by <nombre>" + (" --accept-shell-risk" if prepared.requires_shell_warning else "")
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


# --- repetir y puntuar -----------------------------------------------------------------------


class GateChoice(StrEnum):
    BASELINE = "baseline"
    THRESHOLD = "threshold"
    TARGET = "target"


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


@app.command("run")
def run(
    partition: PartitionOption = Partition.DEV,
    provider: Annotated[
        Provider | None, typer.Option("--provider", help="Un proveedor grabado.")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Su modelo grabado.")] = None,
    all_recorded: Annotated[
        bool, typer.Option("--all-recorded", help="Todos los proveedores y modelos grabados.")
    ] = False,
    gate: Annotated[GateChoice | None, typer.Option("--gate", help="Puerta que aplicar.")] = None,
    baseline_file: Annotated[
        Path | None, typer.Option("--baseline", help="Línea base con la que comparar.")
    ] = None,
    baseline_ref: Annotated[
        str | None,
        typer.Option("--baseline-ref", help="Ref de git cuya línea base se compara."),
    ] = None,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TEXT,
    out: Annotated[Path | None, typer.Option("--out", help="Escribe el informe aquí.")] = None,
    cases: CasesOption = BENCH_DIR,
    measurement: MeasurementOption = False,
) -> None:
    """Repite las grabaciones por la pre-auditoría (`service.prepare` y `service.run`, sin red)
    y puntúa por proveedor y modelo. Sale con 0 si pasa, 1 si falla la puerta, hay regresión o
    el banco está vacío, 2 si un caso, una grabación o una opción no vale y 3 si una grabación
    falta o está desligada."""
    if (provider is None) != (model is None) or all_recorded == (provider is not None):
        raise _fail("Error: indica --provider y --model, o --all-recorded")
    if baseline_file is not None and baseline_ref is not None:
        raise _fail("Error: --baseline y --baseline-ref se excluyen")
    if gate is GateChoice.BASELINE and partition is not Partition.DEV:
        raise _fail("Error: la puerta baseline es de la partición de desarrollo")
    if gate is GateChoice.THRESHOLD and partition is not Partition.HOLDOUT:
        raise _fail("Error: la puerta threshold es de la partición reservada")
    if gate is GateChoice.TARGET and partition is not Partition.DEV:
        raise _fail("Error: la puerta target es de la partición de desarrollo")
    if gate is GateChoice.TARGET and not all_recorded:
        raise _fail(
            "Error: la puerta target exige --all-recorded: deciden los dos pares de la puerta"
        )
    pair = Pair(provider, model) if provider is not None and model is not None else None
    replayed = _replay(cases, partition, pair, gate is not None, "run", measurement)
    pack_sha256, bench, ran = replayed
    compared = retired = source = found = None
    if gate is GateChoice.BASELINE:
        compared, source = _baseline_for(cases, baseline_file, baseline_ref, pack_sha256)
        retired = _guarded(lambda: working_tree_retired(cases))
        found = _inventory(cases, bench)
    gating = list(REQUIRED_PAIRS) if all_recorded else ran.pairs
    result = assemble(
        cases,
        partition,
        ran,
        now=clock(),
        gate=gate.value if gate else None,
        gating=gating,
        baseline=compared,
        baseline_source=source,
        inventory=found,
        retired=retired or [],
    )
    shown = gating if gate in (GateChoice.THRESHOLD, GateChoice.TARGET) else ()
    if output_format is OutputFormat.JSON:
        text = result.model_dump_json(indent=2) + "\n"
    else:
        text = render_text(result, shown)
    if out is None:
        typer.echo(text, nl=False)
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"informe: {out}")
    if partition is Partition.HOLDOUT:
        for provider_result in result.results:
            append_measurement(cases, _measured(result, provider_result))
    if result.gate_passed is False:
        raise _fail(f"la puerta {result.gate} no pasa", REFUSED)


@app.command("baseline")
def baseline(
    partition: PartitionOption = Partition.DEV,
    write: Annotated[
        bool, typer.Option("--write", help="Escribe baseline/dev.json; sin ella, solo el diff.")
    ] = False,
    cases: CasesOption = BENCH_DIR,
) -> None:
    """Repite todo lo grabado de la partición de desarrollo y escribe su línea base
    (`baseline/dev.json`), conservando las retiradas anotadas. Imprime un diff resumido."""
    if partition is not Partition.DEV:
        raise _fail("Error: la línea base es de la partición de desarrollo")
    pack_sha256, bench, ran = _replay(cases, partition, None, False, "baseline", False)
    previous = _guarded(lambda: read_file(cases / BASELINE_FILE))
    retired = previous.retired if previous else []
    result = assemble(cases, partition, ran, now=clock())
    entries = [entry_of(r.provider, r.model, Score(r.cases, r.totals)) for r in result.results]
    written = build_baseline(clock(), pack_sha256, _inventory(cases, bench), entries, retired)
    for line in diff(previous or empty_baseline(clock(), pack_sha256), written):
        typer.echo(line)
    if write:
        typer.echo(f"escrito: {write_baseline(cases, written).relative_to(cases).as_posix()}")


def _replay(
    root: Path,
    partition: Partition,
    pair: Pair | None,
    gated: bool,
    action: str,
    measurement: bool,
) -> tuple[str, list[BenchCase], PartitionRun]:
    if not root.is_dir():
        raise _fail(f"Error: no existe el banco {root}")
    if pair is not None and not pair.model.strip():
        raise _fail("Error: --model: indica el modelo; no hay valor por defecto")
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
            provider=pair.provider if pair else None,
            model=pair.model if pair else None,
        )
        bench = load_cases(root, partition)
    except HoldoutLocked as exc:
        raise _fail(f"Error: {exc}") from None
    except BenchError as exc:
        raise _fail("\n".join(exc.problems)) from None
    if not bench:
        raise _fail(f"Error: el banco está vacío en {partition}; no hay nada que medir", REFUSED)
    try:
        pairs = [pair] if pair else recorded_pairs(root, partition)
    except BenchError as exc:
        raise _fail("\n".join(exc.problems)) from None
    if pair is None and gated and (missing := required_pair_problems(pairs)):
        raise _fail("\n".join(f"Error: {m}" for m in missing), STALE)
    with tempfile.TemporaryDirectory(prefix="ens-benchmark-") as scratch:
        try:
            ran = run_partition(root, bench, pairs, Path(scratch))
        except RecordingMissing as exc:
            raise _fail("\n".join(f"Error: {p}" for p in exc.problems), STALE) from None
        except RecordingStale as exc:
            raise _fail(f"Error: {exc}", STALE) from None
        except BenchError as exc:
            raise _fail("\n".join(exc.problems)) from None
        except service.PreAuditRejected as exc:
            raise _fail("\n".join(f"Error: {e}" for e in exc.errors)) from None
    return pack_sha256, bench, ran


def _inventory(root: Path, bench: list[BenchCase]) -> dict[str, str]:
    try:
        return inventory(root, bench)
    except BenchError as exc:
        raise _fail("\n".join(exc.problems)) from None


def _guarded[T](read: Callable[[], T]) -> T:
    try:
        return read()
    except BaselineUnreadable as exc:
        raise _fail(f"Error: línea base: {exc}") from None


def _baseline_for(
    root: Path, file: Path | None, ref: str | None, pack_sha256: str
) -> tuple[Baseline, str]:
    if file is not None:
        found = _guarded(lambda: read_file(file))
        if found is None:
            raise _fail(f"Error: no existe la línea base {file}")
        return found, file.as_posix()
    if ref is not None:
        found = _guarded(lambda: read_ref(root, ref))
        source = f"{ref}:{BASELINE_FILE}"
        if found is None:
            return empty_baseline(clock(), pack_sha256), f"{source} (no existe: vacía)"
        return found, source
    found = _guarded(lambda: read_file(root / BASELINE_FILE))
    if found is None:
        return empty_baseline(clock(), pack_sha256), f"{BASELINE_FILE} (no existe: vacía)"
    return found, BASELINE_FILE


def _measured(result: BenchmarkResult, provider: ProviderResult) -> Measurement:
    totals = provider.totals
    return Measurement(
        at=result.run_at,
        action="run-result",
        pack_sha256=result.pack_sha256,
        provider=provider.provider,
        model=provider.model,
        result={
            "gate": result.gate,
            "gate_passed": provider.gate_passed,
            "deterministic": totals.deterministic.value,
            "judgment": totals.judgment.value,
            "false_alarms": totals.false_alarms.value,
            "correct_with_false_alarms": totals.correct_with_false_alarms.value,
        },
    )
