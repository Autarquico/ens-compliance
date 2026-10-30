"""La API pública del paquete y las piezas que no se duplican (ADR 0011)."""

from ens_evidence import matrix
from ens_evidence.matrix import evidence, links, model, review, status


def test_un_solo_alias_link_para_los_tres_tipos_de_enlace():
    assert evidence.Link is model.Link
    assert links.Link is model.Link


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
    assert all(not hasattr(matrix, name) for name in internals)


def test_el_estado_de_fila_vive_en_status_y_no_en_el_modulo_de_revisiones():
    assert status.row_status and status.RowStatus and status.matrix_status
    assert not hasattr(review, "row_status") and not hasattr(review, "RowStatus")
    assert matrix.RowStatus is status.RowStatus


def test_link_outcome_nombra_lo_declarado_sin_chocar_con_la_funcion_de_evidence():
    assert "declared" in links.LinkOutcome.__dataclass_fields__
    assert "link_units" not in links.LinkOutcome.__dataclass_fields__
