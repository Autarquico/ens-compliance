"""Un workspace local con utilidades para fabricar y manipular incidentes (datos sintéticos)."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from ens_evidence.evidence import import_manual
from ens_evidence.incidents import (
    IncidentRecord,
    IncidentState,
    RecordKind,
    current_procedure,
    record_incident,
)
from ens_evidence.incidents.layout import (
    PROCEDURE_DOCUMENT,
    approval_key,
    record_key,
    render_record,
)
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.models.enums import Role
from ens_evidence.orgdocs.approval import DocumentApproval, DocumentState, DocumentStatus
from ens_evidence.orgdocs.meta import TemplateRef
from ens_evidence.workspace import LocalStore

from .helpers import RECORDED, closure, state

BY = "Ana Ejemplo"
SHA = "b" * 64
APPROVED_AT = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
LEVELS = (
    {"nivel": "bajo", "criterio": "sin impacto en el servicio", "escalado": "responsable"},
    {"nivel": "alto", "criterio": "servicio caído", "escalado": "dirección"},
)


class World:
    def __init__(self, root: Path) -> None:
        self.root = root / "ws"
        self.store = LocalStore(self.root)
        self.incident_id = "INC-2026-001"
        if not self.store.exists("scope/assets.yaml"):
            self.store.write_text(
                "scope/assets.yaml",
                yaml.safe_dump(
                    {
                        "assets": [
                            {
                                "id": "APP-001",
                                "type": "laptop",
                                "deployment": "deployed",
                                "os": "macOS",
                                "owner": "Ana",
                                "encrypted": True,
                                "usage": "trabajo",
                            },
                            {
                                "id": "APP-002",
                                "type": "laptop",
                                "deployment": "deployed",
                                "os": "Linux",
                                "owner": "Luis",
                                "encrypted": True,
                                "usage": "trabajo",
                            },
                        ]
                    }
                ),
            )

    def path(self, key: str) -> Path:
        return self.root.joinpath(*key.split("/"))

    def remove(self, key: str) -> None:
        os.remove(self.path(key))

    def keys(self, prefix: str = "") -> list[str]:
        return self.store.list(prefix)

    def written(self) -> list[str]:
        """Lo escrito además del inventario que prepara el propio mundo."""
        return [key for key in self.keys() if key != "scope/assets.yaml"]

    def declare_levels(self, rows=LEVELS) -> None:
        self.store.write_text(
            "org/answers.yaml", yaml.safe_dump({"incidentes_niveles": list(rows)})
        )

    def approval(self, document_sha256: str = SHA) -> DocumentApproval:
        return DocumentApproval(
            document=PROCEDURE_DOCUMENT,
            approved_by=BY,
            role=Role.MANAGEMENT,
            approved_at=APPROVED_AT,
            document_sha256=document_sha256,
            answers_sha256=SHA,
            template=TemplateRef(id=PROCEDURE_DOCUMENT, version=1),
            renderer_version=1,
            catalog_sha256=SHA,
            articulado_sha256=SHA,
            scope_fingerprint=SHA,
        )

    def write_approval(self, document_sha256: str = SHA) -> None:
        approval = self.approval(document_sha256)
        self.store.write_text(
            approval_key(approval.approved_at),
            yaml.safe_dump(approval.model_dump(mode="json"), sort_keys=False),
        )

    def approve_procedure(self, monkeypatch) -> None:
        """Deja `procedimiento-incidentes` aprobado y vigente (org-docs completo queda fuera)."""
        self.write_approval()
        status = DocumentStatus(
            PROCEDURE_DOCUMENT, DocumentState.APPROVED, approval=self.approval()
        )
        monkeypatch.setattr("ens_evidence.orgdocs.approval.document_status", lambda *_: status)

    def procedure(self):
        procedure = current_procedure(self.store)
        assert procedure is not None
        return procedure

    def evidence(self) -> str:
        return import_manual(
            self.store, b"captura sintetica", asset_id="a1", submitted_by=BY, now=RECORDED
        ).id

    def new_state(self, **fields) -> IncidentState:
        return IncidentState.model_validate(state(**fields))

    def open(self, **fields):
        self.incident_id = fields.get("id", self.incident_id)
        return record_incident(
            self.store, self.new_state(**fields), kind=RecordKind.OPENED, by=BY, now=RECORDED
        )

    def last_state(self) -> dict:
        scan = scan_incident(self.store, self.incident_id)
        return scan.records[-1].record.state.model_dump(mode="json")

    def update(self, hours: int = 1, **fields):
        return record_incident(
            self.store,
            IncidentState.model_validate({**self.last_state(), **fields}),
            kind=RecordKind.UPDATED,
            by=BY,
            now=RECORDED + timedelta(hours=hours),
        )

    def close(self, hours: int = 2, **fields):
        closing = {
            "status": "closed",
            "personal_data": "no",
            "closure": closure(),
            **fields,
        }
        return record_incident(
            self.store,
            IncidentState.model_validate({**self.last_state(), **closing}),
            kind=RecordKind.CLOSED,
            by=BY,
            now=RECORDED + timedelta(hours=hours),
        )

    def closed_incident(self, **fields):
        self.open()
        self.update(analysis={"summary": "Credenciales reutilizadas."})
        return self.close(**fields)

    def forge(self, record: IncidentRecord, *, old_key: str | None = None) -> str:
        """Escribe `record` con su clave correcta (sin reserva ni manifiesto); retira `old_key`."""
        data = render_record(record.model_dump(mode="json"))
        key = record_key(record, data)
        if old_key:
            self.remove(old_key)
        self.store.create_bytes(key, data)
        return key
