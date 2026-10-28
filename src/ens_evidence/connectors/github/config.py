"""Configuración del conector de GitHub (`connectors/github.yaml`)."""

from pydantic import BaseModel, ConfigDict, Field

ORGANIZATION = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitHubConfig(_Strict):
    asset_id: str = Field(min_length=1)
    organization: str = Field(pattern=ORGANIZATION)


class GitHubRun(GitHubConfig):
    """Configuración más los datos del inventario que `prepare` resuelve para una recogida."""

    inventory_repos: list[str] = Field(default_factory=list)
