"""Configuración remota que se sirve a osquery: las consultas del paquete del repo, tal cual."""

from typing import Any

from ens_evidence.endpoint.pack import PACK_DIR, Query, load_pack

SCHEDULE_INTERVAL_SECONDS = 6 * 60 * 60
LOGGER_MIN_STATUS = 3


def pack_queries() -> dict[str, Query]:
    return {query.id: query for query in load_pack().queries}


def query_sql(query_id: str) -> str:
    """SQL del paquete byte a byte (también el salto de línea final)."""
    return (PACK_DIR / f"{query_id}.sql").read_text("utf-8")


def build_config() -> dict[str, Any]:
    schedule = {
        query.id: {
            "query": query_sql(query.id),
            "interval": SCHEDULE_INTERVAL_SECONDS,
            "snapshot": True,
            "platform": ",".join(query.platforms),
        }
        for query in load_pack().queries
    }
    return {"schedule": schedule, "options": {"logger_min_status": LOGGER_MIN_STATUS}}
