"""Runner del banco: repite las grabaciones a través de la pre-auditoría real y puntúa.

Cada caso se construye (con el perfil base cacheado), se prepara con `service.prepare` y se
ejecuta tres veces con `service.run`, con un `ReplayProvider` como destino. Los hallazgos se
leen de los `PreAuditRecord` escritos. Un caso solo determinista se ejecuta sin consentimiento
ni proveedor. Nada sale a la red.
"""

import hashlib
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ens_evidence.benchmark.build import BASE_STEPS, Build, build_bench_profile
from ens_evidence.benchmark.cases import (
    BenchError,
    case_files_dir,
    case_paths,
    load_case,
    load_recording,
    recording_problems,
)
from ens_evidence.benchmark.model import Case, Partition, Recording
from ens_evidence.benchmark.mutations import finish_case, resolve_targets
from ens_evidence.benchmark.recordings import (
    RECORDINGS_DIR,
    BenchCase,
    model_slug,
    read_recording,
    recording_path,
)
from ens_evidence.benchmark.replay import RecordingStale, replay_factory
from ens_evidence.benchmark.score import CaseRun, ExpectedDefect, Observed, RepetitionRun
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload
from ens_evidence.workspace import PreAuditSettings

REPETITIONS = 3
REPLAYED_BY = "repetición del banco"
"""Quién consiente en la repetición: no se envía nada, el destino es la grabación."""


@dataclass(frozen=True, order=True)
class Pair:
    provider: Provider
    model: str

    def __str__(self) -> str:
        return f"{self.provider.value}/{self.model}"

    @property
    def settings(self) -> PreAuditSettings:
        return PreAuditSettings(provider=self.provider, model=self.model)


REQUIRED_PAIRS = (
    Pair(Provider.CLAUDE_CODE, "claude-opus-5-5"),
    Pair(Provider.CODEX, "gpt-6.1-sol"),
)
"""Los pares que pasan el umbral de v0.1 (decisión del usuario D4): el plan contratado."""


