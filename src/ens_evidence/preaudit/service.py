"""Servicio de la pre-auditoría: preparar el envío y ejecutar (plan pre-audit, «Cambios r2»).

`prepare` construye el payload de un expediente íntegro y vigente, lo guarda en
`evidence/preaudits/<instante>-<sha12>/` junto con `prepared.json` (huella, destino, pack) y lo
devuelve para mostrarlo. No sale nada del equipo.

`run` ejecuta siempre la capa determinista. La de juicio solo se envía con consentimiento
(`consent_sha256`) que coincida con una preparación de ese expediente, y si la huella del payload
y el destino actuales son los mismos que se prepararon. El resultado es un registro de solo
adición en la cadena `evidence/preaudits/runs/` (`chain.py`). Orden de escritura, como en las
transiciones del expediente:

1. comprobaciones y envío: un rechazo no escribe nada;
2. reservar `runs/seq/<n>` con creación exclusiva;
3. escribir el registro.

Una ejecución nueva nunca sustituye a otra ni cierra lo que propuso, y una fallida o vacía queda
registrada como tal. La disposición humana de los hallazgos va aparte (T6b).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ens_evidence import chain
from ens_evidence.assessment.dossier import build_snapshot, rebuild_and_diff, snapshot_sha256
from ens_evidence.assessment.dossier.build import DossierRejected
from ens_evidence.assessment.dossier.derived import markdown_problems, view_problems
from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.assessment.dossier.package import has_package, verify_package
from ens_evidence.assessment.dossier.store import (
    DossierUnreadable,
    load_snapshot,
    stored_snapshot_sha256,
)
from ens_evidence.assessment.dossier.transitions import scan_transitions
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.models.enums import Category
from ens_evidence.models.preaudit import (
    CLI_PROVIDERS,
    Consent,
    Finding,
    LayerStatus,
    PreAuditRecord,
    PreAuditRun,
    Provider,
)
from ens_evidence.preaudit.adapter import (
    JudgmentConfig,
    JudgmentOutcome,
    JudgmentProvider,
    ProviderError,
    run_judgment,
)
from ens_evidence.preaudit.classify import group_findings
from ens_evidence.preaudit.clients import anthropic, openai
from ens_evidence.preaudit.clients.claude_code import ClaudeCodeProvider
from ens_evidence.preaudit.clients.codex import CodexProvider
from ens_evidence.preaudit.deterministic import deterministic_findings
from ens_evidence.preaudit.pack import AuditPack, PackError, load_pack
from ens_evidence.preaudit.payload import (
    Payload,
    PayloadError,
    build_payload,
    canonical_json,
    describe_destination,
)
from ens_evidence.preaudit.redact import RedactionLeak
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.workspace import AlreadyExists, NotFound, PreAuditSettings, WorkspaceStore

PREAUDITS_PREFIX = "evidence/preaudits"
RUNS_PREFIX = f"{PREAUDITS_PREFIX}/runs"
PREPARED_NAME = "prepared.json"
PAYLOAD_NAME = "payload.json"
PREPARED_FORMAT = "ens-preaudit-prepared/1"
RUNS_OWNER = "preaudits"
_PREPARED_KEY = re.compile(
    rf"{PREAUDITS_PREFIX}/\d{{8}}T\d{{12}}Z-[0-9a-f]{{12}}/{re.escape(PREPARED_NAME)}"
)
DEFAULT_BASE_URL = {
    Provider.ANTHROPIC: anthropic.DEFAULT_BASE_URL,
    Provider.OPENAI: openai.DEFAULT_BASE_URL,
}
SHELL_RISK = (
    "este destino conserva una herramienta de shell que no se puede quitar y podría leer ficheros "
    "de este equipo; para enviar hay que aceptarlo expresamente (`--accept-shell-risk`)"
)


class PreAuditRejected(Exception):
    """No se escribe ni se envía nada; `errors` dice por qué (rutas y tipos, nunca valores)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class PreAuditUnconfigured(Exception):
    """Falta configurar el proveedor o el modelo en los ajustes."""


