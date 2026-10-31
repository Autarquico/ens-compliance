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


def select_collector_input(
    store: WorkspaceStore, connector: str, asset_id: str, at: datetime | None = None
) -> InputSelection:
    """La última evidencia del conector para el activo; con `at`, la recogida hasta entonces."""
    listing = list_manifests(store)
    if listing.errors:
        return InputSelection(EVIDENCE_UNREADABLE, problems=tuple(listing.errors))
    candidates = [
        manifest
        for manifest in listing.manifests
        if manifest.provenance.kind is ProvenanceKind.COLLECTOR
        and manifest.collector is not None
        and manifest.collector.name == connector
        and manifest.asset_id == asset_id
        and (at is None or manifest.collected_at <= at)
    ]
    if not candidates:
        return InputSelection(NO_EVIDENCE)
    latest = max(candidates, key=lambda m: (m.collected_at, m.provenance.submitted_at, m.id))
    verified = verified_manifest(store, latest.id)
    if verified is None:
        return InputSelection(
            EVIDENCE_UNVERIFIED, evidence_id=latest.id, collected_at=latest.collected_at
        )
    result = _read_result(store, verified)
    if isinstance(result, str):
        return InputSelection(
            EVIDENCE_UNVERIFIED,
            evidence_id=latest.id,
            collected_at=latest.collected_at,
            problems=(result,),
        )
    mismatches = _identity_mismatches(verified, result)
    if result is not None and result.collector != connector:
        mismatches.append("collector")
    if result is None or mismatches:
        return InputSelection(
            EVIDENCE_INCONSISTENT,
            evidence_id=latest.id,
            collected_at=latest.collected_at,
            problems=tuple(mismatches),
        )
    return InputSelection(
        evidence_id=latest.id,
        collected_at=latest.collected_at,
        result=result,
        manifest_sha256=manifest_sha256(verified),
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
        return ["el artefacto no es un CollectionResult válido"]
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
    inconsistent: list[str] = []
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
    evidencia y no se sabe si hay una recogida posterior; ante la duda se bloquea. Reutiliza la
    selección del motor; `asset_id` es el del conector con collector (el endpoint no lo necesita).
    """
    cited = {i.evidence_id for i in run.inputs}
    if connector == ENDPOINT:
        loaded = load_endpoint_inputs(store)
        if loaded.unreadable:
            return EVIDENCE_UNREADABLE
        seen = [i.evidence for i in loaded.valid if i.evidence.collected_at <= at]
        if loaded.inconsistent:
            newer = any(e.collected_at > run.finished_at for e in seen)
        else:
            newer = any(e.evidence_id not in cited for e in latest_by_host(seen).values())
        return MISSING_RUN if newer else None
    assert asset_id is not None
    selection = select_collector_input(store, connector, asset_id, at)
    if selection.error == EVIDENCE_UNREADABLE:
        return EVIDENCE_UNREADABLE
    if selection.evidence_id is None or selection.collected_at is None:
        return None
    newer = (
        selection.evidence_id not in cited
        if selection.ok
        else selection.collected_at > run.finished_at
    )
    return MISSING_RUN if newer else None
