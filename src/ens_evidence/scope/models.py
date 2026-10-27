"""Modelos del inventario y del alcance (guía ENS, Fase 0)."""

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from ens_evidence.models.contracts import Strict


class AssetType(StrEnum):
    LAPTOP = "laptop"
    SERVER = "server"
    APPLICATION = "application"
    DATABASE = "database"
    CODE_HOSTING = "code_hosting"
    EDGE_NETWORK = "edge_network"
    IDENTITY = "identity"
    PASSWORD_MANAGER = "password_manager"
    STORAGE = "storage"
    BACKUP = "backup"
    PROVIDER = "provider"


class Deployment(StrEnum):
    DEPLOYED = "deployed"
    PLANNED = "planned"


class ExclusionKind(StrEnum):
    ASSET = "asset"
    SERVICE = "service"


REQUIRED_FIELDS: dict[AssetType, tuple[str, ...]] = {
    AssetType.LAPTOP: ("os", "owner", "encrypted", "usage"),
    AssetType.SERVER: ("hostname", "function", "os", "environment", "location"),
    AssetType.APPLICATION: ("service", "domain", "repo", "database"),
    AssetType.DATABASE: ("engine", "location", "data", "backup"),
    AssetType.CODE_HOSTING: ("account", "repos", "actions"),
    AssetType.EDGE_NETWORK: ("account", "zones", "tunnel", "waf", "access"),
    AssetType.IDENTITY: ("users", "admins", "mfa"),
    AssetType.PASSWORD_MANAGER: ("mode", "recovery", "mfa"),
    AssetType.STORAGE: ("buckets", "location", "encryption", "access"),
    AssetType.BACKUP: ("source", "destination", "frequency", "restoration"),
    AssetType.PROVIDER: ("service", "function", "dependency"),
}


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


class Asset(Strict):
    id: str = Field(min_length=1)
    type: AssetType
    deployment: Deployment
    os: str | None = None
    owner: str | None = None
    encrypted: bool | None = None
    usage: str | None = None
    hostname: str | None = None
    function: str | None = None
    environment: str | None = None
    location: str | None = None
    service: str | None = None
    domain: str | None = None
    repo: str | None = None
    database: str | None = None
    engine: str | None = None
    data: str | None = None
    backup: str | None = None
    account: str | None = None
    repos: list[str] | None = None
    actions: str | None = None
    zones: list[str] | None = None
    tunnel: str | None = None
    waf: str | None = None
    access: str | None = None
    users: str | None = None
    admins: str | None = None
    mfa: bool | None = None
    mode: str | None = None
    recovery: str | None = None
    buckets: list[str] | None = None
    encryption: str | None = None
    source: str | None = None
    destination: str | None = None
    frequency: str | None = None
    restoration: str | None = None
    dependency: str | None = None

    @model_validator(mode="after")
    def _required_fields_present(self) -> "Asset":
        absent = [name for name in REQUIRED_FIELDS[self.type] if _missing(getattr(self, name))]
        if absent:
            raise ValueError(
                f"un activo de tipo {self.type.value} necesita estos campos: {', '.join(absent)}"
            )
        return self


class AssetsFile(Strict):
    assets: list[Asset]


class Service(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    assets: list[str] = Field(min_length=1)


class ServicesFile(Strict):
    services: list[Service]


class Dependency(Strict):
    id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    depends_on: str = Field(min_length=1)
    purpose: str | None = None


class DependenciesFile(Strict):
    dependencies: list[Dependency]


class Exclusion(Strict):
    kind: ExclusionKind
    ref: str = Field(min_length=1)
    justification: str

    @field_validator("justification")
    @classmethod
    def _justified(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("la justificación no puede estar vacía")
        return value


class SystemScope(Strict):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    included_assets: list[str] = Field(default_factory=list)
    included_services: list[str] = Field(default_factory=list)
    exclusions: list[Exclusion] = Field(default_factory=list)
