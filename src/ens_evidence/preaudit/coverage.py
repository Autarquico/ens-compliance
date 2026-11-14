"""Cobertura determinista del payload: qué propiedad de qué evidencia comprueba ya un check.

La capa de juicio no propone lo que un check evalúa; lo demás de una evidencia `collector` sí
puede contradecir un documento. Para saber dónde acaba una cosa y empieza la otra, el payload
lleva una entrada por check enlazado a una medida aplicable: la medida, el check, las
evidencias que lee, la propiedad que evalúa y su resultado. Sin volcar la configuración recogida.
"""

from typing import Any

from ens_evidence.matrix.obligations import RowKind
from ens_evidence.matrix.render import ExportedRow, MatrixExport

CHECK_PROPERTIES = {
    "github.org_2fa_required": "la organización de GitHub exige 2FA a sus miembros",
    "github.default_branch_protected": (
        "la rama por defecto de cada repositorio no archivado exige pull request y bloquea "
        "force-push y borrado, sin bypass demostrable (administradores o actores de bypass)"
    ),
    "github.secret_scanning_enabled": "secret scanning activado en cada repositorio no archivado",
    "github.dependabot_alerts_enabled": (
        "alertas de Dependabot activadas en cada repositorio no archivado"
    ),
    "cloudflare.tunnel_requires_access": (
        "toda ruta publicada por Cloudflare Tunnel está protegida por una aplicación de Access"
    ),
    "workspace.user_2sv_enrolled": (
        "Google indica 2SV activado (flag isEnrolledIn2Sv) en cada usuario activo, ni suspendido "
        "ni archivado; no prueba MFA resistente a phishing"
    ),
    "workspace.admin_2sv_enforced": (
        "Google indica 2SV exigido (flag isEnforcedIn2Sv) a cada administrador, suspendido o no, "
        "y que el descubrimiento de administradores es completo; no prueba MFA resistente a "
        "phishing"
    ),
    "workspace.suspended_without_admin_role": (
        "ningún usuario suspendido de Google Workspace conserva rol de administrador"
    ),
    "restic.backup_001_recent": (
        "el último snapshot del repositorio, citado por una copia completada, no supera la "
        "antigüedad máxima"
    ),
    "restic.backup_002_no_errors": (
        "la última ejecución de copia terminó completada y su snapshot está en el repositorio"
    ),
    "restic.backup_003_repository_accessible": (
        "restic lee la configuración del repositorio (existe y es accesible)"
    ),
    "restic.backup_004_integrity": (
        "el último `backup check` terminó sin errores, no supera la antigüedad máxima y alcanza "
        "la cobertura mínima de datos leídos"
    ),
    "restic.backup_005_restore_test": (
        "el último restore test está completo, aprobado, atado a una restauración verificada y "
        "vigente; el aislamiento del destino es una declaración humana no comprobada"
    ),
    "restic.backup_006_offsite": (
        "el repositorio es remoto y accesible y tiene una declaración offsite vigente; la "
        "separación administrativa del destino es una declaración no comprobada"
    ),
    "HOST-001": (
        "el equipo inscrito tiene evidencia de atestación vigente y no caducada de su última "
        "ejecución"
    ),
    "HOST-002": (
        "nada: en v0.1 no se evalúa el agente de seguridad y el resultado es siempre desconocido"
    ),
    "HOST-003": (
        "el sistema operativo del equipo está en una release con soporte según la política "
        "`supported_os` (fin de soporte y versión mínima)"
    ),
    "HOST-004": "cifrado del volumen raíz del equipo (no de otros discos)",
    "HOST-005": (
        "firewall de aplicación de macOS activo; en Linux no se evalúa y el resultado es "
        "desconocido"
    ),
    "HOST-006": (
        "instalación automática de actualizaciones de macOS activada por una preferencia "
        "gestionada del equipo; en Linux no se evalúa y el resultado es desconocido"
    ),
    "HOST-007": (
        "la atestación del equipo incluye filas de software con nombre y versión válidos; no "
        "prueba que el inventario sea completo"
    ),
}
"""Check → propiedad que evalúa. Un test lo compara con `checks.catalog.CHECKS`."""

FAIL, UNVERIFIED, PASS = "fail", "unverified", "pass"


def deterministic_coverage(export: MatrixExport, evidence: set[str]) -> list[dict[str, Any]]:
    """Una entrada por (medida aplicable, check enlazado), ordenadas por id.

    `evidence`: ids de las evidencias que van en el payload; solo se citan esas.
    """
    read_by = {c.check_id: set(c.evidence_ids) for c in export.sources.checks}
    entries = {}
    for row in export.rows:
        if row.kind is not RowKind.SELECTED:
            continue
        for link in row.technical:
            if link.kind == "check" and link.target in CHECK_PROPERTIES:
                entry_id = f"coverage:{row.measure_id}:{link.target}"
                entries[entry_id] = {
                    "id": entry_id,
                    "measure": row.measure_id,
                    "check": link.target,
                    "evidence": sorted(read_by.get(link.target, set()) & evidence),
                    "evaluates": CHECK_PROPERTIES[link.target],
                    "result": _result(row, link.target, link.counts),
                }
    return [entries[key] for key in sorted(entries)]


def _result(row: ExportedRow, check: str, counts: bool) -> str:
    named = [
        kind
        for kind, _, rest in (b.detail.partition(":") for b in row.blockers)
        if rest == check or rest.startswith(f"{check}:")
    ]
    if "check_failed" in named:
        return FAIL
    if not counts or named:
        return UNVERIFIED
    return PASS
