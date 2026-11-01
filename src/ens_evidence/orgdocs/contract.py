"""Ids y opciones de las plantillas de los que depende el motor.

`check_engine_contract` (template.py) hace fallar la carga si alguno falta."""

from ens_evidence.models.enums import Role

RESPONSIBLES = ("resp_informacion", "resp_servicio", "resp_seguridad", "resp_sistema")
SECURITY_OFFICER = "resp_seguridad"
SYSTEM_OWNER = "resp_sistema"
PEOPLE = "personas"
PERSON_NAME = "nombre"
DIRECTION_SIGNATORY = "firmante_direccion"
DECLARED_PERSON_QUESTIONS = (*RESPONSIBLES, DIRECTION_SIGNATORY)
RELATION = "relacion_seguridad_sistema"
SAME_PERSON = "misma persona"
PROCEDURES_AUTHORITY_QUESTION = "autoridad_procedimientos"
INCIDENT_LEVELS = "incidentes_niveles"
INCIDENT_LEVEL_FIELDS = ("nivel", "criterio", "escalado")
# Opción que ve la organización -> rol que aprueba.
AUTHORITY_ROLE = {
    "la dirección": Role.MANAGEMENT,
    "el responsable de la seguridad": Role.SECURITY_OFFICER,
    "el responsable del sistema": Role.SYSTEM_OWNER,
}
