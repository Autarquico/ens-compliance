"""Lo que se añade a la instantánea al avanzar el expediente (secciones 10 y 11)."""

from pydantic import AwareDatetime, Field

from ens_evidence.assessment.dossier.model import DossierSnapshot
from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.dossier import Transition
from ens_evidence.models.enums import DossierState


class TransitionEntry(Strict):
    """Un registro de la cadena tal como lo guarda el almacén."""

    key: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    recorded_at: AwareDatetime
    record: Transition


class DossierView(Strict):
    """La instantánea con las transiciones registradas."""

    snapshot: DossierSnapshot
    transitions: list[TransitionEntry]

    @property
    def analysis(self) -> TransitionEntry | None:
        return next(
            (e for e in self.transitions if e.record.to_state is DossierState.ANALYZED), None
        )
