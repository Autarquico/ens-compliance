"""Lectura y validación de las entradas del motor de checks: configuración y evidencia.

Funciones puras de lectura sobre el workspace: sin red y sin instanciar conectores. Cuando algo no
se puede leer o no cuadra, el resultado lo dice con un código estable; nunca se retrocede a una
evidencia anterior, porque no se sabría si era la última.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ValidationError

from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.connectors.config import (
    ConnectorConfigError,
    connector_config_key,
    load_connector_config,
)
from ens_evidence.endpoint.config import CONFIG_NAME as ENDPOINT
from ens_evidence.endpoint.evidence import HostEvidence, latest_by_host, load_host_evidence
from ens_evidence.endpoint.normalize import SOURCE as ENDPOINT_SOURCE
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import sha256_hex
from ens_evidence.matrix.evidence import manifest_sha256, verified_manifest
from ens_evidence.models.contracts import CollectionResult, EvidenceManifest, ProvenanceKind
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

EVIDENCE_UNREADABLE = "evidence_unreadable"
NO_EVIDENCE = "no_evidence"
EVIDENCE_UNVERIFIED = "evidence_unverified"
EVIDENCE_INCONSISTENT = "evidence_inconsistent"
MISSING_RUN = "check_run_missing"
CHECK_EVIDENCE_INCONSISTENT = "check_evidence_inconsistent"
CHECK_EVIDENCE_UNVERIFIED = "check_evidence_unverified"
_NOT_A_RESULT = "el artefacto no es un CollectionResult válido"


@dataclass(frozen=True)
class InputSelection:
    """La última evidencia de collector de un conector, o el código de por qué no hay ninguna."""

    error: str | None = None
    evidence_id: str | None = None
    collected_at: datetime | None = None
    result: CollectionResult | None = None
    manifest_sha256: str | None = None
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.error is None


def config_asset_id(config: BaseModel) -> str:
    """El activo al que se refiere la configuración de un conector con evidencia de collector."""
    return str(config.model_dump()["asset_id"])


@dataclass(frozen=True)
class _Entry:
    """Un manifiesto de collector leído contra su artefacto. `error` None: coherente. `affected`:
    los conectores a los que podría pertenecer (None: no se sabe, todos)."""

    manifest: EvidenceManifest
    result: CollectionResult | None = None
    error: str | None = None
    affected: frozenset[str] | None = None
    problems: tuple[str, ...] = ()


def _read_entry(store: WorkspaceStore, manifest: EvidenceManifest) -> _Entry:
    verified = verified_manifest(store, manifest.id)
    if verified is None:
        return _Entry(manifest, error=EVIDENCE_UNVERIFIED)
    result = _read_result(store, verified)
    if isinstance(result, str):
        return _Entry(manifest, error=EVIDENCE_UNVERIFIED, problems=(result,))
    if result is None:
        return _Entry(manifest, error=EVIDENCE_INCONSISTENT, problems=(_NOT_A_RESULT,))
    mismatches = _identity_mismatches(verified, result)
    if not mismatches:
        return _Entry(manifest, result)
    declared = {manifest.collector.name} if manifest.collector else set()
    return _Entry(
        manifest,
        error=EVIDENCE_INCONSISTENT,
        affected=frozenset({result.collector, *declared}),
        problems=tuple(mismatches),
    )


def select_collector_input(
    store: WorkspaceStore, connector: str, asset_id: str, at: datetime | None = None
) -> InputSelection:
    """La última evidencia del conector para el activo; con `at`, la recogida hasta entonces.

    La identidad sale del artefacto, que está verificado por hash, y no de los campos del
    manifiesto, que se pueden editar. Si algún manifiesto de collector no cuadra con su artefacto
    o su artefacto no verifica, el conector al que pudiera pertenecer se bloquea: retocar el
    manifiesto de la última evidencia no puede hacer elegir una anterior.
    """
    listing = list_manifests(store)
    if listing.errors:
        return InputSelection(EVIDENCE_UNREADABLE, problems=tuple(listing.errors))
    entries = [
        _read_entry(store, m)
        for m in listing.manifests
        if m.provenance.kind is ProvenanceKind.COLLECTOR
    ]
    blocking = sorted(
        (e for e in entries if e.error and (e.affected is None or connector in e.affected)),
        key=lambda e: (e.error != EVIDENCE_UNVERIFIED, e.manifest.id),
    )
    if blocking:
        first = blocking[0]
        problems = tuple(f"{e.manifest.id}: {p}" for e in blocking for p in e.problems)
        return InputSelection(first.error, evidence_id=first.manifest.id, problems=problems)
    candidates = [
        e
        for e in entries
        if e.result is not None
        and e.result.collector == connector
        and e.result.asset_id == asset_id
        and (at is None or e.result.collected_at <= at)
    ]
    if not candidates:
        return InputSelection(NO_EVIDENCE)
    latest = max(
        candidates,
        key=lambda e: (e.result.collected_at, e.manifest.provenance.submitted_at, e.manifest.id),
    )
    return InputSelection(
        evidence_id=latest.manifest.id,
        collected_at=latest.result.collected_at,
        result=latest.result,
        manifest_sha256=manifest_sha256(latest.manifest),
    )


def _read_result(
    store: WorkspaceStore, manifest: EvidenceManifest
) -> CollectionResult | str | None:
    """El resultado; un texto si el artefacto no se puede leer o no tiene el hash del manifiesto;
    None si no es un `CollectionResult` válido."""
    try:
        raw = store.read_bytes(manifest.artifact.key)
    except WorkspaceError as exc:
        return f"artefacto ilegible ({type(exc).__name__})"
    if sha256_hex(raw) != manifest.artifact.sha256:
        return "el artefacto no coincide con su sha256"
    try:
        return CollectionResult.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return None


def _identity_mismatches(manifest: EvidenceManifest, result: CollectionResult | None) -> list[str]:
    if result is None:
        return [_NOT_A_RESULT]
    collector = manifest.collector.name if manifest.collector else None
    pairs = {
        "source": (manifest.source, result.source),
        "collector": (collector, result.collector),
        "asset_id": (manifest.asset_id, result.asset_id),
        "collected_at": (manifest.collected_at, result.collected_at),
    }
    return [name for name, (declared, actual) in pairs.items() if declared != actual]


@dataclass(frozen=True)
class EndpointInput:
    evidence: HostEvidence
    manifest_sha256: str


@dataclass(frozen=True)
class EndpointInputs:
    """Evidencia de endpoints por su cargador: válida, incoherente con su manifiesto o ilegible.

    Con `unreadable` no vacío no se sabe cuál es la última ejecución de cada host.
    """

    valid: list[EndpointInput] = field(default_factory=list)
    inconsistent: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def load_endpoint_inputs(store: WorkspaceStore) -> EndpointInputs:
    """Usa `load_host_evidence`: sin exigir procedencia COLLECTOR (el paquete es manual)."""
    loaded = load_host_evidence(store)
    manifests = {m.id: m for m in list_manifests(store).manifests}
    valid: list[EndpointInput] = []
    inconsistent: list[str] = [
        f"{EVIDENCE_INCONSISTENT}:{m.id}: source" for m in _disguised_endpoint_packages(store)
    ]
    for item in loaded.items:
        manifest = manifests.get(item.evidence_id)
        mismatches = (
            ["manifiesto"] if manifest is None else _identity_mismatches(manifest, item.result)
        )
        if mismatches:
            inconsistent.append(
                f"{EVIDENCE_INCONSISTENT}:{item.evidence_id}: {', '.join(mismatches)}"
            )
        else:
            assert manifest is not None
            valid.append(EndpointInput(item, manifest_sha256(manifest)))
    return EndpointInputs(valid, inconsistent, list(loaded.unreadable))


def _disguised_endpoint_packages(store: WorkspaceStore) -> list[EvidenceManifest]:
    """Manifiestos cuyo artefacto, íntegro, es un resultado de endpoints pero cuyo `source` dice
    otra cosa: el cargador los salta por el `source`, que se puede editar."""
    found = []
    for manifest in list_manifests(store).manifests:
        if manifest.source == ENDPOINT_SOURCE:
            continue
        result = _read_result(store, manifest)
        if isinstance(result, CollectionResult) and result.source == ENDPOINT_SOURCE:
            found.append(manifest)
    return found


@dataclass(frozen=True)
class EngineConfig[M: BaseModel]:
    """La configuración del conector o el error de config; `sha256` es el del fichero, si se pudo
    leer, aunque no sea una configuración válida."""

    config: M | None = None
    sha256: str | None = None
    error: str | None = None


def load_engine_config[M: BaseModel](
    store: WorkspaceStore, connector: str, model: type[M]
) -> EngineConfig[M]:
    sha256 = _file_sha256(store, connector_config_key(connector))
    try:
        config = load_connector_config(store, connector, model)
    except (ConnectorConfigError, WorkspaceError) as exc:
        return EngineConfig(sha256=sha256, error=str(exc))
    return EngineConfig(config, sha256)


def _file_sha256(store: WorkspaceStore, key: str) -> str | None:
    try:
        return sha256_hex(store.read_bytes(key))
    except WorkspaceError:
        return None


def evidence_after_run(
    store: WorkspaceStore,
    connector: str,
    run: StoredCheckRun,
    at: datetime,
    asset_id: str | None = None,
) -> str | None:
    """El motivo por el que la evidencia de hoy no es la que vio el intento `run`, o None.

    `check_run_missing`: hay evidencia recogida hasta `at` que el motor elegiría hoy y que el
    intento no cita (o es posterior a su fin). `evidence_unreadable`: no se puede listar o leer la
    evidencia y no se sabe si hay una recogida posterior; ante la duda se bloquea.
    `check_evidence_inconsistent` y `check_evidence_unverified`: algún manifiesto no cuadra con su
    artefacto o este no verifica, así que no se sabe cuál es la última; tampoco se retrocede.
    Reutiliza la selección del motor; `asset_id` es el del conector con collector (el endpoint no
    lo necesita).
    """
    cited = {i.evidence_id for i in run.inputs}
    if connector == ENDPOINT:
        loaded = load_endpoint_inputs(store)
        if loaded.unreadable:
            return EVIDENCE_UNREADABLE
        if loaded.inconsistent:
            return CHECK_EVIDENCE_INCONSISTENT
        seen = [i.evidence for i in loaded.valid if i.evidence.collected_at <= at]
        newer = any(e.evidence_id not in cited for e in latest_by_host(seen).values())
        return MISSING_RUN if newer else None
    assert asset_id is not None
    selection = select_collector_input(store, connector, asset_id, at)
    blocked = {
        EVIDENCE_UNREADABLE: EVIDENCE_UNREADABLE,
        EVIDENCE_INCONSISTENT: CHECK_EVIDENCE_INCONSISTENT,
        EVIDENCE_UNVERIFIED: CHECK_EVIDENCE_UNVERIFIED,
    }
    if selection.error in blocked:
        return blocked[selection.error]
    if selection.evidence_id is None:
        return None
    return MISSING_RUN if selection.evidence_id not in cited else None
