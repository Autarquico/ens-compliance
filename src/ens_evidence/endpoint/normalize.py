"""Normalización del paquete validado a un `CollectionResult` (atestación manual).

Por consulta se conserva el estado y solo las columnas de la lista blanca: las
que necesita algún check. No se guardan nombres de usuario ni rutas de usuario;
del equipo solo `hardware_uuid` y `hostname`, ya verificados contra la
inscripción, para que los checks detecten un cambio de inscripción. El paquete
original también se conserva, por eso sus consultas no piden más datos personales.
"""

from typing import Any

from ens_evidence.endpoint.config import HostEnrollment
from ens_evidence.endpoint.pack import load_pack
from ens_evidence.endpoint.package import Package, PackageRejected, parse_rows
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus

SOURCE = "endpoint-script"
IDENTITY_QUERY = "system_info"
MAX_STORED_CHARS = 200

STORED_COLUMNS: dict[str, tuple[str, ...]] = {
    IDENTITY_QUERY: (),
    "os_version": ("name", "platform", "version", "major", "minor", "patch", "build", "codename"),
    "osquery_info": ("version", "build_platform", "build_distro"),
    "root_mount": ("device", "device_alias", "path", "type", "flags"),
    "disk_encryption": ("name", "uuid", "encrypted", "type", "encryption_status"),
    "disk_encryption_darwin": (
        "name",
        "uuid",
        "encrypted",
        "type",
        "encryption_status",
        "filevault_status",
    ),
    "alf": (
        "global_state",
        "stealth_enabled",
        "logging_enabled",
        "firewall_unload",
        "allow_signed_enabled",
        "version",
    ),
    "software_update_policy": ("domain", "name", "value", "user_scoped", "manual"),
    "apps": ("name", "bundle_identifier", "bundle_short_version", "bundle_version"),
    "deb_packages": ("name", "version", "arch", "status"),
    "rpm_packages": ("name", "version", "release", "arch"),
}

ATTESTATION = (
    "Atestación manual: el paquete lo ha ejecutado y entregado una persona en el endpoint; "
    "los hashes detectan corrupción, no falsificación. No lo ha recogido un agente."
)


def verify_enrollment(package: Package, enrolled: HostEnrollment | None) -> None:
    """El paquete debe identificar al equipo inscrito para el activo (uuid y nombre)."""
    if enrolled is None:
        raise PackageRejected("el activo no está inscrito en `hosts` de connectors/endpoint.yaml")
    identity = _identity(package)
    if identity is None or not enrolled.matches(*identity):
        raise PackageRejected("el paquete no corresponde al equipo inscrito para este activo")
    if not _same_name(identity[1], package.meta.hostname):
        raise PackageRejected("el nombre de equipo de meta.json no coincide con `system_info`")


def _same_name(reported: str, declared: str) -> bool:
    """Mismo nombre corto: `hostname` y osquery pueden dar uno el FQDN y otro el nombre corto."""

    def short(name: str) -> str:
        return name.strip().casefold().split(".", 1)[0]

    return short(reported) == short(declared)


def normalize(package: Package, *, asset_id: str) -> CollectionResult:
    pack = load_pack()
    identity = _identity(package)
    assert identity is not None, "verify_enrollment debe ejecutarse antes de normalize"
    queries = {
        query.id: _query_data(package, query.id)
        if package.meta.platform in query.platforms
        else {"status": CollectionStatus.NOT_SUPPORTED, "row_count": 0, "rows": []}
        for query in pack.queries
    }
    return CollectionResult(
        source=SOURCE,
        collector=SOURCE,
        collected_at=package.meta.collected_at,
        asset_id=asset_id,
        collection_status=CollectionStatus.SUCCESS,
        data={
            "attestation": ATTESTATION,
            "package": {
                "sha256": package.sha256,
                "pack_id": package.meta.pack_id,
                "pack_version": package.meta.pack_version,
                "pack_sha256": package.meta.pack_sha256,
                "script_version": package.meta.script_version,
                "osquery_version": package.meta.osquery_version[:MAX_STORED_CHARS],
                "platform": package.meta.platform,
                "hardware_uuid": identity[0],
                "hostname": identity[1],
            },
            "queries": queries,
        },
    )


def _identity(package: Package) -> tuple[str, str] | None:
    run = _run(package, IDENTITY_QUERY)
    rows = (
        parse_rows(package.raw_results[IDENTITY_QUERY]) if run and run["exit_code"] == 0 else None
    )
    if rows is None or len(rows) != 1:
        return None
    uuid, hostname = rows[0].get("uuid"), rows[0].get("hostname")
    return (uuid, hostname) if uuid and hostname else None


def _run(package: Package, query_id: str) -> dict[str, Any] | None:
    for run in package.meta.queries:
        if run.id == query_id:
            return run.model_dump()
    return None


def _failed_status(run: dict[str, Any]) -> CollectionStatus:
    """Tabla o columna inexistente en ese osquery es `NOT_SUPPORTED`; sin permiso, el suyo."""
    if run["exit_code"] != 0 and run["unsupported_hint"]:
        return CollectionStatus.NOT_SUPPORTED
    if run["exit_code"] != 0 and run["permission_hint"]:
        return CollectionStatus.PERMISSION_DENIED
    return CollectionStatus.ERROR


def _query_data(package: Package, query_id: str) -> dict[str, Any]:
    run = _run(package, query_id)
    assert run is not None
    rows = parse_rows(package.raw_results[query_id]) if run["exit_code"] == 0 else None
    if rows is None:
        status = _failed_status(run)
        rows = []
    elif not rows and run["permission_hint"]:
        status = CollectionStatus.PERMISSION_DENIED
    else:
        status = CollectionStatus.SUCCESS
    columns = STORED_COLUMNS[query_id]
    return {
        "status": status,
        "exit_code": run["exit_code"],
        "permission_hint": run["permission_hint"],
        "unsupported_hint": run["unsupported_hint"],
        "row_count": len(rows),
        "rows": [
            {column: row[column][:MAX_STORED_CHARS] for column in columns if column in row}
            for row in rows
        ]
        if columns
        else [],
    }
