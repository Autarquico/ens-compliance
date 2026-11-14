"""Checks HOST-001…009 sobre la evidencia de endpoints (atestación manual).

Funciones puras. `PASS` solo con evidencia positiva y vigente de la última ejecución del
host; tabla vacía, ambigua o sin privilegios es `UNKNOWN`; el resto de estados de recogida
se traducen según el ADR 0002. Toda conclusión es "según atestación manual del host": los
hashes del paquete detectan corrupción, no falsificación. `CheckResult.subject` es el
activo evaluado.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ens_evidence.connectors.sections import worst
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
WORKSTATION_ONLY_CHECKS = frozenset({REALTIME})

ATTESTED = "Según atestación manual del host"
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


@dataclass(frozen=True)
class Host:
    evidence: HostEvidence
    platform: str
    queries: dict[str, Any]
    collected_at: datetime

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
) -> list[CheckResult]:
    """HOST-001…009 de cada laptop/server del alcance, sobre su última ejecución.

    HOST-009 (protección en tiempo real) solo se emite para laptops, en todos los caminos.

    Con evidencia `unreadable` no se sabe cuál es la última ejecución de ningún host (un
    manifiesto ilegible no dice de qué activo es): todos dan UNKNOWN, nunca la anterior.
    """
    latest = latest_by_host(evidence)
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
            else _outcomes(item, enrollment, context)
        )
        results += [
            _result(check_id, asset.id, outcomes[check_id], item, evaluated_at)
            for check_id in HOST_CHECKS
            if asset.type is AssetType.LAPTOP or check_id not in WORKSTATION_ONLY_CHECKS
        ]
    return results


def _outcomes(
    item: HostEvidence | None, enrollment: HostEnrollment | None, context: CheckContext
) -> dict[str, Outcome]:
    if item is None:
        if enrollment is not None:
            return _without_evidence(
                "no_evidence",
                (
                    "falta la evidencia del script guiado (`endpoint script`) de este equipo; "
                    "los resultados del agente aún no se evalúan (lo hará collector-osquery)"
                ),
            )
        return _without_evidence(
            "not_enrolled", "el equipo no está inscrito en `hosts` de connectors/endpoint.yaml"
        )
    if enrollment is None or not _same_host(item, enrollment):
        return _without_evidence(
            "enrollment_changed",
            "evidencia de otro equipo: no corresponde al uuid y nombre inscritos ahora",
        )
    freshness = _freshness(item, context)
    if freshness is not None:
        return {check_id: freshness for check_id in HOST_CHECKS}
    host = Host(
        item,
        str(_dict(item.result.data.get("package")).get("platform")),
        _dict(item.result.data.get("queries")),
        item.collected_at,
    )
    return {
        COVERAGE: Outcome(
            CheckStatus.PASS, f"{ATTESTED}: hay evidencia vigente del {item.collected_at:%Y-%m-%d}"
        ),
        AGENT: Outcome(
            CheckStatus.UNKNOWN,
            f"{ATTESTED}: sin agente en v0.1; lo cubre `collector-osquery`",
            CollectionStatus.NOT_SUPPORTED,
            "not_supported",
            cite=False,
        ),
        OS_SUPPORTED: _os_supported(host, context),
        DISK_ENCRYPTION: _disk_encryption(host),
        FIREWALL: _firewall(host),
        AUTO_UPDATES: _auto_updates(host),
        SOFTWARE_INVENTORY: _software_inventory(host),
        SIGNATURES: _signatures(host, context),
        REALTIME: _realtime(host, context),
    }


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


def _result(
    check_id: str,
    asset_id: str,
    outcome: Outcome,
    item: HostEvidence | None,
    evaluated_at: datetime,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        asset_id=asset_id,
        subject=asset_id,
        status=outcome.status,
        evaluated_at=evaluated_at,
        evidence_ids=[item.evidence_id] if item is not None and outcome.cite else [],
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

    def age_exceeded(self, host: Host, max_age_days: int) -> bool:
        assert self.moment is not None
        return host.collected_at - self.moment > timedelta(days=max_age_days)


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


def _newest_file_date(host: Host, rows: list[dict[str, Any]], what: str) -> SignatureDate:
    """Fecha del fichero más reciente de `rows`; UNKNOWN si falta, es ilegible o es futura."""
    dated = [(_epoch(row.get("mtime")), row) for row in rows]
    if not dated:
        return SignatureDate(blocked=_unknown(f"no constan ficheros de {what}"))
    if any(moment is None for moment, _ in dated):
        return SignatureDate(blocked=_unknown(f"un fichero de {what} no informa su fecha"))
    moment, row = max(((m, r) for m, r in dated if m is not None), key=lambda pair: pair[0])
    if moment > host.collected_at + CLOCK_SKEW:
        return SignatureDate(
            blocked=_unknown(
                f"el fichero de {what} tiene fecha futura ({moment:%Y-%m-%d}); {FILE_DATE_NOTE}"
            )
        )
    return SignatureDate(moment=moment, row=row)


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
    date = _newest_file_date(host, daily, "firmas de ClamAV (`daily.*`)")
    if date.blocked is not None:
        return date.blocked
    assert date.moment is not None and date.row is not None
    if not _positive(date.row.get("size")):
        return _unknown(f"el fichero de firmas de ClamAV está vacío o sin tamaño; {FILE_DATE_NOTE}")
    shown = f"firmas de ClamAV del {date.moment:%Y-%m-%d} ({FILE_DATE_NOTE})"
    if date.age_exceeded(host, context.config.max_signature_age_days):
        limit = context.config.max_signature_age_days
        return Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: {shown}, más antiguas que los {limit} días de la política",
        )
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
    date = _newest_file_date(host, rows, "XProtect")
    if date.blocked is not None or date.moment is None:
        return None, date.blocked
    if date.age_exceeded(host, context.config.max_xprotect_age_days):
        limit = context.config.max_xprotect_age_days
        return date.moment, Outcome(
            CheckStatus.FAIL,
            f"{ATTESTED}: XProtect del {date.moment:%Y-%m-%d} ({FILE_DATE_NOTE}), "
            f"más antiguo que los {limit} días de la política",
        )
    return date.moment, None


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
        return _unknown(
            f"Gatekeeper activo, pero XProtect no consta presente y al día: {problem.reason}"
        )
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
        return Outcome(
            CheckStatus.PASS,
            f"{ATTESTED}: hay un antivirus activo; {SECURITY_CENTER_VIEW}",
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
