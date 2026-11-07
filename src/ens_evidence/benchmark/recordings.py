"""Grabaciones del banco: preparar los casos de juicio de una partición, grabar lo que devuelve el
proveedor y leerlo para repetirlo (plan, «Grabaciones y su ligadura»).

Disposición: `recordings/<partición>/<caso>/<provider>--<model-slug>.json`. Se graba solo lo que
devuelve `judge()`: el texto si pasa el esquema; si no, `schema_error`; de un `ProviderError`,
solo su tipo. Nunca stdout ni stderr. Antes de escribir se escanea todo valor decodificado; si
algo parece un dato real o un secreto, no se escribe.

El consentimiento es una huella combinada: proveedor, modelo, destino, pack y la lista ordenada
de huellas de payload de cada caso. Sin ella, `record` no envía nada.
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import ValidationError

from ens_evidence import __version__
from ens_evidence.benchmark.build import Build
from ens_evidence.benchmark.cases import (
    BenchError,
    case_paths,
    load_case,
    load_recording,
    recording_problems,
)
from ens_evidence.benchmark.model import Case, Partition, Recording, Repetition, RepetitionOutcome
from ens_evidence.benchmark.mutations import build_case
from ens_evidence.benchmark.replay import Captured, Recorder
from ens_evidence.catalog import catalog_sha256
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.adapter import parse_response
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import canonical_json
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import PreAuditSettings

RECORDINGS_DIR = "recordings"
RECORDING_FORMAT = "ens-benchmark-recording/1"
CONSENT_FORMAT = "ens-benchmark-consent/1"
_EXECUTABLE = re.compile(r"(ejecutable )(.+?)( \(sha256 )")
_NOT_SLUG = re.compile(r"[^a-z0-9._-]+")

CaseBuilder = Callable[[Path, Case, Path], Build]


class RecordingRefused(Exception):
    """La grabación no se escribe: parece llevar un dato real o un secreto, o ya existe."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --- rutas y ficheros ------------------------------------------------------------------------


def model_slug(model: str) -> str:
    slug = _NOT_SLUG.sub("-", model.strip().lower()).strip("-.")
    return slug or "modelo"


def recording_path(root: Path, case_key: str, provider: Provider, model: str) -> Path:
    """`recordings/<partición>/<caso>/<provider>--<model-slug>.json` bajo la raíz del banco."""
    return root / RECORDINGS_DIR / case_key / f"{provider.value}--{model_slug(model)}.json"


def read_recording(root: Path, case_key: str, provider: Provider, model: str) -> Recording:
    """`BenchError` si no existe o no cumple el contrato."""
    return load_recording(recording_path(root, case_key, provider, model), root)


def portable_destination(destination: str) -> str:
    """El destino sin la ruta absoluta del ejecutable de la CLI: queda su nombre y su sha256."""

    def basename(match: re.Match[str]) -> str:
        path = match.group(2)
        name = (PureWindowsPath(path) if "\\" in path else PurePosixPath(path)).name
        return f"{match.group(1)}{name}{match.group(3)}"

    return _EXECUTABLE.sub(basename, destination)