def default_client(provider: Provider, config: PreAuditSettings) -> JudgmentProvider:
    """El cliente real del destino. Las claves las leen los clientes del entorno."""
    match provider:
        case Provider.ANTHROPIC:
            return anthropic.AnthropicProvider(config.base_url or DEFAULT_BASE_URL[provider])
        case Provider.OPENAI:
            return openai.OpenAIProvider(config.base_url or DEFAULT_BASE_URL[provider])
        case Provider.CLAUDE_CODE:
            return ClaudeCodeProvider()
        case Provider.CODEX:
            return CodexProvider()


# --- expediente --------------------------------------------------------------------------------


def dossier_problems(store: WorkspaceStore, dossier: str) -> list[str]:
    """Lo que comprueba `assessment dossier verify` (reproducción con las entradas congeladas,
    derivados, cadena de transiciones y paquete) y, además, la deriva respecto al estado actual.
    `DossierUnreadable` si ni siquiera se puede leer la instantánea."""
    snapshot = load_snapshot(store, dossier)
    problems = verification_problems(store, dossier, snapshot)
    drift = rebuild_and_diff(store, snapshot).drift
    problems += [
        f"deriva en {section}: {line}" for section, lines in drift.items() for line in lines
    ]
    return problems


def verification_problems(
    store: WorkspaceStore, dossier: str, snapshot: DossierSnapshot
) -> list[str]:
    """Los problemas que informa `assessment dossier verify`."""
    problems: list[str] = []
    try:
        rebuilt = build_snapshot(store, snapshot.inputs)
    except (DossierRejected, CategorizationNotCurrent) as exc:
        problems.append(f"no se puede reproducir con las entradas congeladas ({exc})")
    else:
        if snapshot_sha256(rebuilt) != stored_snapshot_sha256(store, dossier):
            problems.append("la reproducción con las entradas congeladas no da los mismos bytes")
    problems += markdown_problems(store, dossier, snapshot)
    scan = scan_transitions(store, dossier)
    problems += scan.problems
    if not scan.problems:
        problems += view_problems(store, dossier, snapshot, list(scan.entries))
    if has_package(store, dossier) or snapshot.category is not Category.BASICA:
        problems += verify_package(store, dossier).problems
    return problems


# --- preparar ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    key: str
    """Clave de `prepared.json`."""
    dossier: str
    payload: Payload
    provider: Provider
    model: str
    destination: str
    requires_shell_warning: bool
    pack_version: str


def prepare(
    store: WorkspaceStore,
    dossier: str,
    provider: Provider | None,
    settings: PreAuditSettings | None,
    *,
    now: datetime,
) -> Prepared:
    """Guarda el payload y lo que liga el consentimiento. No envía nada."""
    config, chosen = resolve_config(provider, settings)
    _require_sound(store, dossier)
    pack = _pack()
    payload = _payload(store, dossier, pack)
    destination, shell = _destination(chosen, config)
    directory = f"{PREAUDITS_PREFIX}/{chain.instant_stamp(now)}-{payload.sha256[:12]}"
    prepared = {
        "format": PREPARED_FORMAT,
        "prepared_at": now.isoformat(),
        "dossier": dossier,
        "dossier_sha256": stored_snapshot_sha256(store, dossier),
        "payload": f"{directory}/{PAYLOAD_NAME}",
        "payload_sha256": payload.sha256,
        "provider": chosen.value,
        "model": config.model,
        "destination": destination,
        "requires_shell_warning": shell,
        "pack_version": pack.version,
        "pack_sha256": pack.sha256,
    }
    key = f"{directory}/{PREPARED_NAME}"
    try:
        store.create_bytes(prepared["payload"], payload.canonical)
        store.create_bytes(key, canonical_json(prepared))
    except AlreadyExists:
        raise PreAuditRejected(["ya hay una preparación con ese instante y esa huella"]) from None
    return Prepared(key, dossier, payload, chosen, config.model, destination, shell, pack.version)


