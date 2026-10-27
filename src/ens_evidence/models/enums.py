"""Un enum por eje (docs/decisions/0002-modelo-de-estados.md)."""

from enum import StrEnum


class CollectionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class Applicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNDETERMINED = "UNDETERMINED"


class MeasureAssessment(StrEnum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    NOT_ASSESSED = "NOT_ASSESSED"


class Readiness(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class DossierState(StrEnum):
    GENERATED = "GENERATED"
    SELF_ASSESSED = "SELF_ASSESSED"
    ANALYZED = "ANALYZED"
    DECLARED = "DECLARED"


class Category(StrEnum):
    BASICA = "BASICA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"


class Role(StrEnum):
    SYSTEM_ADMINISTRATOR = "system_administrator"
    SYSTEM_ADMINISTRATOR_DELEGATE = "system_administrator_delegate"
    SECURITY_OFFICER = "security_officer"
    SYSTEM_OWNER = "system_owner"
