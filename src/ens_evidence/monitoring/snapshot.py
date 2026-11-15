"""Instantánea y latido en `state/monitoring`: lo único que lee la UI.

`snapshot.json` y `heartbeat.json` los escribe solo el bucle; `load_snapshot` marca el panel como
desactualizado si cualquiera de los dos supera 2 intervalos.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ens_evidence.monitoring.files import monitoring_dir, read_json, write_json
from ens_evidence.monitoring.model import (
    Item,
    MailState,
    Point,
    Snapshot,
    Status,
    instant,
    parse_instant,
)

SNAPSHOT_FILE = "snapshot.json"
HEARTBEAT_FILE = "heartbeat.json"
STALE_AFTER_INTERVALS = 2


def write_heartbeat(state_dir: Path, now: datetime, interval_seconds: int) -> None:
    write_json(
        monitoring_dir(state_dir) / HEARTBEAT_FILE,
        {"at": instant(now), "interval_seconds": interval_seconds},
    )


def write_snapshot(state_dir: Path, snapshot: Snapshot) -> None:
    write_json(monitoring_dir(state_dir) / SNAPSHOT_FILE, _dump(snapshot))


def load_snapshot(state_dir: Path, now: datetime) -> Snapshot | None:
    """La última instantánea con `stale` calculado, o `None` si no existe o no es legible."""
    try:
        snapshot = _load(read_json(monitoring_dir(state_dir) / SNAPSHOT_FILE))
    except (KeyError, ValueError, TypeError, AttributeError, IndexError):
        return None
    if snapshot is None:
        return None
    beat = read_json(monitoring_dir(state_dir) / HEARTBEAT_FILE)
    beat_at = parse_instant(beat.get("at")) if isinstance(beat, dict) else None
    limit = timedelta(seconds=STALE_AFTER_INTERVALS * snapshot.interval_seconds)
    stale = beat_at is None or now - beat_at > limit or now - snapshot.generated_at > limit
    return Snapshot(
        snapshot.generated_at, snapshot.interval_seconds, snapshot.items, snapshot.mail, stale
    )


def _dump(snapshot: Snapshot) -> dict[str, Any]:
    mail = snapshot.mail
    return {
        "generated_at": instant(snapshot.generated_at),
        "interval_seconds": snapshot.interval_seconds,
        "mail": {
            "configured": mail.configured,
            "last_attempt": instant(mail.last_attempt) if mail.last_attempt else None,
            "last_ok": mail.last_ok,
            "last_error": mail.last_error,
        },
        "items": [
            {
                "id": i.id,
                "group": i.group,
                "title": i.title,
                "status": i.status.value,
                "detail": i.detail,
                "runbook": i.runbook,
                "since": instant(i.since) if i.since else None,
                "observed_at": instant(i.observed_at) if i.observed_at else None,
                "value": i.value,
                "series": [[instant(p.at), p.status.value, p.value] for p in i.series],
            }
            for i in snapshot.items
        ],
    }


def _load(raw: object) -> Snapshot | None:
    if not isinstance(raw, dict):
        return None
    generated = parse_instant(raw["generated_at"])
    if generated is None:
        return None
    mail = raw["mail"]
    items = tuple(
        Item(
            id=i["id"],
            group=i["group"],
            title=i["title"],
            status=Status(i["status"]),
            detail=i["detail"],
            runbook=i["runbook"],
            since=parse_instant(i["since"]),
            observed_at=parse_instant(i["observed_at"]),
            value=i["value"],
            series=tuple(
                Point(at, Status(p[1]), p[2])
                for p in i["series"]
                if (at := parse_instant(p[0])) is not None
            ),
        )
        for i in raw["items"]
    )
    mail_state = MailState(
        bool(mail["configured"]),
        parse_instant(mail["last_attempt"]),
        mail["last_ok"],
        mail["last_error"],
    )
    return Snapshot(generated, int(raw["interval_seconds"]), items, mail_state)
