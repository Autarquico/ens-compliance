"""Configuración de la evidencia de endpoints (`connectors/endpoint.yaml`, ADR 0006).

La antigüedad máxima de la evidencia y las versiones soportadas son decisiones de la
organización: no tienen valor por defecto. La antigüedad máxima de las firmas antimalware
(`max_signature_age_days`) sí: 7 días. La inscripción de cada equipo (uuid y nombre) es
independiente del paquete que se importa y no contiene credenciales.
"""

import re
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Days = Annotated[int, Field(gt=0, strict=True)]
NUMERIC_VERSION = re.compile(r"\d+(\.\d+)*")

CONFIG_NAME = "endpoint"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def release_of(name: str, version: tuple[int, ...]) -> tuple[int, ...]:
    """Serie de una versión: YY.MM en Ubuntu; la versión mayor en el resto de sistemas."""
    return version[:2] if name.casefold() == "ubuntu" else version[:1]


def parse_numeric(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split("."))


class SupportedOs(_Strict):
    """Política de una serie del sistema: una entrada por (platform, name, release)."""

    platform: Literal["darwin", "linux"]
    name: Text
    release: Text
    min_version: Text | None = None
    end_of_support: date | None = None

    @field_validator("release", "min_version")
    @classmethod
    def numeric(cls, value: str | None) -> str | None:
        if value is not None and not NUMERIC_VERSION.fullmatch(value):
            raise ValueError("debe ser numérica, por ejemplo 15 o 22.04")
        return value

    @model_validator(mode="after")
    def _release_shape(self) -> Self:
        release = parse_numeric(self.release)
        if len(release) != len(release_of(self.name, release + (0, 0))):
            raise ValueError(
                "release: Ubuntu usa YY.MM (22.04); el resto de sistemas, la versión mayor (15)"
            )
        if self.min_version and release_of(self.name, parse_numeric(self.min_version)) != release:
            raise ValueError(f"min_version: {self.min_version} no pertenece a la release")
        return self

    @property
    def key(self) -> tuple[str, str, tuple[int, ...]]:
        return self.platform, self.name.casefold(), parse_numeric(self.release)


class HostEnrollment(_Strict):
    hardware_uuid: Text
    hostname: Text

    def matches(self, hardware_uuid: str, hostname: str) -> bool:
        return (
            self.hardware_uuid.casefold() == hardware_uuid.strip().casefold()
            and self.hostname.casefold() == hostname.strip().casefold()
        )


class EndpointConfig(_Strict):
    max_evidence_age_days: Days
    max_signature_age_days: Days = 7
    supported_os: list[SupportedOs]
    hosts: dict[Text, HostEnrollment]

    @model_validator(mode="after")
    def _one_policy_per_release(self) -> Self:
        keys = [policy.key for policy in self.supported_os]
        if len(keys) != len(set(keys)):
            raise ValueError("supported_os: hay dos entradas con la misma platform, name y release")
        return self

    @model_validator(mode="after")
    def _unambiguous_hosts(self) -> Self:
        for field in ("hardware_uuid", "hostname"):
            values = [getattr(host, field).casefold() for host in self.hosts.values()]
            if len(values) != len(set(values)):
                raise ValueError(f"hosts: hay dos activos con el mismo {field}")
        return self