def resolve_config(
    provider: Provider | None, settings: PreAuditSettings | None
) -> tuple[PreAuditSettings, Provider]:
    """El destino configurado. `--provider` solo confirma el de los ajustes: el modelo y la URL
    base que hay en ellos son de ese proveedor."""
    if settings is None:
        raise PreAuditUnconfigured(
            "configura `preaudit` en los ajustes (proveedor y modelo; el modelo no tiene valor "
            "por defecto)"
        )
    if provider is not None and provider is not settings.provider:
        raise PreAuditUnconfigured(
            f"los ajustes configuran {settings.provider.value}, no {provider.value}: el modelo "
            "es de ese proveedor; cambia `preaudit` en los ajustes"
        )
    return settings, settings.provider


def _require_sound(store: WorkspaceStore, dossier: str) -> None:
    try:
        problems = dossier_problems(store, dossier)
    except DossierUnreadable as exc:
        raise PreAuditRejected([f"{dossier} no se puede leer", *exc.problems]) from None
    if problems:
        raise PreAuditRejected(
            [f"{dossier} no está íntegro o ya no describe el estado actual; no se envía", *problems]
        )


def _pack() -> AuditPack:
    try:
        return load_pack()
    except PackError as exc:
        raise PreAuditRejected([f"el audit pack no es íntegro: {exc}"]) from None


def _payload(store: WorkspaceStore, dossier: str, pack: AuditPack) -> Payload:
    try:
        return build_payload(store, dossier, pack)
    except (PayloadError, RedactionLeak) as exc:
        raise PreAuditRejected(["no se puede preparar el envío", *exc.problems]) from None


def _destination(provider: Provider, config: PreAuditSettings) -> tuple[str, bool]:
    """(destino descrito, aviso de shell). Para una API basta la URL: no hace falta la clave."""
    if provider in CLI_PROVIDERS:
        client = _client(provider, config)
        return client.destination, client.requires_shell_warning
    base_url = config.base_url or DEFAULT_BASE_URL[provider]
    try:
        return describe_destination(provider, base_url=base_url), False
    except ValueError as exc:
        raise PreAuditRejected([str(exc)]) from None


def _client(provider: Provider, config: PreAuditSettings) -> JudgmentProvider:
    try:
        return default_client(provider, config)
    except ProviderError as error:
        raise PreAuditRejected([error.message]) from None


def _client_destination(provider: Provider, client: JudgmentProvider) -> str:
    if provider in CLI_PROVIDERS:
        return client.destination
    return describe_destination(provider, base_url=client.destination)


# --- ejecutar ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Consented:
    """Lo que se envía tras comprobar el consentimiento."""

    prepared_key: str
    payload: Payload
    client: JudgmentProvider
    config: PreAuditSettings
    consent: Consent