class RecordingMissing(Exception):
    """Falta la grabación de un caso de juicio para un proveedor y modelo."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --- casos -----------------------------------------------------------------------------------


def load_cases(root: Path, partition: Partition) -> list[BenchCase]:
    """Todos los casos de la partición, de juicio o solo deterministas. `BenchError` si alguno
    no se puede leer."""
    return [
        BenchCase(load_case(path, root), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in case_paths(root, partition)
    ]


class ProfileCache:
    """Construye cada perfil base una vez por ejecución y cada caso sobre una copia suya. Da el
    mismo workspace que `build_case` (mismo reloj, mismos ids), sin pagar el perfil en cada caso."""

    def __init__(self, scratch: Path) -> None:
        self._scratch = scratch
        self._bases: dict[str, Build] = {}

    def __call__(self, root: Path, case: Case, destination: Path) -> Build:
        if case.profile is None:
            raise BenchError([f"{case.key}: las bases `workspace:` aún no se construyen"])
        base = self._bases.get(case.profile)
        if base is None:
            where = self._scratch / case.profile
            base = build_bench_profile(root, case.profile, where, steps=BASE_STEPS)
            self._bases[case.profile] = base
        return finish_case(base.fork(destination), case, case_files_dir(root, case))


CaseBuilder = Callable[[Path, Case, Path], Build]


# --- grabaciones -----------------------------------------------------------------------------


def recorded_pairs(root: Path, partition: Partition) -> list[Pair]:
    """Los proveedores y modelos con alguna grabación en la partición."""
    directory = root / RECORDINGS_DIR / partition.value
    pairs = {
        Pair(recording.provider, recording.model)
        for path in sorted(directory.glob("*/*.json"))
        for recording in [load_recording(path, root)]
    }
    return sorted(pairs)


def required_pair_problems(pairs: Sequence[Pair]) -> list[str]:
    return [f"falta la grabación de {p}, que es puerta" for p in REQUIRED_PAIRS if p not in pairs]


def recordings_for(
    root: Path, cases: Sequence[BenchCase], pairs: Sequence[Pair]
) -> dict[tuple[str, Pair], Recording]:
    """La grabación de cada caso de juicio para cada par. `RecordingMissing` si falta alguna;
    `RecordingStale` si no tiene tres repeticiones o es de otro par."""
    found: dict[tuple[str, Pair], Recording] = {}
    missing = []
    for bench in cases:
        key = bench.case.key
        if not bench.case.judgment:
            continue
        for pair in pairs:
            if not recording_path(root, key, pair.provider, pair.model).is_file():
                missing.append(f"{key}: no hay grabación de {pair}")
                continue
            recording = read_recording(root, key, pair.provider, pair.model)
            if Pair(recording.provider, recording.model) != pair or recording.case != key:
                raise RecordingStale(f"{key}: la grabación de {pair} es de otro caso o par")
            if len(recording.repetitions) != REPETITIONS:
                raise RecordingStale(
                    f"{key}: la grabación de {pair} tiene {len(recording.repetitions)} "
                    f"repeticiones; se exigen {REPETITIONS}"
                )
            found[(key, pair)] = recording
    if missing:
        raise RecordingMissing(missing)
    return found


def recording_sha256(root: Path, recording: Recording) -> str:
    path = recording_path(root, recording.case, recording.provider, recording.model)
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- ejecutar --------------------------------------------------------------------------------


@dataclass
class PartitionRun:
    """Lo que el informe necesita de una ejecución, por proveedor y modelo."""

    cases: list[BenchCase]
    pairs: list[Pair]
    runs: dict[Pair, list[CaseRun]] = field(default_factory=dict)
    recordings: dict[tuple[str, Pair], Recording] = field(default_factory=dict)


def run_partition(
    root: Path,
    cases: Sequence[BenchCase],
    pairs: Sequence[Pair],
    scratch: Path,
    *,
    builder: CaseBuilder | None = None,
) -> PartitionRun:
    """Repite cada caso para cada par. `RecordingMissing`/`RecordingStale` si una grabación
    falta o está desligada; `BenchError` o `PreAuditRejected` si un caso no se puede ejecutar."""
    recordings = recordings_for(root, cases, pairs)
    build = builder or ProfileCache(scratch / "profiles")
    result = PartitionRun(list(cases), list(pairs), {p: [] for p in pairs}, recordings)
    for bench in cases:
        case = bench.case
        built = build(root, case, scratch / "cases" / case.id)
        defects = expected_defects(built, case)
        if not case.judgment:
            shared = _deterministic_run(bench, built, defects)
            for pair in pairs:
                result.runs[pair].append(shared)
            continue
        for pair in pairs:
            slug = f"{pair.provider.value}--{model_slug(pair.model)}"
            copy = built.fork(scratch / "runs" / case.id / slug)
            run = _replayed_run(bench, copy, defects, recordings[(case.key, pair)], pair)
            result.runs[pair].append(run)
    return result


def expected_defects(build: Build, case: Case) -> list[ExpectedDefect]:
    targets = resolve_targets(build, case)
    return [
        ExpectedDefect(
            key=f"{case.key}#{d.id}",
            layer=d.layer,
            measure=d.measure,
            expected=str(d.expected_rule or d.expected_criterion),
            target=targets[d.id],
        )
        for d in case.defects
    ]


def _deterministic_run(bench: BenchCase, build: Build, defects: list[ExpectedDefect]) -> CaseRun:
    assert build.dossier is not None, "la construcción termina en la instantánea"
    repetitions = []
    for n in range(1, REPETITIONS + 1):
        written = service.run(
            build.store, build.dossier, None, None, None, settings=None, now=build.tick()
        )
        repetitions.append(RepetitionRun(n, _findings(build, written.record.seq)))
    return CaseRun(
        case=bench.case.key,
        kind=bench.case.kind,
        case_sha256=bench.sha256,
        repetitions=repetitions,
        defects=defects,
        judgment=False,
    )


def _replayed_run(
    bench: BenchCase,
    build: Build,
    defects: list[ExpectedDefect],
    recording: Recording,
    pair: Pair,
) -> CaseRun:
    assert build.dossier is not None, "la construcción termina en la instantánea"
    settings = pair.settings
    prepared = service.prepare(
        build.store,
        build.dossier,
        None,
        settings,
        now=build.tick(),
        client_factory=replay_factory(recording, 1),
    )
    if prepared.payload.sha256 != recording.payload_sha256:
        raise RecordingStale(f"{bench.case.key}: el payload de {pair} ha cambiado; regraba")
    repetitions = []
    for repetition in recording.repetitions:
        written = service.run(
            build.store,
            build.dossier,
            None,
            prepared.payload.sha256,
            REPLAYED_BY,
            settings=settings,
            now=build.tick(),
            client_factory=replay_factory(recording, repetition.n),
        )
        repetitions.append(
            RepetitionRun(repetition.n, _findings(build, written.record.seq), repetition.outcome)
        )
    return CaseRun(
        case=bench.case.key,
        kind=bench.case.kind,
        case_sha256=bench.sha256,
        repetitions=repetitions,
        defects=defects,
        payload_sha256=prepared.payload.sha256,
    )


def _findings(build: Build, seq: int) -> list[Observed]:
    """Los hallazgos tal como quedaron escritos en la cadena de pre-auditorías."""
    record = service.show(build.store, seq).record
    return [Observed.of(finding) for finding in record.findings]


# --- validar grabaciones ---------------------------------------------------------------------


def recording_check(root: Path, partition: Partition) -> tuple[list[str], list[str], int]:
    """Las grabaciones de la partición: (las que no valen o llevan un dato detectable, las
    desligadas del caso, del pack o del payload actuales, cuántas hay). Construye cada caso
    grabado para comparar su payload. `BenchError` si un caso no se puede construir."""
    directory = root / RECORDINGS_DIR / partition.value
    cases = {bench.case.key: bench.case for bench in load_cases(root, partition)}
    pack_sha256 = load_pack().sha256
    invalid: list[str] = []
    stale: list[str] = []
    bound: dict[str, list[Recording]] = {}
    paths = sorted(directory.glob("*/*.json"))
    for path in paths:
        try:
            recording = load_recording(path, root)
        except BenchError as exc:
            invalid += exc.problems
            continue
        shown = path.relative_to(root).as_posix()
        invalid += [f"{shown}: {p}" for p in recording_problems(recording)]
        case = cases.get(recording.case)
        if recording.case != f"{partition.value}/{path.parent.name}":
            stale.append(f"{shown}: es del caso {recording.case}, no de su directorio")
        elif case is None:
            stale.append(f"{shown}: el caso ya no existe")
        elif not case.judgment:
            stale.append(f"{shown}: el caso es solo determinista; no se graba")
        elif recording.pack_sha256 != pack_sha256:
            stale.append(f"{shown}: el audit pack ha cambiado; regraba")
        else:
            bound.setdefault(recording.case, []).append(recording)
    with tempfile.TemporaryDirectory(prefix="ens-benchmark-") as scratch:
        build = ProfileCache(Path(scratch) / "profiles")
        for key, recordings in sorted(bound.items()):
            built = build(root, cases[key], Path(scratch) / "cases" / cases[key].id)
            assert built.dossier is not None, "la construcción termina en la instantánea"
            payload_sha256 = build_payload(built.store, built.dossier, load_pack()).sha256
            stale += [
                f"{key}: la grabación de {r.provider.value}/{r.model} no corresponde al payload "
                "actual; regraba"
                for r in recordings
                if r.payload_sha256 != payload_sha256
            ]
    return invalid, stale, len(paths)
