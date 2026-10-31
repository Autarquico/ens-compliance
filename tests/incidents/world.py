"""Un workspace local con utilidades para fabricar y manipular incidentes (datos sintéticos)."""

import os
from datetime import timedelta
from pathlib import Path

import yaml

from ens_evidence.evidence import import_manual
from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE, import_generated
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.incidents import (
    IncidentRecord,
    IncidentState,
    RecordKind,
    current_procedure,
    record_incident,
)
from ens_evidence.incidents.layout import (
    asset_id,
    record_key,
    render_record,
)
from ens_evidence.incidents.verify import scan_incident
from ens_evidence.workspace import LocalStore

from .acceptance_helpers import prepare_workspace
from .helpers import RECORDED, closure, state

BY = "Ana Ejemplo"
LEVELS = (
    {"nivel": "bajo", "criterio": "sin impacto en el servicio", "escalado": "responsable"},
    {"nivel": "alto", "criterio": "servicio caído", "escalado": "dirección"},
)


class HistoryRacingStore(LocalStore):
    """Una orden ajena publica el historial en cuanto se escribe el registro de cierre."""

    raced = None

    def create_bytes(self, key: str, data: bytes) -> None:
        super().create_bytes(key, data)
        if key.endswith(".yaml") and b"kind: closed" in data:
            self.raced = import_generated(
                self,
                b"historial ajeno",
                source=INCIDENT_HISTORY_SOURCE,
                asset_id=asset_id("INC-2026-001"),
                submitted_by=BY,
                now=RECORDED,
                collected_at=RECORDED,
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

    def approve_procedure(self) -> None:
        """Deja `procedimiento-incidentes` aprobado y vigente con el flujo real de org-docs.
        Sustituye el inventario por el del ejemplo: el alcance forma parte de la aprobación."""
        prepare_workspace(self.root)

    def replace_approved_document(self, document_sha256: str) -> None:
        """Reescribe el documento aprobado en su registro, como una edición a mano."""
        (key,) = self.keys("org/docs/approvals/procedimiento-incidentes/")
        record = yaml.safe_load(self.store.read_text(key))
        record["document_sha256"] = document_sha256
        self.store.write_text(key, yaml.safe_dump(record))

    def race_history_after_closure(self) -> "HistoryRacingStore":
        """Otra orden genera el historial justo tras escribirse el cierre: `record_history`
        rechaza sin parches. `drop_raced_history` lo quita para recuperarlo con
        `incident history`."""
        self.store = HistoryRacingStore(self.root)
        return self.store

    def drop_raced_history(self) -> None:
        self.remove(manifest_key(self.store.raced.id))

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
