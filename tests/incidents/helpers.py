from datetime import UTC, datetime, timedelta

from ens_evidence.incidents.model import IncidentRecord

DETECTED = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
RECORDED = DETECTED + timedelta(hours=2)
SHA = "a" * 64
EVIDENCE = "EV-20261015-0123abcd"


def procedure() -> dict:
    return {
        "name": "procedimiento-incidentes",
        "document_sha256": SHA,
        "approved_at": DETECTED - timedelta(days=30),
        "approval_key": "org/approvals/procedimiento-incidentes.yaml",
    }


def closure(**fields) -> dict:
    return {
        "closed_at": RECORDED,
        "conclusion": "Contenido el acceso indebido y rotadas las credenciales de servicio.",
        "legal_relevance": "no",
        "auditable_events_review": {
            "result": "no_change",
            "detail": "Los eventos ya cubrían el caso.",
        },
        **fields,
    }


def state(**fields) -> dict:
    return {
        "id": "INC-2026-001",
        "title": "Acceso indebido a la aplicación",
        "detected_at": DETECTED,
        **fields,
    }


def closed_state(**fields) -> dict:
    return state(**{"status": "closed", "personal_data": "no", "closure": closure(), **fields})


def record(**fields) -> IncidentRecord:
    values = {"seq": 1, "kind": "opened", "recorded_at": RECORDED, "state": state()}
    return IncidentRecord.model_validate({**values, **fields})


def full_payload() -> dict:
    """Registro con todos los campos admitidos rellenos."""
    return record(
        seq=2,
        kind="closed",
        previous_sha256=SHA,
        state=closed_state(
            severity="alto",
            assets=["APP-001"],
            source={"system": "wazuh", "alert_ids": ["1700000000.1234"]},
            analysis={"summary": "Credenciales reutilizadas.", "recommendations": ["Rotar claves"]},
            actions=[{"kind": "emergency", "at": DETECTED, "description": "Se aísla el servicio."}],
            evidence_ids=[EVIDENCE],
            procedure=procedure(),
        ),
    ).model_dump(mode="json")
