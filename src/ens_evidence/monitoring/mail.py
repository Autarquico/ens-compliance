"""Correo de avisos por SMTP con STARTTLS obligatorio; credenciales solo de `ENS_SMTP_*`.

Sin la configuración completa no hay correo (avisos solo en la web). Un fallo se devuelve como
texto saneado (nombre del tipo de error, nunca credenciales) y nunca se propaga.
"""

import smtplib
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage

from ens_evidence.monitoring.model import Reading

TIMEOUT_SECONDS = 30
DEFAULT_PORT = 587
SmtpFactory = Callable[[str, int], smtplib.SMTP]


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str = ""
    sender: str = ""
    recipients: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return f"SmtpSettings(host={self.host!r})"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "SmtpSettings | None":
        """Todo o nada: falta cualquiera de las variables (salvo el puerto) y no hay correo."""
        values = {
            k: env.get(f"ENS_SMTP_{k}", "").strip()
            for k in ("HOST", "USER", "PASSWORD", "FROM", "TO")
        }
        if not all(values.values()):
            return None
        try:
            port = int(env.get("ENS_SMTP_PORT", "").strip() or DEFAULT_PORT)
        except ValueError:
            return None
        recipients = tuple(r.strip() for r in values["TO"].split(",") if r.strip())
        if not 0 < port < 65536 or not recipients:
            return None
        return cls(
            values["HOST"], port, values["USER"], values["PASSWORD"], values["FROM"], recipients
        )


def alert_message(settings: SmtpSettings, failing: list[Reading]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"[ens-compliance] Monitorización: {len(failing)} elemento(s) en rojo"
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    lines = [
        "Estos elementos de la caja están en rojo. Es un aviso operativo: no cambia el "
        "estado de ninguna medida ENS.",
        "",
    ]
    for reading in failing:
        lines.append(f"- {reading.title}: {reading.detail} (runbook: {reading.runbook})")
    message.set_content("\n".join(lines) + "\n")
    return message


def send_alert(
    settings: SmtpSettings, failing: list[Reading], *, factory: SmtpFactory | None = None
) -> str | None:
    """`None` si se envió; si no, el motivo saneado. Nunca lanza."""
    try:
        message = alert_message(settings, failing)
        connect = factory or (lambda h, p: smtplib.SMTP(h, p, timeout=TIMEOUT_SECONDS))
        with connect(settings.host, settings.port) as smtp:
            smtp.ehlo()
            smtp.starttls(
                context=ssl.create_default_context()
            )  # sin STARTTLS: falla, no cae a claro
            smtp.ehlo()
            smtp.login(settings.user, settings.password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError, ValueError) as error:
        return type(error).__name__
    return None
