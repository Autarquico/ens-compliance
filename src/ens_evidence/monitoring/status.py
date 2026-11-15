"""Texto de `ens-evidence monitoring status` (solo lectura)."""

from datetime import datetime

from ens_evidence.monitoring.model import GROUPS, Snapshot

_LABEL = {"ok": "ok", "warn": "ámbar", "fail": "ROJO", "unknown": "desconocido"}
_GROUP_TITLE = {"herramientas": "Herramientas", "vps": "VPS", "caja": "Caja"}


def render_status(snapshot: Snapshot | None, mail_configured: bool, now: datetime) -> str:
    if snapshot is None:
        return "Sin instantánea: el bucle de monitorización aún no ha corrido.\n" + _mail(
            None, mail_configured
        )
    lines = [f"Instantánea de {snapshot.generated_at:%Y-%m-%d %H:%M} UTC"]
    if snapshot.stale:
        lines.append("DESACTUALIZADA: el bucle no ha corrido en más de 2 intervalos.")
    for group in GROUPS:
        lines.append(f"\n{_GROUP_TITLE[group]}")
        for item in snapshot.group(group):
            since = f" (desde {item.since:%Y-%m-%d %H:%M} UTC)" if item.since else ""
            lines.append(f"  [{_LABEL[item.status.value]}] {item.title}: {item.detail}{since}")
    return "\n".join(lines) + "\n\n" + _mail(snapshot, mail_configured)


def _mail(snapshot: Snapshot | None, configured: bool) -> str:
    if not configured:
        return "Correo: no configurado; los avisos son solo en la web.\n"
    text = "Correo: configurado."
    mail = snapshot.mail if snapshot else None
    if mail and mail.last_ok is False:
        text += f" Último envío fallido ({mail.last_error})."
    return text + "\n"