@dataclass(frozen=True)
class RunScan:
    records: tuple[chain.Stored[PreAuditRecord], ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class RunResult:
    key: str
    record: PreAuditRecord


def run(
    store: WorkspaceStore,
    dossier: str,
    provider: Provider | None,
    consent_sha256: str | None,
    by: str | None,
    *,
    settings: PreAuditSettings | None,
    now: datetime,
    accept_shell_risk: bool = False,
) -> RunResult:
    """Ejecuta la pre-auditoría de `dossier`. Sin `consent_sha256` no hay red: solo la capa
    determinista. `PreAuditRejected` si no se puede registrar o si el consentimiento no liga."""
    try:
        snapshot = load_snapshot(store, dossier)
    except DossierUnreadable as exc:
        raise PreAuditRejected([f"{dossier} no se puede leer", *exc.problems]) from None
    scan = scan_runs(store, now=now)
    if scan.problems:
        raise PreAuditRejected(
            ["el registro de pre-auditorías está roto; no admite más ejecuciones", *scan.problems]
        )
    integrity = dossier_problems(store, dossier)
    pack = _pack()
    consented = None
    if consent_sha256 is not None:
        consented = _consented(
            store,
            dossier,
            pack,
            provider,
            consent_sha256,
            by,
            settings=settings,
            now=now,
            integrity=integrity,
            accept_shell_risk=accept_shell_risk,
        )
    findings = deterministic_findings(snapshot, integrity=integrity)
    outcome = JudgmentOutcome(LayerStatus.NOT_RUN)
    if consented is not None:
        judgment = JudgmentConfig(model=consented.config.model, timeout=consented.config.timeout)
        outcome = run_judgment(consented.client, pack, consented.payload.canonical, judgment)
        findings += [f for f in outcome.findings if f.id not in {g.id for g in findings}]
    record = _record(store, dossier, pack, scan, consented, outcome, findings, now)
    return RunResult(_write(store, record), record)


def _consented(
    store: WorkspaceStore,
    dossier: str,
    pack: AuditPack,
    provider: Provider | None,
    consent_sha256: str,
    by: str | None,
    *,
    settings: PreAuditSettings | None,
    now: datetime,
    integrity: list[str],
    accept_shell_risk: bool,
) -> Consented:
    """Todo lo que liga el consentimiento, comprobado antes de abrir ninguna conexión."""
    if by is None:
        raise PreAuditRejected(["--by: indica quién consiente el envío"])
    if errors := check_declared_name(by):
        raise PreAuditRejected(errors)
    config, chosen = resolve_config(provider, settings)
    if integrity:
        raise PreAuditRejected(
            [
                f"{dossier} no está íntegro o ya no describe el estado actual; no se envía",
                *integrity,
            ]
        )
    key, prepared = _find_prepared(store, dossier, consent_sha256)
    if prepared["pack_sha256"] != pack.sha256:
        raise PreAuditRejected(
            ["el audit pack ha cambiado desde la preparación; vuelve a preparar"]
        )
    if prepared["provider"] != chosen.value or prepared["model"] != config.model:
        raise PreAuditRejected(
            ["el proveedor o el modelo han cambiado desde la preparación; vuelve a preparar"]
        )
    payload = _payload(store, dossier, pack)
    if (
        payload.sha256 != consent_sha256
        or _stored_payload_sha256(store, prepared) != consent_sha256
    ):
        raise PreAuditRejected(
            ["lo que se enviaría ha cambiado desde la preparación; vuelve a preparar y a consentir"]
        )
    client = _client(chosen, config)
    destination = _client_destination(chosen, client)
    if destination != prepared["destination"]:
        raise PreAuditRejected(
            ["el destino ha cambiado desde la preparación; vuelve a preparar y a consentir"]
        )
    if client.requires_shell_warning and not accept_shell_risk:
        raise PreAuditRejected([SHELL_RISK])
    consent = Consent(
        by=by,
        at=now,
        payload_sha256=consent_sha256,
        destination=destination,
        shell_risk_accepted=client.requires_shell_warning,
    )
    return Consented(key, payload, client, config, consent)


def _find_prepared(store: WorkspaceStore, dossier: str, sha256: str) -> tuple[str, dict]:
    """La preparación más reciente de `dossier` con esa huella."""
    found = []
    for key in store.list(PREAUDITS_PREFIX):
        if not _PREPARED_KEY.fullmatch(key) or key.split("/")[-2][-12:] != sha256[:12]:
            continue
        try:
            prepared = json.loads(store.read_bytes(key))
        except (ValueError, NotFound):
            continue
        if (
            isinstance(prepared, dict)
            and prepared.get("format") == PREPARED_FORMAT
            and prepared.get("dossier") == dossier
            and prepared.get("payload_sha256") == sha256
        ):
            found.append((key, prepared))
    if not found:
        raise PreAuditRejected(
            [f"no hay ninguna preparación de {dossier} con esa huella; ejecuta `preaudit prepare`"]
        )
    return max(found, key=lambda item: item[0])


def _stored_payload_sha256(store: WorkspaceStore, prepared: Mapping) -> str | None:
    try:
        return hashlib.sha256(store.read_bytes(prepared["payload"])).hexdigest()
    except (NotFound, KeyError, TypeError):
        return None


def _record(
    store: WorkspaceStore,
    dossier: str,
    pack: AuditPack,
    scan: RunScan,
    consented: Consented | None,
    outcome: JudgmentOutcome,
    findings: list[Finding],
    now: datetime,
) -> PreAuditRecord:
    judged = consented is not None
    run_model = PreAuditRun(
        dossier=dossier,
        dossier_sha256=stored_snapshot_sha256(store, dossier),
        pack_version=pack.version,
        pack_sha256=pack.sha256,
        at=now,
        provider=consented.config.provider if judged else None,
        model=consented.config.model if judged else None,
        consent=consented.consent if judged else None,
        judgment_status=outcome.status,
        judgment_error=outcome.error,
        discarded=outcome.discarded,
    )
    last = scan.records[-1] if scan.records else None
    return PreAuditRecord(
        seq=(last.record.seq if last else 0) + 1,
        previous_sha256=last.sha256 if last else None,
        recorded_at=now,
        prepared=consented.prepared_key if judged else None,
        run=run_model,
        findings=findings,
        groupings=group_findings(findings),
    )


def _write(store: WorkspaceStore, record: PreAuditRecord) -> str:
    data = chain.render_record(record.model_dump(mode="json"))
    try:
        chain.reserve_sequence(store, RUNS_PREFIX, record.seq)
    except chain.SequenceTaken:
        raise PreAuditRejected(
            [f"otra orden reservó antes la ejecución {record.seq}; revisa y reintenta"]
        ) from None
    key = chain.record_key(RUNS_PREFIX, record.seq, record.recorded_at, data)
    store.create_bytes(key, data)
    return key


# --- leer --------------------------------------------------------------------------------------


def scan_runs(store: WorkspaceStore, *, now: datetime | None = None) -> RunScan:
    """La cadena de ejecuciones verificada: reservas y registros uno a uno, sin huecos ni
    bifurcaciones, enlazados y con la clave que corresponde a su contenido. Una fecha futura
    respecto a `now` o un `recorded_at` que retrocede también son problemas."""
    stored, reserved, problems = chain.read_chain(
        store,
        RUNS_PREFIX,
        PreAuditRecord,
        owner=RUNS_OWNER,
        owner_of=lambda _record: RUNS_OWNER,
        owner_label="registro",
        chain_label="las pre-auditorías",
    )
    by_seq = chain.group_by_seq(stored)
    pairs = chain.linked_pairs(stored, by_seq)
    problems += chain.sequence_problems(by_seq, reserved)
    problems += chain.link_problems(pairs)
    problems += [
        f"registros {a.record.seq} y {b.record.seq}: recorded_at retrocede"
        for a, b in pairs
        if b.record.recorded_at < a.record.recorded_at
    ]
    if now is not None:
        problems += [
            f"{item.key}: recorded_at está en el futuro"
            for item in stored
            if item.record.recorded_at > now
        ]
    return RunScan(tuple(stored), tuple(problems))


def show(store: WorkspaceStore, seq: int) -> chain.Stored[PreAuditRecord]:
    scan = scan_runs(store)
    if scan.problems:
        raise PreAuditRejected(["el registro de pre-auditorías está roto", *scan.problems])
    found = next((item for item in scan.records if item.record.seq == seq), None)
    if found is None:
        raise PreAuditRejected([f"no hay ninguna ejecución {seq}"])
    return found
