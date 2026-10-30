"""Contrato de la API pública del paquete (ADR 0011)."""

from ens_evidence import matrix


def test_la_api_publica_no_expone_las_fuentes_internas():
    internals = {
        "manual_support",
        "technical_support",
        "document_support",
        "effective_review",
        "obligations_for",
        "CoverExpansion",
        "InitOutcome",
    }

    assert internals.isdisjoint(matrix.__all__)
