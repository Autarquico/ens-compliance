"""`alerts.json`: desde cuándo está mal cada elemento y cuándo se avisó por última vez.

Aviso por transición a rojo y recordatorio cada 24 h mientras siga en rojo. El escritor es el
bucle de monitorización; la UI y la CLI solo leen.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ens_evidence.monitoring.files import monitoring_dir, read_json, write_json
from ens_evidence.monitoring.model import MailState, Reading, Status, instant, parse_instant

REMINDER_EVERY = timedelta(hours=24)
ALERTS_FILE = "alerts.json"
MAX_ERROR_CHARS = 200


@dataclass(frozen=True)
class Tracked:
    status: Status
    since: datetime
    notified_at: datetime | None


class AlertBook:
    def __init__(self, state_dir: Path) -> None:
        self._path = monitoring_dir(state_dir) / ALERTS_FILE
        document = read_json(self._path)
        document = document if isinstance(document, dict) else {}
        self.tracked: dict[str, Tracked] = {}
        for item_id, raw in (document.get("items") or {}).items():
            try:
                since = parse_instant(raw["since"])
                if since is not None:
                    self.tracked[item_id] = Tracked(
                        Status(raw["status"]), since, parse_instant(raw.get("notified_at"))
                    )
            except (KeyError, ValueError, TypeError):
                continue
        mail = document.get("mail") if isinstance(document.get("mail"), dict) else {}
        self._mail_attempt = parse_instant(mail.get("last_attempt"))
        self._mail_ok = mail.get("last_ok") if isinstance(mail.get("last_ok"), bool) else None
        self._mail_error = (
            str(mail["last_error"])[:MAX_ERROR_CHARS] if mail.get("last_error") else None
        )

    def update(self, readings: list[Reading], now: datetime) -> list[Reading]:
        """Actualiza «desde» por transición y devuelve los elementos en rojo que toca avisar."""
        current: dict[str, Tracked] = {}
        due: list[Reading] = []
        for reading in readings:
            if reading.status is Status.OK:
                continue
            before = self.tracked.get(reading.id)
            same = before is not None and before.status is reading.status
            tracked = before if same and before else Tracked(reading.status, now, None)
            current[reading.id] = tracked
            if reading.status is Status.FAIL and (
                tracked.notified_at is None or now - tracked.notified_at >= REMINDER_EVERY
            ):
                due.append(reading)
        self.tracked = current
        return due

    def since(self, item_id: str) -> datetime | None:
        tracked = self.tracked.get(item_id)
        return tracked.since if tracked else None

    def mark_notified(self, ids: list[str], now: datetime) -> None:
        for item_id in ids:
            tracked = self.tracked[item_id]
            self.tracked[item_id] = Tracked(tracked.status, tracked.since, now)

    def record_mail(self, now: datetime, ok: bool, error: str | None) -> None:
        self._mail_attempt, self._mail_ok = now, ok
        self._mail_error = None if ok else (error or "fallo")[:MAX_ERROR_CHARS]

    def mail_state(self, configured: bool) -> MailState:
        return MailState(configured, self._mail_attempt, self._mail_ok, self._mail_error)

    def save(self) -> None:
        write_json(
            self._path,
            {
                "items": {
                    item_id: {
                        "status": t.status.value,
                        "since": instant(t.since),
                        "notified_at": instant(t.notified_at) if t.notified_at else None,
                    }
                    for item_id, t in self.tracked.items()
                },
                "mail": {
                    "last_attempt": instant(self._mail_attempt) if self._mail_attempt else None,
                    "last_ok": self._mail_ok,
                    "last_error": self._mail_error,
                },
            },
        )
