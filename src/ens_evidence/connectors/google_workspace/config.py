"""Configuración del conector de Google Workspace (`connectors/google-workspace.yaml`)."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MY_CUSTOMER = "my_customer"
CUSTOMER_ID = r"^C[0-9A-Za-z]{2,31}$"
EMAIL = r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}$"
DOMAIN = r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.[a-z]{2,63}$"


class GoogleWorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1)
    customer: str = MY_CUSTOMER
    admin_subject: str = Field(pattern=EMAIL)
    domains: list[str] = Field(min_length=1)

    @field_validator("customer")
    @classmethod
    def _customer(cls, value: str) -> str:
        if value != MY_CUSTOMER and not re.fullmatch(CUSTOMER_ID, value):
            raise ValueError(f"debe ser {MY_CUSTOMER} o un identificador de cliente (C...)")
        return value

    @field_validator("domains")
    @classmethod
    def _domains(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if not all(re.fullmatch(DOMAIN, value) for value in normalized):
            raise ValueError("cada dominio debe ser un nombre de dominio válido")
        return normalized

    @model_validator(mode="after")
    def _subject_in_domains(self) -> "GoogleWorkspaceConfig":
        if self.admin_subject.rpartition("@")[2].lower() not in self.domains:
            raise ValueError("admin_subject: su dominio debe estar en domains")
        return self
