"""Checks HOST-001…009 sobre la evidencia de endpoints (atestación manual).

Funciones puras. `PASS` solo con evidencia positiva y vigente de la última observación del
host; tabla vacía, ambigua o sin privilegios es `UNKNOWN`; el resto de estados de recogida
se traducen según el ADR 0002. Las conclusiones del script son "según atestación manual del
host" (los hashes del paquete detectan corrupción, no falsificación) y las del agente "según
el agente osquery". Por check y host manda la fuente (script o agente) con la observación más
reciente de las consultas que ese check lee, aunque sea un FAIL; si esa fuente caducó no se
vuelve a la otra. HOST-002 (último contacto del agente) es independiente de la vigencia de
los demás checks. `CheckResult.subject` es el activo evaluado.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ens_evidence.checks.catalog import CHECKS, ENDPOINT_CONNECTOR, SubjectKind
from ens_evidence.connectors.sections import worst
from ens_evidence.endpoint.agent_view import (
    WINDOWS,
    Admission,
    AgentView,
    QuerySnapshot,
    build_views,
)
from ens_evidence.endpoint.config import (
    EndpointConfig,
    HostEnrollment,
    SupportedOs,
    parse_numeric,
    release_of,
)
from ens_evidence.endpoint.evidence import HostEvidence, latest_by_host
from ens_evidence.endpoint.importer import ENDPOINT_ASSET_TYPES
from ens_evidence.endpoint.package import CLOCK_SKEW
from ens_evidence.models.contracts import CheckResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus
from ens_evidence.scope.models import Asset, AssetType

COVERAGE = "HOST-001"
AGENT = "HOST-002"
OS_SUPPORTED = "HOST-003"
DISK_ENCRYPTION = "HOST-004"
FIREWALL = "HOST-005"
AUTO_UPDATES = "HOST-006"
SOFTWARE_INVENTORY = "HOST-007"
SIGNATURES = "HOST-008"
REALTIME = "HOST-009"
HOST_CHECKS = (
    COVERAGE,
    AGENT,
    OS_SUPPORTED,
    DISK_ENCRYPTION,
    FIREWALL,
    AUTO_UPDATES,
    SOFTWARE_INVENTORY,
    SIGNATURES,
    REALTIME,
)
WORKSTATION_ONLY_CHECKS = frozenset(
    check_id
    for check_id, spec in CHECKS.items()
    if spec.connector == ENDPOINT_CONNECTOR and spec.subjects is SubjectKind.WORKSTATION
)

ATTESTED = "Según atestación manual del host"
AGENT_SAYS = "Según el agente osquery"
APPS_COVERAGE = "`apps` solo cubre aplicaciones conocidas de macOS, no todo el software"
ALF_ACTIVE = frozenset({"1", "2"})
ALF_MIN_OSQUERY_FOR_MACOS_15 = (5, 14, 1)
AUTO_INSTALL_KEY = "AutomaticallyInstallMacOSUpdates"
DEB_INSTALLED = "install ok installed"
POLICY_FLAGS = {"1": True, "true": True, "0": False, "false": False}
FILEVAULT_FLAGS = {"on": True, "off": False}


@dataclass(frozen=True)
class Outcome:
    status: CheckStatus
    reason: str
    collection: CollectionStatus = CollectionStatus.SUCCESS
    code: str | None = None
    cite: bool = True
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Host:
    """Lo que un check lee de una fuente; `moments` da la fecha de cada consulta del agente."""

    platform: str
    queries: dict[str, Any]
    collected_at: datetime
    moments: Mapping[str, datetime] = field(default_factory=dict)

    def collected(self, query_id: str) -> datetime:
        """Cuándo se observó la consulta: su fecha si el agente la dio; si no, la de la recogida."""
        return self.moments.get(query_id, self.collected_at)

    def rows(self, query_id: str) -> list[dict[str, Any]]:
        return [
            row for row in _list(_dict(self.queries.get(query_id)).get("rows")) if _is_dict(row)
        ]

    def status(self, query_id: str) -> CollectionStatus:
        query = self.queries.get(query_id)
        if not _is_dict(query):
            return CollectionStatus.ERROR
        return _status(query.get("status"))


@dataclass(frozen=True)
class CheckContext:
    config: EndpointConfig
    evaluated_at: datetime


def evaluate_host_checks(
    assets: Iterable[Asset],
    evidence: Iterable[HostEvidence],
    config: EndpointConfig,
    evaluated_at: datetime,
    unreadable: Sequence[str] = (),
    *,
    agent_evidence: Iterable[HostEvidence] = (),
    admissions: Mapping[str, Admission] | None = None,
) -> list[CheckResult]:
    """HOST-001…009 de cada laptop/server del alcance, sobre su última ejecución.

    HOST-009 (protección en tiempo real) solo se emite para laptops, en todos los caminos.

    Con evidencia `unreadable` no se sabe cuál es la última ejecución de ningún host (un
    manifiesto ilegible no dice de qué activo es): todos dan UNKNOWN, nunca la anterior.

    `agent_evidence` es lo leído del agente (`LoadedEvidence.agent`); `admissions`, las altas no
    revocadas del registro por activo. Sin ellos solo cuenta el script y HOST-002 es UNKNOWN.
    """
    latest = latest_by_host(evidence)
    views = build_views(agent_evidence, config.hosts)
    admissions = admissions or {}
    context = CheckContext(config, evaluated_at)
    results: list[CheckResult] = []
    for asset in assets:
        if asset.type not in ENDPOINT_ASSET_TYPES:
            continue
        enrollment = config.hosts.get(asset.id)
        item = latest.get(asset.id) if enrollment else None
        outcomes = (
            _all_unknown(_unreadable_reason(unreadable))
            if unreadable
            else _outcomes(item, enrollment, context, views.get(asset.id), admissions.get(asset.id))
        )
        results += [
            _result(check_id, asset.id, outcomes[check_id], evaluated_at)
            for check_id in HOST_CHECKS
            if asset.type is AssetType.LAPTOP or check_id not in WORKSTATION_ONLY_CHECKS
        ]
    return results


def _outcomes(
    item: HostEvidence | None,
    enrollment: HostEnrollment | None,
    context: CheckContext,
    view: AgentView | None = None,
    admission: Admission | None = None,
) -> dict[str, Outcome]:
    if enrollment is None:
        return _without_evidence(
            "not_enrolled", "el equipo no está inscrito en `hosts` de connectors/endpoint.yaml"
        )
    script = item if item is not None and _same_host(item, enrollment) else None
    agent = view if view is not None and view.snapshots else None
    if script is None and agent is None:
        outcomes = _without_evidence(*_no_sources(item))
    else:
        outcomes = _from_sources(script, agent, context)
    return outcomes | {AGENT: _agent_contact(view, admission, context)}


def _no_sources(item: HostEvidence | None) -> tuple[str, str]:
    if item is not None:
        return (
            "enrollment_changed",
            "evidencia de otro equipo: no corresponde al uuid y nombre inscritos ahora",
        )
    return (
        "no_evidence",
        "falta la evidencia del script guiado (`endpoint script`) y los resultados programados "
        "del agente osquery de este equipo",
    )


def _from_sources(
    script: HostEvidence | None, agent: AgentView | None, context: CheckContext
) -> dict[str, Outcome]:
    outcomes = {COVERAGE: _coverage(script, agent, context)}
    for check_id in HOST_CHECKS[2:]:
        outcomes[check_id] = _by_freshest_source(check_id, script, agent, context)
    return outcomes


def _coverage(
    script: HostEvidence | None, agent: AgentView | None, context: CheckContext
) -> Outcome:
    script_problem = _freshness(script, context) if script is not None else None
    if script is not None and script_problem is not None:
        script_problem = replace(script_problem, evidence_ids=(script.evidence_id,))
    agent_problem = _agent_freshness(agent.snapshots.values(), context) if agent else None
    used: list[tuple[str, str, datetime, tuple[str, ...]]] = []
    if script is not None and script_problem is None:
        used.append(("script", ATTESTED, script.collected_at, (script.evidence_id,)))
    if agent is not None and agent_problem is None:
        latest = max(snapshot.collected_at for snapshot in agent.snapshots.values())
        used.append(("agente", AGENT_SAYS, latest, _ids(agent.snapshots.values())))
    if not used:
        problem = script_problem or agent_problem
        assert problem is not None
        return problem
    ids = tuple(sorted({i for *_, group in used for i in group}))
    if len(used) == 1:
        _, prefix, moment, _ = used[0]
        reason = f"{prefix}: hay evidencia vigente del {moment:%Y-%m-%d}"
    else:
        detail = ", ".join(f"{who} del {moment:%Y-%m-%d}" for who, _, moment, _ in used)
        reason = f"{ATTESTED} y del agente osquery: hay evidencia vigente ({detail})"
    return Outcome(CheckStatus.PASS, reason, evidence_ids=ids)


def _by_freshest_source(
    check_id: str, script: HostEvidence | None, agent: AgentView | None, context: CheckContext
) -> Outcome:
    seen = _agent_observed(check_id, agent)
    if script is not None and (seen is None or seen <= script.collected_at):
        return _from_script(check_id, script, context)
    assert agent is not None
    return _from_agent(check_id, agent, context)


def _agent_observed(check_id: str, agent: AgentView | None) -> datetime | None:
    """La observación más reciente del agente de las consultas de este check, si hay alguna."""
    if agent is None:
        return None
    moments = [
        agent.snapshots[query].collected_at
        for query in CHECK_QUERIES[check_id]
        if query in agent.snapshots
    ]
    return max(moments, default=None)


def _from_script(check_id: str, script: HostEvidence, context: CheckContext) -> Outcome:
    if (stale := _freshness(script, context)) is not None:
        return replace(stale, evidence_ids=(script.evidence_id,))
    host = Host(
        str(_dict(script.result.data.get("package")).get("platform")),
        _dict(script.result.data.get("queries")),
        script.collected_at,
    )
    return replace(RUNNERS[check_id](host, context), evidence_ids=(script.evidence_id,))


def _from_agent(check_id: str, agent: AgentView, context: CheckContext) -> Outcome:
    platform = agent.platform
    if platform == WINDOWS:
        return _agent_outcome(
            _not_supported("checks de Windows pendientes: este check aún no se evalúa en Windows")
        )
    if platform is None:
        return _agent_outcome(_unknown("el agente no informa la plataforma del equipo"))
    groups = _needed_queries(check_id, platform)
    if any(not any(query in agent.snapshots for query in group) for group in groups):
        missing = ", ".join(
            " o ".join(f"`{query}`" for query in group)
            for group in groups
            if not any(query in agent.snapshots for query in group)
        )
        return _agent_outcome(_unknown(f"el agente aún no ha enviado {missing}"))
    used = {
        query: agent.snapshots[query]
        for group in groups
        for query in group
        if query in agent.snapshots
    }
    if (stale := _agent_freshness(used.values(), context)) is not None:
        return replace(stale, evidence_ids=_ids(used.values()))
    host = Host(
        platform,
        {query: snapshot.data for query, snapshot in used.items()},
        max(snapshot.collected_at for snapshot in agent.snapshots.values()),
        {query: snapshot.collected_at for query, snapshot in used.items()},
    )
    outcome = RUNNERS[check_id](host, context)
    return _agent_outcome(replace(outcome, evidence_ids=_ids(used.values())))


def _agent_outcome(outcome: Outcome) -> Outcome:
    reason = outcome.reason.removeprefix(f"{ATTESTED}: ")
    return replace(outcome, reason=f"{AGENT_SAYS}: {reason}")


def _ids(snapshots: Iterable[QuerySnapshot]) -> tuple[str, ...]:
    return tuple(sorted({snapshot.evidence_id for snapshot in snapshots}))


def _agent_freshness(snapshots: Iterable[QuerySnapshot], context: CheckContext) -> Outcome | None:
    """UNKNOWN si una consulta usada tiene fecha futura o caducó (con `>=`, como el script)."""
    used = list(snapshots)
    evaluated_at = context.evaluated_at
    for snapshot in used:
        if snapshot.collected_at > evaluated_at + CLOCK_SKEW:
            return _agent_outcome(
                Outcome(
                    CheckStatus.UNKNOWN,
                    f"{ATTESTED}: la consulta `{snapshot.query_id}` tiene fecha futura "
                    f"({snapshot.collected_at.isoformat()})",
                    code="evidence_in_future",
                    evidence_ids=(snapshot.evidence_id,),
                )
            )
    for snapshot in used:
        expires = snapshot.expires_at(context.config.max_evidence_age_days)
        if evaluated_at >= expires:
            return _agent_outcome(
                Outcome(
                    CheckStatus.UNKNOWN,
                    f"{ATTESTED}: la última consulta `{snapshot.query_id}` caducó el "
                    f"{expires.isoformat()}",
                    code="evidence_expired",
                    evidence_ids=(snapshot.evidence_id,),
                )
            )
    return None


# HOST-002 ---------------------------------------------------------------------------------


def _agent_contact(
    view: AgentView | None, admission: Admission | None, context: CheckContext
) -> Outcome:
    """Último contacto autenticado del agente, con evidencia citable; independiente de la vigencia
    de los demás checks. Sin alta (o revocada) no se demuestra ausencia de agente: UNKNOWN."""
    if admission is None:
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{AGENT_SAYS}: el equipo no tiene un agente dado de alta (o su alta está revocada)",
            code="no_agent",
            cite=False,
        )
    evaluated_at = context.evaluated_at
    limit = timedelta(hours=context.config.max_agent_silence_hours)
    contacts = [c for c in (view.contacts if view else []) if c.generation == admission.generation]
    if not contacts:
        if admission.admitted_at is not None and evaluated_at - admission.admitted_at > limit:
            return Outcome(
                CheckStatus.FAIL,
                f"{AGENT_SAYS}: agente sin contacto desde el alta del "
                f"{_moment(admission.admitted_at)} "
                f"(umbral de {context.config.max_agent_silence_hours} h)",
                code="agent_silent",
                cite=False,
            )
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{AGENT_SAYS}: el agente dado de alta aún no ha contactado",
            code="no_contact",
            cite=False,
        )
    last = max(contacts, key=lambda contact: (contact.collected_at, contact.evidence_id))
    cited = (last.evidence_id,)
    if last.collected_at > evaluated_at + CLOCK_SKEW:
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{AGENT_SAYS}: el último contacto tiene fecha futura ({_moment(last.collected_at)})",
            code="evidence_in_future",
            evidence_ids=cited,
        )
    if evaluated_at - last.collected_at <= limit:
        return Outcome(
            CheckStatus.PASS,
            f"{AGENT_SAYS}: último contacto del agente el {_moment(last.collected_at)}",
            evidence_ids=cited,
        )
    return Outcome(
        CheckStatus.FAIL,
        f"{AGENT_SAYS}: agente sin contacto desde {_moment(last.collected_at)} "
        f"(umbral de {context.config.max_agent_silence_hours} h)",
        code="agent_silent",
        evidence_ids=cited,
    )


def _moment(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


# Consultas que lee cada check (constante: decide qué fuente tiene la observación más reciente)
# y, por plataforma, grupos de los que hace falta al menos una consulta para poder concluir.
CHECK_QUERIES: dict[str, tuple[str, ...]] = {
    OS_SUPPORTED: ("os_version",),
    DISK_ENCRYPTION: ("root_mount", "disk_encryption", "disk_encryption_darwin"),
    FIREWALL: ("alf", "os_version", "osquery_info"),
    AUTO_UPDATES: ("software_update_policy",),
    SOFTWARE_INVENTORY: ("apps", "deb_packages", "rpm_packages"),
    SIGNATURES: ("clamav_signatures", "clamav_units", "xprotect_bundle"),
    REALTIME: ("gatekeeper", "xprotect_bundle"),
}


def _needed_queries(check_id: str, platform: str) -> tuple[tuple[str, ...], ...]:
    darwin = platform == "darwin"
    match check_id:
        case "HOST-003":
            return (("os_version",),)
        case "HOST-004":
            return (("root_mount",), ("disk_encryption_darwin" if darwin else "disk_encryption",))
        case "HOST-005":
            return (("alf",), ("os_version",), ("osquery_info",)) if darwin else ()
        case "HOST-006":
            return (("software_update_policy",),) if darwin else ()
        case "HOST-007":
            return (("apps",),) if darwin else (("deb_packages", "rpm_packages"),)
        case "HOST-008":
            return (
                (("xprotect_bundle",),) if darwin else (("clamav_signatures",), ("clamav_units",))
            )
        case "HOST-009":
            return (("gatekeeper",), ("xprotect_bundle",)) if darwin else ()
    return ()


def _all_unknown(reason: str) -> dict[str, Outcome]:
    outcome = Outcome(
        CheckStatus.UNKNOWN, f"{ATTESTED}: {reason}", code="unreadable_evidence", cite=False
    )
    return {check_id: outcome for check_id in HOST_CHECKS}


def _unreadable_reason(unreadable: Sequence[str]) -> str:
    return f"evidencia ilegible, no se sabe cuál es la última ejecución ({'; '.join(unreadable)})"


def _without_evidence(code: str, reason: str) -> dict[str, Outcome]:
    full = f"{ATTESTED}: {reason}"
    coverage = Outcome(CheckStatus.FAIL, full, code=code, cite=False)
    unknown = Outcome(CheckStatus.UNKNOWN, full, code=code, cite=False)
    return {check_id: unknown for check_id in HOST_CHECKS} | {COVERAGE: coverage}


def _same_host(item: HostEvidence, enrollment: HostEnrollment) -> bool:
    package = _dict(item.result.data.get("package"))
    return enrollment.matches(
        str(package.get("hardware_uuid", "")), str(package.get("hostname", ""))
    )


def _freshness(item: HostEvidence, context: CheckContext) -> Outcome | None:
    evaluated_at = context.evaluated_at
    if item.collected_at > evaluated_at + CLOCK_SKEW:
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{ATTESTED}: la evidencia tiene fecha futura ({item.collected_at.isoformat()})",
            code="evidence_in_future",
        )
    expires_at = item.expires_at(context.config.max_evidence_age_days)
    if evaluated_at >= expires_at:
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{ATTESTED}: la última ejecución caducó el {expires_at.isoformat()}",
            code="evidence_expired",
        )
    return None


def _result(check_id: str, asset_id: str, outcome: Outcome, evaluated_at: datetime) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        asset_id=asset_id,
        subject=asset_id,
        status=outcome.status,
        evaluated_at=evaluated_at,
        evidence_ids=list(outcome.evidence_ids) if outcome.cite else [],
        reason=outcome.reason,
        collection_status=outcome.collection,
        reason_code=outcome.code,
    )


def _unknown(reason: str, code: str = "no_data") -> Outcome:
    return Outcome(CheckStatus.UNKNOWN, f"{ATTESTED}: {reason}", code=code)


def _unrecovered(collection: CollectionStatus, what: str) -> Outcome:
    translation = translate_collection(collection)
    return Outcome(
        translation.status,
        f"{ATTESTED}: la consulta de {what} no se recogió con éxito ({collection})",
        collection,
        translation.reason_code,
    )


def _failed_query(host: Host, query_ids: Iterable[str], what: str) -> Outcome | None:
    ids = tuple(query_ids)
    collection = worst(host.status(query_id) for query_id in ids)
    return None if collection is CollectionStatus.SUCCESS else _unrecovered(collection, what)


def _single_row(host: Host, query_id: str) -> dict[str, Any] | None:
    rows = host.rows(query_id)
    return rows[0] if len(rows) == 1 else None


# HOST-003 ---------------------------------------------------------------------------------


# Sistemas cuya versión sin componentes finales equivale a ceros ("22.04" es 22.04.0).
# En el resto (p. ej. Debian, "12 (bookworm)") falta la versión menor: no se compara.
ZERO_FILLED_VERSIONS = frozenset({"macos", "ubuntu"})


def _os_supported(host: Host, context: CheckContext) -> Outcome:
    if (failed := _failed_query(host, ["os_version"], "versión del sistema")) is not None:
        return failed
    row = _single_row(host, "os_version")
    version = _parse_version(row.get("version")) if row else None
    name = str(row.get("name", "")).strip().casefold() if row else ""
    if row is None or not name or version is None:
        return _unknown("`os_version` no da un único nombre y versión interpretables")
    policy = _policy_for(context.config.supported_os, host.platform, name, version)
    label = f"{row.get('name')} {row.get('version')}"
    if policy is None:
        return _unknown(f"no hay política `supported_os` para {label}", "no_policy")
    if policy.end_of_support and context.evaluated_at.date() >= policy.end_of_support:
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: {label} llegó al fin de soporte el {policy.end_of_support.isoformat()}",
        )
    minimum = parse_numeric(policy.min_version) if policy.min_version else None
    if minimum and len(version) < len(minimum) and name not in ZERO_FILLED_VERSIONS:
        return _unknown(
            f"{label} no informa la versión con el detalle de la mínima {policy.min_version}; "
            "no se puede comparar"
        )
    if minimum and not _at_least(version, minimum):
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: {label} es anterior a la versión mínima {policy.min_version}",
        )
    return Outcome(CheckStatus.PASS, f"{ATTESTED}: {label} está en una release soportada")


def _policy_for(
    policies: list[SupportedOs], platform: str, name: str, version: tuple[int, ...]
) -> SupportedOs | None:
    """La única entrada de `supported_os` de la release exacta del equipo."""
    key = (platform, name, release_of(name, version))
    return next((policy for policy in policies if policy.key == key), None)


def _at_least(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    return _padded(version, minimum) >= _padded(minimum, version)


def _parse_version(raw: Any) -> tuple[int, ...] | None:
    match = re.match(r"\s*(\d+(?:\.\d+)*)", str(raw or ""))
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _padded(version: tuple[int, ...], other: tuple[int, ...]) -> tuple[int, ...]:
    return version + (0,) * (len(other) - len(version))


# HOST-004 ---------------------------------------------------------------------------------


def _disk_encryption(host: Host) -> Outcome:
    disks = "disk_encryption_darwin" if host.platform == "darwin" else "disk_encryption"
    if (
        failed := _failed_query(host, ["root_mount", disks], "cifrado del volumen raíz")
    ) is not None:
        return failed
    root = _single_row(host, "root_mount")
    device = str(root.get("device", "")).strip() if root else ""
    if root is None or root.get("path") != "/" or not device:
        return _unknown("`mounts` no identifica un único volumen raíz", "ambiguous")
    alias = str(root.get("device_alias", "")).strip().casefold()
    aliases = {device.casefold(), alias} - {""}
    if host.platform == "darwin":
        return _filevault(host, device, alias)
    related = [
        row
        for row in host.rows("disk_encryption")
        if str(row.get("name", "")).casefold() in aliases
    ]
    states = {_encryption_state(row) for row in related}
    if len(related) != 1 or None in states:
        return _unknown(
            f"el volumen raíz {device} no casa con una única fila definida de `disk_encryption`",
            "ambiguous",
        )
    if states == {True}:
        return Outcome(CheckStatus.PASS, f"{ATTESTED}: el volumen raíz {device} está cifrado")
    return Outcome(CheckStatus.FAIL, f"{ATTESTED}: el volumen raíz {device} no está cifrado")


def _encryption_state(row: dict[str, Any]) -> bool | None:
    if str(row.get("encryption_status", "")).strip().casefold() == "undefined":
        return None
    return {"1": True, "0": False}.get(str(row.get("encrypted", "")).strip())


def _filevault_rows(host: Host, root_device: str, alias: str) -> list[dict[str, Any]]:
    """Fila de `disk_encryption` que corresponde al volumen raíz, nunca un hermano del disco.

    Regla: la fila cuyo `name` es el dispositivo raíz (o su alias) y, si no existe, la del
    volumen de respaldo. En APFS el raíz sellado `/dev/diskNsMsK` se apoya en `/dev/diskNsM`,
    así que se admite esa única derivación. Cualquier otra partición del mismo disco no cuenta.
    """
    names = {root_device.casefold(), alias} - {""}
    backing = re.fullmatch(r"(/dev/disk\d+s\d+)s\d+", root_device.casefold())
    rows = host.rows("disk_encryption_darwin")
    exact = [row for row in rows if str(row.get("name", "")).casefold() in names]
    if exact or backing is None:
        return exact
    return [row for row in rows if str(row.get("name", "")).casefold() == backing.group(1)]


def _filevault(host: Host, root_device: str, alias: str) -> Outcome:
    related = _filevault_rows(host, root_device, alias)
    if len(related) != 1:
        return _unknown(
            f"el volumen raíz {root_device} no casa con una única fila de `disk_encryption`",
            "ambiguous",
        )
    value = str(related[0].get("filevault_status", "")).strip().casefold()
    if value not in FILEVAULT_FLAGS:
        return _unknown(
            f"FileVault devuelve un valor no reconocido ({value!r}); "
            "solo se interpretan `on` y `off`",
            "ambiguous",
        )
    filevault, encrypted = FILEVAULT_FLAGS[value], _encryption_state(related[0])
    if filevault and encrypted is True:
        return Outcome(CheckStatus.PASS, f"{ATTESTED}: FileVault activo en el volumen raíz")
    if not filevault and encrypted is False:
        return Outcome(
            CheckStatus.FAIL, f"{ATTESTED}: FileVault desactivado y volumen raíz sin cifrar"
        )
    return _unknown(
        "FileVault y `disk_encryption` no coinciden o el cifrado por hardware no prueba FileVault",
        "ambiguous",
    )


# HOST-005 ---------------------------------------------------------------------------------


def _firewall(host: Host) -> Outcome:
    if host.platform != "darwin":
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{ATTESTED}: sin consulta de firewall concluyente en Linux en v1",
            CollectionStatus.NOT_SUPPORTED,
            "not_supported",
            cite=False,
        )
    if (failed := _failed_query(host, ["alf", "os_version"], "firewall de aplicación")) is not None:
        return failed
    if not _alf_reliable(host):
        return _unknown(
            "macOS 15 o superior exige osquery 5.14.1 o superior (según `osquery_info`) "
            "para leer `alf` con fiabilidad",
            "osquery_too_old",
        )
    row = _single_row(host, "alf")
    state = str(row.get("global_state", "")).strip() if row else ""
    if state in ALF_ACTIVE:
        return Outcome(
            CheckStatus.PASS, f"{ATTESTED}: firewall de aplicación activo (global_state={state})"
        )
    if state == "0":
        return Outcome(CheckStatus.FAIL, f"{ATTESTED}: firewall de aplicación desactivado")
    return _unknown("`alf.global_state` ausente, múltiple o con un valor no reconocido")


def _alf_reliable(host: Host) -> bool:
    row = _single_row(host, "os_version")
    macos = _parse_version(row.get("version")) if row else None
    if macos is None:
        return False
    if macos[0] < 15:
        return True
    info = _single_row(host, "osquery_info")
    osquery = _parse_version(info.get("version")) if info else None
    return (
        host.status("osquery_info") is CollectionStatus.SUCCESS
        and osquery is not None
        and _at_least(osquery, ALF_MIN_OSQUERY_FOR_MACOS_15)
    )


# HOST-006 ---------------------------------------------------------------------------------


def _auto_updates(host: Host) -> Outcome:
    if host.platform != "darwin":
        return Outcome(
            CheckStatus.UNKNOWN,
            f"{ATTESTED}: sin consulta de actualizaciones concluyente en Linux en v1",
            CollectionStatus.NOT_SUPPORTED,
            "not_supported",
            cite=False,
        )
    if (
        failed := _failed_query(host, ["software_update_policy"], "política de actualizaciones")
    ) is not None:
        return failed
    rows = [
        row for row in host.rows("software_update_policy") if row.get("name") == AUTO_INSTALL_KEY
    ]
    if rows and all(_is_user_scoped(row) for row in rows):
        return _unknown(
            f"`{AUTO_INSTALL_KEY}` solo consta como preferencia de un usuario, no del equipo",
            "user_scoped",
        )
    if not all(_has_scope(row) for row in rows):
        return _unknown(
            f"`{AUTO_INSTALL_KEY}` sin `user_scoped`: no se sabe si es del equipo", "no_scope"
        )
    rows = [row for row in rows if not _is_user_scoped(row)]
    states = {POLICY_FLAGS.get(str(row.get("value", "")).strip().casefold()) for row in rows}
    managed = all(str(row.get("manual", "")).strip() == "0" for row in rows)
    if states == {False}:
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: la instalación automática de macOS está desactivada ({AUTO_INSTALL_KEY})",
        )
    if states == {True} and managed:
        return Outcome(
            CheckStatus.PASS,
            f"{ATTESTED}: una preferencia gestionada activa la instalación automática",
        )
    return _unknown(
        f"sin estado efectivo gestionado de `{AUTO_INSTALL_KEY}` (comprobar o descargar no basta)",
        "no_effective_state",
    )


def _is_user_scoped(row: dict[str, Any]) -> bool:
    return str(row.get("user_scoped", "")).strip() == "1"


def _has_scope(row: dict[str, Any]) -> bool:
    return str(row.get("user_scoped", "")).strip() in {"0", "1"}


# HOST-007 ---------------------------------------------------------------------------------


def _software_inventory(host: Host) -> Outcome:
    queries = ["apps"] if host.platform == "darwin" else ["deb_packages", "rpm_packages"]
    counted = {query: _valid_software(host, query) for query in queries}
    total = sum(counted.values())
    if total == 0:
        failed = _failed_query(host, queries, "inventario de software")
        if failed is not None and all(
            host.status(query) is not CollectionStatus.SUCCESS for query in queries
        ):
            return failed
        return _unknown("las consultas de software no devuelven filas con nombre y versión válidos")
    detail = ", ".join(f"{count} en `{query}`" for query, count in counted.items() if count)
    note = f". Aviso de cobertura: {APPS_COVERAGE}" if host.platform == "darwin" else ""
    return Outcome(CheckStatus.PASS, f"{ATTESTED}: {total} elementos de software ({detail}){note}")


def _valid_software(host: Host, query_id: str) -> int:
    if host.status(query_id) is not CollectionStatus.SUCCESS:
        return 0
    return sum(1 for row in host.rows(query_id) if _is_software(row, query_id))


def _is_software(row: dict[str, Any], query_id: str) -> bool:
    version_column = "bundle_short_version" if query_id == "apps" else "version"
    name, version = (
        str(row.get("name", "")).strip(),
        str(row.get(version_column) or row.get("bundle_version", "")).strip(),
    )
    if query_id == "deb_packages" and row.get("status") != DEB_INSTALLED:
        return False
    return bool(name and version)


# HOST-008 y HOST-009 ----------------------------------------------------------------------

SECURITY_CENTER_VIEW = (
    "es la vista del Centro de seguridad de Windows, no el indicador de tiempo real de Defender"
)
FILE_DATE_NOTE = "la fecha es la del fichero, no la versión de las firmas"
BUILT_IN_NOTE = (
    "protección integrada del sistema (XProtect y Gatekeeper); su adecuación la valora una persona"
)
FRESHCLAM_UNIT = "clamav-freshclam.service"
ANTIVIRUS_OFF = frozenset({"off", "snoozed", "expired"})


@dataclass(frozen=True)
class SignatureDate:
    """Fecha de un fichero de firmas ya validada, o el resultado que impide usarla."""

    moment: datetime | None = None
    row: dict[str, Any] | None = None
    blocked: Outcome | None = None
    observed_at: datetime | None = None

    def age_exceeded(self, max_age_days: int) -> bool:
        """Antigüedad al observarse la consulta de la que sale la fecha, no al de otra."""
        assert self.moment is not None and self.observed_at is not None
        return self.observed_at - self.moment > timedelta(days=max_age_days)

    def outdated_at_evaluation(self, context: CheckContext, max_age_days: int) -> Outcome | None:
        """UNKNOWN si, con la recogida aún vigente, las firmas ya superan la política hoy."""
        assert self.moment is not None
        if context.evaluated_at - self.moment <= timedelta(days=max_age_days):
            return None
        return _unknown(
            f"las firmas del {self.moment:%Y-%m-%d} ({FILE_DATE_NOTE}) cumplían la política "
            f"de {max_age_days} días al recoger, pero ya no al evaluar; recoge evidencia nueva",
            "signatures_stale",
        )


def _signatures(host: Host, context: CheckContext) -> Outcome:
    match host.platform:
        case "linux":
            return _clamav_signatures(host, context)
        case "darwin":
            return _xprotect_signatures(host, context)
        case "windows":
            return _windows_signatures(host)
    return _not_supported("sin consulta de firmas antimalware para esta plataforma")


def _realtime(host: Host, context: CheckContext) -> Outcome:
    match host.platform:
        case "darwin":
            return _gatekeeper(host, context)
        case "windows":
            return _windows_realtime(host)
        case "linux":
            return _not_supported(
                "sin protección en tiempo real evidenciable en Linux: ClamAV no analiza en acceso"
            )
    return _not_supported("sin consulta de protección en tiempo real para esta plataforma")


def _not_supported(reason: str) -> Outcome:
    return Outcome(
        CheckStatus.UNKNOWN,
        f"{ATTESTED}: {reason}",
        CollectionStatus.NOT_SUPPORTED,
        "not_supported",
        cite=False,
    )


def _epoch(raw: Any) -> datetime | None:
    try:
        seconds = int(str(raw).strip())
    except ValueError:
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _newest_file_date(
    host: Host, query_id: str, rows: list[dict[str, Any]], what: str
) -> SignatureDate:
    """Fecha del fichero más reciente de `rows`; UNKNOWN si falta, es ilegible o es futura."""
    dated = [(_epoch(row.get("mtime")), row) for row in rows]
    if not dated:
        return SignatureDate(blocked=_unknown(f"no constan ficheros de {what}"))
    if any(moment is None for moment, _ in dated):
        return SignatureDate(blocked=_unknown(f"un fichero de {what} no informa su fecha"))
    moment, row = max(((m, r) for m, r in dated if m is not None), key=lambda pair: pair[0])
    observed_at = host.collected(query_id)
    if moment > observed_at + CLOCK_SKEW:
        return SignatureDate(
            blocked=_unknown(
                f"el fichero de {what} tiene fecha futura ({moment:%Y-%m-%d}); {FILE_DATE_NOTE}"
            )
        )
    return SignatureDate(moment=moment, row=row, observed_at=observed_at)


def _positive(raw: Any) -> bool:
    try:
        return int(str(raw).strip()) > 0
    except ValueError:
        return False


def _clamav_signatures(host: Host, context: CheckContext) -> Outcome:
    if (failed := _failed_query(host, ["clamav_signatures"], "firmas de ClamAV")) is not None:
        return failed
    daily = [
        row
        for row in host.rows("clamav_signatures")
        if str(row.get("path", "")).rsplit("/", 1)[-1].startswith("daily.")
    ]
    date = _newest_file_date(host, "clamav_signatures", daily, "firmas de ClamAV (`daily.*`)")
    if date.blocked is not None:
        return date.blocked
    assert date.moment is not None and date.row is not None
    if not _positive(date.row.get("size")):
        return _unknown(f"el fichero de firmas de ClamAV está vacío o sin tamaño; {FILE_DATE_NOTE}")
    shown = f"firmas de ClamAV del {date.moment:%Y-%m-%d} ({FILE_DATE_NOTE})"
    if date.age_exceeded(context.config.max_signature_age_days):
        limit = context.config.max_signature_age_days
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: {shown}, más antiguas que los {limit} días de la política",
        )
    limit = context.config.max_signature_age_days
    if (stale := date.outdated_at_evaluation(context, limit)) is not None:
        return stale
    if (failed := _failed_query(host, ["clamav_units"], "servicio clamav-freshclam")) is not None:
        return failed
    units = [row for row in host.rows("clamav_units") if row.get("id") == FRESHCLAM_UNIT]
    if len(units) != 1 or str(units[0].get("active_state", "")).strip() != "active":
        return _unknown(
            f"{shown}; `{FRESHCLAM_UNIT}` no consta activo, así que no se garantiza que "
            "se sigan actualizando",
            "freshclam_inactive",
        )
    return Outcome(
        CheckStatus.PASS, f"{ATTESTED}: {shown}, dentro de la política y con freshclam activo"
    )


def _xprotect(host: Host, context: CheckContext) -> tuple[datetime | None, Outcome | None]:
    """Fecha del XProtect más reciente y, si no se puede usar o está caducado, el resultado."""
    if (failed := _failed_query(host, ["xprotect_bundle"], "XProtect")) is not None:
        return None, failed
    rows = host.rows("xprotect_bundle")
    if any(not str(row.get("bundle_short_version", "")).strip() for row in rows):
        return None, _unknown("XProtect sin versión (`CFBundleShortVersionString`) legible")
    date = _newest_file_date(host, "xprotect_bundle", rows, "XProtect")
    if date.blocked is not None or date.moment is None:
        return None, date.blocked
    if date.age_exceeded(context.config.max_xprotect_age_days):
        limit = context.config.max_xprotect_age_days
        return date.moment, Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: XProtect del {date.moment:%Y-%m-%d} ({FILE_DATE_NOTE}), "
            f"más antiguo que los {limit} días de la política",
        )
    limit = context.config.max_xprotect_age_days
    return date.moment, date.outdated_at_evaluation(context, limit)


def _xprotect_signatures(host: Host, context: CheckContext) -> Outcome:
    moment, problem = _xprotect(host, context)
    if problem is not None:
        return problem
    return Outcome(
        CheckStatus.PASS,
        f"{ATTESTED}: XProtect del {moment:%Y-%m-%d} ({FILE_DATE_NOTE}), dentro de la política",
    )


def _gatekeeper(host: Host, context: CheckContext) -> Outcome:
    if (failed := _failed_query(host, ["gatekeeper"], "Gatekeeper")) is not None:
        return failed
    row = _single_row(host, "gatekeeper")
    state = str(row.get("assessments_enabled", "")).strip() if row else ""
    if state == "0":
        return Outcome(CheckStatus.FAIL, f"{ATTESTED}: Gatekeeper desactivado")
    if state != "1":
        return _unknown("`gatekeeper.assessments_enabled` ausente, múltiple o no reconocido")
    moment, problem = _xprotect(host, context)
    if problem is not None:
        cause = problem.reason.removeprefix(f"{ATTESTED}: ")
        return _unknown(f"Gatekeeper activo, pero XProtect no consta presente y al día: {cause}")
    return Outcome(
        CheckStatus.PASS,
        f"{ATTESTED}: Gatekeeper activo y XProtect del {moment:%Y-%m-%d} ({FILE_DATE_NOTE}); "
        f"{BUILT_IN_NOTE}",
    )


def _antivirus_rows(host: Host) -> list[dict[str, Any]]:
    return [
        row
        for row in host.rows("windows_security_products")
        if str(row.get("type", "")).strip().casefold() == "antivirus"
    ]


def _active(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("state", "")).strip().casefold() == "on"]


def _windows_signatures(host: Host) -> Outcome:
    query = "windows_security_products"
    if (failed := _failed_query(host, [query], "productos de seguridad")) is not None:
        return failed
    rows = _antivirus_rows(host)
    if not rows:
        return _not_supported(
            "sin productos antivirus en el Centro de seguridad (p. ej. Windows Server, "
            "donde `windows_security_products` no es compatible)"
        )
    active = _active(rows)
    if len(active) > 1:
        return _unknown("varios antivirus activos en el Centro de seguridad", "ambiguous")
    if not active:
        return _unknown("ningún antivirus activo en el Centro de seguridad", "no_data")
    name = active[0].get("name") or "antivirus"
    flag = str(active[0].get("signatures_up_to_date", "")).strip()
    if flag == "1":
        return Outcome(
            CheckStatus.PASS,
            f"{ATTESTED}: {name} con firmas al día; {SECURITY_CENTER_VIEW}",
        )
    if flag == "0":
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: {name} con firmas desactualizadas; {SECURITY_CENTER_VIEW}",
        )
    return _unknown(f"`signatures_up_to_date` ausente o no reconocido; {SECURITY_CENTER_VIEW}")


def _windows_realtime(host: Host) -> Outcome:
    query = "windows_security_products"
    if (failed := _failed_query(host, [query], "productos de seguridad")) is not None:
        return failed
    rows = _antivirus_rows(host)
    if not rows:
        return _not_supported("sin productos antivirus en el Centro de seguridad")
    if _active(rows):
        return _unknown(
            "el Centro de seguridad muestra un antivirus activo, pero eso no prueba la "
            "protección en tiempo real y su correspondencia está pendiente de validar; "
            f"{SECURITY_CENTER_VIEW}",
            "realtime_unproven",
        )
    states = {str(row.get("state", "")).strip().casefold() for row in rows}
    if states <= ANTIVIRUS_OFF:
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: ningún antivirus activo (estado {', '.join(sorted(states))}); "
            f"{SECURITY_CENTER_VIEW}",
        )
    return _unknown(f"estado de antivirus no reconocido; {SECURITY_CENTER_VIEW}")


# helpers ----------------------------------------------------------------------------------


def _status(raw: Any) -> CollectionStatus:
    try:
        return CollectionStatus(raw)
    except ValueError:
        return CollectionStatus.ERROR


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


RUNNERS: dict[str, Callable[[Host, CheckContext], Outcome]] = {
    OS_SUPPORTED: _os_supported,
    DISK_ENCRYPTION: lambda host, _: _disk_encryption(host),
    FIREWALL: lambda host, _: _firewall(host),
    AUTO_UPDATES: lambda host, _: _auto_updates(host),
    SOFTWARE_INVENTORY: lambda host, _: _software_inventory(host),
    SIGNATURES: _signatures,
    REALTIME: _realtime,
}
