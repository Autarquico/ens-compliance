"""Configuración del planificador: `reports/schedule.yaml` en el workspace."""

import datetime as dt
import re
from dataclasses import dataclass
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import AfterValidator, Field, ValidationError, field_validator

from ens_evidence.models.contracts import Strict
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.settings import reject_credential_keys
from ens_evidence.yaml_unique import safe_load_unique

SCHEDULE_KEY = "reports/schedule.yaml"
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_CONNECTOR_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ScheduleConfigError(WorkspaceError):
    """La configuración del planificador no es válida; el mensaje no incluye valores."""


def _known_zone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (LookupError, ValueError, OSError):
        raise ValueError("zona horaria desconocida") from None
    return name


class DailyConfig(Strict):
    time: dt.time = dt.time(6, 0)

    @field_validator("time", mode="before")
    @classmethod
    def _hhmm(cls, value: Any) -> dt.time:
        if isinstance(value, dt.time):
            return value
        if not isinstance(value, str) or not (match := _HHMM.match(value)):
            raise ValueError('debe ser una cadena "HH:MM" entre comillas (p. ej. "06:00")')
        return dt.time(int(match[1]), int(match[2]))


class MonthlyConfig(Strict):
    day: Annotated[int, Field(ge=1, le=28)] = 1


class CollectConfig(Strict):
    connectors: list[Annotated[str, Field(pattern=_CONNECTOR_NAME.pattern)]] | None = None
    retries: Annotated[int, Field(ge=0, le=5)] = 2
    retry_delay_minutes: list[Annotated[int, Field(ge=1, le=240)]] = [5, 15]
    timeout_minutes: Annotated[int, Field(ge=1, le=240)] = 15

    def delay_for(self, retry: int) -> int:
        """Minutos de espera antes del reintento `retry` (1 = primero); el último se repite."""
        return self.retry_delay_minutes[min(retry, len(self.retry_delay_minutes)) - 1]


class CycleConfig(Strict):
    attempts: Annotated[int, Field(ge=1, le=10)] = 3
    retry_interval_minutes: Annotated[int, Field(ge=1, le=720)] = 30


class ExpiryWarnings(Strict):
    """Días de antelación con que se avisa de cada caducidad."""

    measure_reviews: Annotated[int, Field(ge=0)] = 30
    approvals: Annotated[int, Field(ge=0)] = 30
    restore_tests: Annotated[int, Field(ge=0)] = 30
    offsite_backup: Annotated[int, Field(ge=0)] = 30
    manual_evidence: Annotated[int, Field(ge=0)] = 14
    technical_evidence: Annotated[int, Field(ge=0)] = 7


class BackupStep(Strict):
    enabled: bool = True


class ScheduleConfig(Strict):
    """Valores por defecto: ciclo diario a las 06:00 Europe/Madrid, informe mensual el día 1,
    2 reintentos por conector (5 y 15 min), 3 intentos de ciclo cada 30 min, copia activada."""

    timezone: Annotated[str, AfterValidator(_known_zone)] = "Europe/Madrid"
    daily: DailyConfig = DailyConfig()
    monthly: MonthlyConfig = MonthlyConfig()
    collect: CollectConfig = CollectConfig()
    cycle: CycleConfig = CycleConfig()
    expiry_warnings: ExpiryWarnings = ExpiryWarnings()
    backup: BackupStep = BackupStep()

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class LoadedSchedule:
    config: ScheduleConfig
    is_default: bool


def load_schedule_config(store: WorkspaceStore) -> LoadedSchedule:
    """Sin fichero: valores por defecto marcados como tales. Inválido: `ScheduleConfigError`."""
    try:
        raw = safe_load_unique(store.read_text(SCHEDULE_KEY))
    except NotFound:
        return LoadedSchedule(ScheduleConfig(), is_default=True)
    except (NotText, yaml.YAMLError):
        raise ScheduleConfigError(f"{SCHEDULE_KEY}: no es YAML UTF-8 válido") from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ScheduleConfigError(f"{SCHEDULE_KEY}: debe ser un mapa YAML")
    _reject_credentials(raw)
    try:
        return LoadedSchedule(ScheduleConfig.model_validate(raw), is_default=False)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_input=False, include_url=False)
        )
        raise ScheduleConfigError(f"{SCHEDULE_KEY}: {details}") from None


def _reject_credentials(raw: dict[str, Any]) -> None:
    try:
        reject_credential_keys(raw)
    except WorkspaceError as exc:
        raise ScheduleConfigError(f"{SCHEDULE_KEY}: {exc}") from None
    if leaked := find_secrets(raw):
        raise ScheduleConfigError(
            f"{SCHEDULE_KEY}: parece contener secretos en {', '.join(leaked)}"
        )


def default_schedule_yaml() -> str:
    """Contenido de `reports/schedule.yaml` para `scheduler init-config`."""
    return """\
# Planificador de informes. Todo lo que figura aquí son los valores por defecto.
# Sin credenciales: las de los conectores y de la copia van en variables de entorno.

# Zona IANA (Europe/Madrid). Los cambios de hora se resuelven solos: una hora inexistente
# se desplaza por el salto y una ambigua usa su primera ocurrencia.
timezone: Europe/Madrid

daily:
  time: "06:00"          # entre comillas: YAML lee 10:30 como un número

monthly:
  day: 1                 # 1..28; informe del mes natural anterior, tras el diario

collect:
  # connectors: [github, cloudflare]   # sin esta clave, todos los configurados
  retries: 2             # reintentos por conector (solo ante ERROR)
  retry_delay_minutes: [5, 15]
  timeout_minutes: 15    # una recogida que lo supera se termina y queda en ERROR

cycle:
  attempts: 3            # intentos del ciclo completo
  retry_interval_minutes: 30

expiry_warnings:         # días de antelación del aviso
  measure_reviews: 30
  approvals: 30
  restore_tests: 30
  offsite_backup: 30
  manual_evidence: 14
  technical_evidence: 7  # antes de cumplir sus 30 días

backup:
  enabled: true          # copia al final del ciclo diario; si falla, queda en ERROR
"""
