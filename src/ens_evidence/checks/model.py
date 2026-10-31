"""Una ejecución de los checks de un conector, tal como se guarda en el historial."""

from pydantic import AwareDatetime, Field, model_validator

from ens_evidence.models.contracts import SHA256_PATTERN, CheckResult, Strict


class CheckInput(Strict):
    """Una evidencia que la ejecución leyó y la huella de su manifiesto en ese momento."""

    evidence_id: str
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)


class StoredCheckRun(Strict):
    """Un intento del motor para un conector, completo o no.

    `seq` lo asigna el almacén al registrarlo. `expected_subjects`: sujetos que cada check debía
    cubrir. `config_sha256` y `scope_fingerprint` fijan con qué configuración y alcance se ejecutó.
    """

    schema_version: str = "1.0"
    seq: int = Field(ge=1, description="Número de orden dentro del conector, desde 1.")
    connector: str
    engine_version: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    complete: bool
    scope_fingerprint: str = Field(pattern=SHA256_PATTERN)
    config_sha256: str = Field(pattern=SHA256_PATTERN)
    inputs: list[CheckInput] = Field(default_factory=list)
    expected_subjects: dict[str, list[str]] = Field(default_factory=dict)
    results: list[CheckResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent(self) -> "StoredCheckRun":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at anterior a started_at")
        if self.complete and self.errors:
            raise ValueError("una ejecución con errores no puede estar completa")
        return self