def write_recording(root: Path, recording: Recording, *, replace: bool = False) -> Path:
    """Escribe la grabación en su sitio. `RecordingRefused` si el escaneo detecta algo (no se
    escribe nada) o si ya existe y no se pide `replace`."""
    if problems := recording_problems(recording):
        raise RecordingRefused([f"{recording.case}: {p}" for p in problems])
    case_key = recording.case
    path = recording_path(root, case_key, recording.provider, recording.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(recording.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    if not replace:
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(data)
        except FileExistsError:
            raise RecordingRefused([f"{case_key}: ya está grabado; usa --replace"]) from None
        return path
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as stream:
        stream.write(data)
    os.replace(stream.name, path)
    return path


# --- preparar --------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchCase:
    case: Case
    sha256: str
    """sha256 del YAML del caso."""


def load_judgment_cases(root: Path, partition: Partition) -> tuple[list[BenchCase], list[str]]:
    """Los casos de juicio de la partición y las claves de los solo deterministas, que no se
    preparan ni se graban. `BenchError` si un caso no se puede leer."""
    judged, skipped = [], []
    for path in case_paths(root, partition):
        case = load_case(path, root)
        if case.judgment:
            judged.append(BenchCase(case, hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            skipped.append(case.key)
    return judged, skipped


@dataclass(frozen=True)
class PreparedCase:
    bench: BenchCase
    build: Build
    prepared: service.Prepared


@dataclass(frozen=True)
class PreparedPartition:
    cases: list[PreparedCase]
    destination: str
    requires_shell_warning: bool
    pack_sha256: str
    fingerprint: str
    """La huella combinada: lo que `record --consent` debe repetir."""


def needs_recording(
    root: Path, settings: PreAuditSettings, pack_sha256: str
) -> Callable[[BenchCase, str], bool]:
    """El criterio de `--pending`: no hay grabación de este par, no se puede leer, está
    desligada del pack o del payload actuales (como en `validate --recordings`) o alguna
    repetición es `provider_error`. Una grabación ligada y toda `response` no se toca."""

    def pending(bench: BenchCase, payload_sha256: str) -> bool:
        try:
            recording = read_recording(root, bench.case.key, settings.provider, settings.model)
        except BenchError:
            return True
        return (
            recording.pack_sha256 != pack_sha256
            or recording.payload_sha256 != payload_sha256
            or any(r.outcome is RepetitionOutcome.PROVIDER_ERROR for r in recording.repetitions)
        )

    return pending


def prepare_cases(
    root: Path,
    cases: list[BenchCase],
    settings: PreAuditSettings,
    scratch: Path,
    *,
    builder: CaseBuilder | None = None,
    client_factory: service.ClientFactory | None = None,
    only: Callable[[BenchCase, str], bool] | None = None,
) -> PreparedPartition:
    """Construye cada caso en `scratch/<caso>` y lo prepara con `service.prepare`. Sin red.
    Con `only(caso, huella del payload)`, la partición preparada (y su huella combinada) solo
    cubre los casos para los que devuelve verdadero; puede quedar sin casos.
    `BenchError` si un caso no se construye; `PreAuditRejected` si no se puede preparar."""
    prepared_cases = []
    for bench in cases:
        build = (builder or build_case)(root, bench.case, scratch / bench.case.id)
        assert build.dossier is not None, "la construcción termina en la instantánea"
        prepared = service.prepare(
            build.store,
            build.dossier,
            None,
            settings,
            now=build.tick(),
            client_factory=client_factory,
        )
        prepared_cases.append(PreparedCase(bench, build, prepared))
    if not prepared_cases:
        raise service.PreAuditRejected(["la partición no tiene casos de juicio"])
    first = prepared_cases[0].prepared
    if any(c.prepared.destination != first.destination for c in prepared_cases):
        raise service.PreAuditRejected(["el destino ha cambiado durante la preparación"])
    pack_sha256 = load_pack().sha256
    if only is not None:
        prepared_cases = [c for c in prepared_cases if only(c.bench, c.prepared.payload.sha256)]
    pairs = [(c.bench.case.key, c.prepared.payload.sha256) for c in prepared_cases]
    return PreparedPartition(
        prepared_cases,
        first.destination,
        first.requires_shell_warning,
        pack_sha256,
        combined_fingerprint(settings, first.destination, pack_sha256, pairs),
    )


def combined_fingerprint(
    settings: PreAuditSettings,
    destination: str,
    pack_sha256: str,
    payloads: list[tuple[str, str]],
) -> str:
    """sha256 de lo que se consiente: destino, pack y la lista ordenada (caso, huella)."""
    consented = {
        "format": CONSENT_FORMAT,
        "provider": settings.provider.value,
        "model": settings.model,
        "destination": destination,
        "pack_sha256": pack_sha256,
        "payloads": [list(pair) for pair in sorted(payloads)],
    }
    return hashlib.sha256(canonical_json(consented)).hexdigest()


# --- grabar ----------------------------------------------------------------------------------


def record_case(
    prepared: PreparedCase,
    settings: PreAuditSettings,
    *,
    by: str,
    repetitions: int,
    accept_shell_risk: bool,
    recorder: Recorder,
    clock: Callable[[], datetime],
) -> Recording:
    """Envía el caso `repetitions` veces por `service.run`, con el consentimiento de su
    preparación, y devuelve la grabación (sin escribirla). `PreAuditRejected` si el producto no
    envía."""
    build, payload_sha256 = prepared.build, prepared.prepared.payload.sha256
    assert build.dossier is not None, "la construcción termina en la instantánea"
    sent = []
    for n in range(1, repetitions + 1):
        recorder.captured.clear()
        at = clock()
        service.run(
            build.store,
            build.dossier,
            None,
            payload_sha256,
            by,
            settings=settings,
            now=build.tick(),
            accept_shell_risk=accept_shell_risk,
            client_factory=recorder,
        )
        assert len(recorder.captured) == 1, "con consentimiento, `run` envía una vez"
        sent.append(_repetition(n, at, recorder.captured[0]))
    pack = load_pack()
    try:
        return Recording(
            format=RECORDING_FORMAT,
            case=prepared.bench.case.key,
            case_sha256=prepared.bench.sha256,
            provider=settings.provider,
            model=settings.model,
            destination=portable_destination(prepared.prepared.destination),
            pack_version=pack.version,
            pack_sha256=pack.sha256,
            payload_sha256=payload_sha256,
            tool_version=__version__,
            catalog_sha256=catalog_sha256(),
            recorded_at=clock(),
            recorded_by=by,
            repetitions=sent,
        )
    except ValidationError as exc:
        raise RecordingRefused(describe_validation(prepared.bench.case.key, exc)) from None


def _repetition(n: int, at: datetime, captured: Captured) -> Repetition:
    if captured.kind is not None:
        return Repetition(n=n, at=at, outcome=RepetitionOutcome.PROVIDER_ERROR, kind=captured.kind)
    assert captured.response is not None
    if parse_response(captured.response) is None:
        return Repetition(n=n, at=at, outcome=RepetitionOutcome.SCHEMA_ERROR)
    return Repetition(n=n, at=at, outcome=RepetitionOutcome.RESPONSE, response=captured.response)
