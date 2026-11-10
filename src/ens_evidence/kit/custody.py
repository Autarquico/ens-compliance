"""Registro de custodios del almacén de claves: quién guarda las partes, sin las partes."""

from datetime import UTC, datetime
from typing import Annotated, Self

import yaml
from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from ens_evidence.backup.records import dump
from ens_evidence.evidence import import_manual
from ens_evidence.evidence.layout import EVIDENCE_ROOT
from ens_evidence.models.contracts import Strict
from ens_evidence.models.enums import Role
from ens_evidence.privacy_text import check_declared_name
from ens_evidence.workspace import AlreadyExists, NotFound, WorkspaceError, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

CUSTODIANS_PREFIX = f"{EVIDENCE_ROOT}/key-custodians"

Name = Annotated[str, StringConstraints(strip_whitespace=True)]


class CustodianRegisterError(ValueError):
    """El registro no se pudo guardar; el mensaje no contiene datos sensibles."""


def _checked_name(value: str, label: str) -> str:
    if errors := check_declared_name(value, label):
        raise ValueError("; ".join(errors))
    return value


class Custodian(Strict):
    name: Name
    since: AwareDatetime

    @field_validator("name")
    @classmethod
    def _declared_name(cls, value: str) -> str:
        return _checked_name(value, "name")


class CustodyReview(Strict):
    reviewer: Name
    role: Role
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime

    @field_validator("reviewer")
    @classmethod
    def _declared_name(cls, value: str) -> str:
        return _checked_name(value, "reviewer")

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        if self.valid_until <= self.reviewed_at:
            raise ValueError("valid_until debe ser posterior a reviewed_at")
        return self


class CustodianRegister(Strict):
    """N y M son los declarados; de las partes no se guarda nada (ni orden ni huella)."""

    custodians: list[Custodian] = Field(min_length=1)
    key_shares: int = Field(ge=1, strict=True)
    key_threshold: int = Field(ge=1, strict=True)
    review: CustodyReview

    @model_validator(mode="after")
    def _threshold_within_shares(self) -> Self:
        if self.key_threshold > self.key_shares:
            raise ValueError("key_threshold no puede superar key_shares")
        return self


def register_key(register_id: str) -> str:
    return f"{CUSTODIANS_PREFIX}/{register_id}.yaml"


def record_custodians(
    store: WorkspaceStore, data: bytes, now: datetime, *, asset_id: str
) -> CustodianRegister:
    """Valida, guarda sin sobrescribir y registra como evidencia manual del activo `asset_id`
    (el del almacén de claves, el mismo que usa el conector `keystore`).

    `ValidationError` si los datos no valen; `CustodianRegisterError` si no se puede guardar.
    """
    try:
        loaded = safe_load_unique(data)
    except yaml.YAMLError:
        raise CustodianRegisterError("el fichero no es YAML válido") from None
    register = CustodianRegister.model_validate(loaded)
    if register.review.reviewed_at > now:
        raise CustodianRegisterError("review.reviewed_at está en el futuro")
    instant = now.astimezone(UTC)
    key = register_key(instant.strftime("%Y%m%dT%H%M%S%fZ"))
    body = dump(register)
    try:
        store.create_bytes(key, body)
    except AlreadyExists:
        raise CustodianRegisterError("ya hay un registro con ese identificador") from None
    try:
        import_manual(
            store,
            body,
            asset_id=asset_id,
            submitted_by=register.review.reviewer,
            now=instant,
            collected_at=register.review.reviewed_at,
            valid_until=register.review.valid_until,
        )
    except (WorkspaceError, ValueError) as exc:
        raise CustodianRegisterError(
            f"el registro se guardó pero no se pudo registrar como evidencia ({type(exc).__name__})"
        ) from None
    return register


def latest_register(
    store: WorkspaceStore, now: datetime
) -> tuple[CustodianRegister | None, list[str]]:
    """El registro vigente revisado más recientemente y las claves ilegibles."""
    registers: list[CustodianRegister] = []
    unreadable: list[str] = []
    for key in store.list(CUSTODIANS_PREFIX):
        if "/" in key.removeprefix(f"{CUSTODIANS_PREFIX}/"):
            continue
        try:
            registers.append(
                CustodianRegister.model_validate(safe_load_unique(store.read_bytes(key)))
            )
        except (ValueError, yaml.YAMLError, NotFound):
            unreadable.append(key)
    current = [r for r in registers if r.review.reviewed_at <= now < r.review.valid_until]
    latest = max(current, key=lambda r: r.review.reviewed_at, default=None)
    return latest, unreadable
