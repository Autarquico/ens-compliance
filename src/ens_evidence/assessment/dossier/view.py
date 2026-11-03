"""Lo que se añade a la instantánea al avanzar el expediente (secciones 10 y 11)."""

from pydantic import AwareDatetime, Field, model_validator

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


class TransitionRecord(Strict):
    """El fichero de la cadena: la transición con su posición y su enlace al anterior."""

    dossier_id: str
    seq: int = Field(ge=1)
    previous_sha256: str | None = Field(pattern=SHA256_PATTERN)
    recorded_at: AwareDatetime
    transition: Transition

    @model_validator(mode="after")
    def _first_has_no_predecessor(self) -> "TransitionRecord":
        if (self.seq == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first record has no previous_sha256")
        return self
