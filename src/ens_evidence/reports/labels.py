"""Rótulos en castellano llano de los informes. Los códigos se quedan en el JSON; los informes
para personas dicen esto. Un código sin rótulo se muestra tal cual: nunca se oculta un valor.

`READINESS_LABELS` y los rótulos de estado de documento reutilizan los de `matrix.messages` y
`cli docs status`.
"""

from ens_evidence.matrix.messages import READINESS_LABELS
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.orgdocs.approval import DocumentState

__all__ = [
    "ACTION_STATUS_LABELS",
    "ASSET_TYPE_LABELS",
    "BACKUP_CODE_LABELS",
    "BACKUP_OUTCOME_LABELS",
    "BACKUP_RUN_LABELS",
    "CHANGE_LABELS",
    "COLLECTION_LABELS",
    "DOCUMENT_STATE_LABELS",
    "EXPIRY_KIND_LABELS",
    "PHASE_LABELS",
    "READINESS_LABELS",
    "REPORT_NOT_A_REVIEW",
    "STAGE_LABELS",
    "plain",
]

REPORT_NOT_A_REVIEW = "Este informe no sustituye la revisión de una persona."

ASSET_TYPE_LABELS = {
    "laptop": "portátil",
    "server": "servidor",
    "application": "aplicación",
    "database": "base de datos",
    "code_hosting": "alojamiento de código",
    "edge_network": "red perimetral",
    "identity": "identidad",
    "password_manager": "gestor de contraseñas",
    "storage": "almacenamiento",
    "backup": "copia de seguridad",
    "provider": "proveedor",
}

DOCUMENT_STATE_LABELS = {
    DocumentState.NOT_GENERATED.value: "sin generar",
    DocumentState.GENERATED.value: "generado, sin aprobar",
    DocumentState.APPROVED.value: "aprobado y vigente",
    DocumentState.OBSOLETE.value: "aprobado pero obsoleto",
}

COLLECTION_LABELS = {
    CollectionStatus.SUCCESS.value: "recogida correcta",
    CollectionStatus.ERROR.value: "ERROR",
    CollectionStatus.PERMISSION_DENIED.value: "permiso denegado",
    CollectionStatus.NOT_SUPPORTED.value: "no soportado",
}

PHASE_LABELS = {
    "plugin_load": "carga del conector",
    "config": "configuración",
    "prepare": "preparación",
    "collect": "recogida",
    "timeout": "tiempo agotado",
    "result_rejected": "resultado rechazado",
}

EXPIRY_KIND_LABELS = {
    "measure_review": "revisión de la medida",
    "applicability_review": "revisión de la aplicabilidad",
    "categorization_approval": "aprobación de la categorización",
    "risk_approval": "aprobación del análisis de riesgos",
    "restore_test": "prueba de restauración",
    "offsite_declaration": "declaración de copia externa",
    "manual_evidence": "evidencia manual",
    "technical_evidence": "evidencia técnica",
    "treatment_action": "acción del plan de tratamiento",
}

CHANGE_LABELS = {"added": "alta", "removed": "baja", "changed": "cambio"}

BACKUP_OUTCOME_LABELS = {
    "backed_up": "copia hecha",
    "backup_failed": "copia fallida",
    "backup_skipped": "copia omitida",
}

BACKUP_CODE_LABELS = {
    "disabled": "copia desactivada",
    "not_configured": "sin ajustes de copia",
    "invalid_credentials": "credenciales de copia no válidas",
    "previous_run_unfinished": "queda una copia anterior sin terminar",
    "workspace_unreadable": "workspace ilegible",
    "restic_failed": "falló la copia con restic",
    "record_not_persisted": "no se guardó el registro de la copia",
    "crashed": "fallo inesperado",
}

BACKUP_RUN_LABELS = {
    "started": "copia sin terminar",
    "completed": "copia completada",
    "failed": "copia fallida",
}

ACTION_STATUS_LABELS = {
    "pending": "pendiente",
    "in_progress": "en curso",
    "done": "hecha",
    "cancelled": "cancelada",
}

STAGE_LABELS = {
    "started": "inicio del ciclo",
    "collected": "recogida de datos",
    "checked": "evaluación de comprobaciones",
    "reported": "publicación del informe",
    "backed_up": "copia de seguridad hecha",
    "backup_failed": "copia de seguridad fallida",
    "backup_skipped": "copia de seguridad omitida",
}


def plain(labels: dict[str, str], code: str) -> str:
    return labels.get(code, code)
