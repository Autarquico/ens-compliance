"""Quién puede qué en la UI: edición y roles ENS por correo, en `settings/ui-roles.yaml`.

El fichero lo escribe solo la CLI (`ens-evidence ui roles`); la UI lo lee en cada petición y no
puede escribirlo (queda fuera de `UiStore.WRITABLE_PREFIXES`). Ausente o inválido: nadie edita.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from ens_evidence.models.enums import Role
from ens_evidence.workspace import NotFound, WorkspaceError
from ens_evidence.yaml_unique import safe_load_unique

ROLES_KEY = "settings/ui-roles.yaml"
_LOGGER = logging.getLogger("ens_evidence.ui.roles")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+")

ROLE_LABELS = {
    Role.SYSTEM_ADMINISTRATOR: "Administrador del sistema",
    Role.SYSTEM_ADMINISTRATOR_DELEGATE: "Delegado del administrador",
    Role.SECURITY_OFFICER: "Responsable de la seguridad",
    Role.SYSTEM_OWNER: "Responsable del sistema",
    Role.MANAGEMENT: "Dirección",
    Role.INFORMATION_OWNER: "Responsable de la información",
    Role.SERVICE_OWNER: "Responsable del servicio",
}


class RolesFileInvalid(Exception):
    """El fichero de roles no es válido; el mensaje nunca incluye valores."""


class RoleDenied(Exception):
    """La operación exige un rol ENS que el usuario no tiene asignado."""

    def __init__(self, role: Role) -> None:
        super().__init__(role.value)
        self.role = role


def normalize_email(value: str) -> str:
    """La única normalización: la misma para la CLI, el fichero y la identidad del JWT."""
    return value.strip().casefold()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _User(_Strict):
    edit: StrictBool = False
    roles: list[Role] = Field(default_factory=list)


class _File(_Strict):
    users: dict[str, _User] = Field(default_factory=dict)


@dataclass(frozen=True)
class Grant:
    edit: bool = False
    roles: frozenset[Role] = frozenset()


class RolesStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class RolesFile:
    status: RolesStatus
    users: Mapping[str, Grant] = field(default_factory=dict)

    def grant_of(self, email: str) -> Grant:
        return self.users.get(normalize_email(email), Grant())


def parse_roles(text: str) -> dict[str, Grant]:
    """Los permisos por correo normalizado; `RolesFileInvalid` ante cualquier irregularidad."""
    try:
        parsed = _File.model_validate(safe_load_unique(text) or {})
    except (yaml.YAMLError, ValidationError, ValueError):
        raise RolesFileInvalid("no cumple el esquema") from None
    users: dict[str, Grant] = {}
    for raw_email, user in parsed.users.items():
        email = normalize_email(raw_email)
        if not _EMAIL.fullmatch(email) or email in users:
            raise RolesFileInvalid("correo vacío, mal formado o repetido")
        users[email] = Grant(edit=user.edit, roles=frozenset(user.roles))
    return users


def dump_roles(users: Mapping[str, Grant]) -> str:
    document = {
        "users": {
            email: {"edit": grant.edit, "roles": sorted(r.value for r in grant.roles)}
            for email, grant in sorted(users.items())
        }
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def load_roles(store) -> RolesFile:
    """Lee el fichero en fresco; ausente o ilegible, nadie tiene permisos."""
    try:
        text = store.read_text(ROLES_KEY)
    except NotFound:
        return RolesFile(RolesStatus.MISSING)
    except WorkspaceError:
        _LOGGER.warning("roles_file=unreadable")
        return RolesFile(RolesStatus.INVALID)
    try:
        return RolesFile(RolesStatus.OK, parse_roles(text))
    except RolesFileInvalid:
        _LOGGER.warning("roles_file=invalid")
        return RolesFile(RolesStatus.INVALID)


@dataclass
class Permissions:
    """Lo que puede hacer quien hace esta petición. `used` recoge los roles que han autorizado."""

    email: str = ""
    status: RolesStatus = RolesStatus.MISSING
    edit: bool = False
    roles: frozenset[Role] = frozenset()
    used: list[Role] = field(default_factory=list)

    @classmethod
    def of(cls, roles_file: RolesFile, email: str) -> "Permissions":
        grant = roles_file.grant_of(email)
        return cls(normalize_email(email), roles_file.status, grant.edit, grant.roles)

    def require(self, role: Role) -> Role:
        """Autoriza con `role` (y edición) o lanza `RoleDenied`; devuelve el rol validado."""
        if not (self.edit and role in self.roles):
            raise RoleDenied(role)
        self.used.append(role)
        return role

    def offer(self, roles: Iterable[Role]) -> list[Role]:
        """De las opciones de un formulario, solo las que este usuario tiene asignadas."""
        return [role for role in roles if self.edit and role in self.roles]

    def lacks(self, role: Role) -> bool:
        return not (self.edit and role in self.roles)
