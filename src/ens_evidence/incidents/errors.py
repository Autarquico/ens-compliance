"""Errores del registro de incidentes."""


class IncidentRejected(Exception):
    """El registro no se escribe; `errors` está listo para mostrar y nunca incluye valores."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
