"""Auditoría de escrituras: una línea por escritura, con identidad y destino, sin el cuerpo."""

import logging


def test_cada_escritura_deja_una_linea_de_auditoria_sin_cuerpo(session, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    response = session.post(
        "/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": "Marta Gil"}
    )

    assert response.status_code == 303
    lines = [r.getMessage() for r in caplog.records if r.name == "ens_evidence.ui.audit"]
    assert len(lines) == 1
    line = lines[0]
    assert "identity=ana@example.com" in line
    assert "step=aprobacion-alcance" in line and "action=aprobar" in line
    assert "scope/approval.yaml" in line and "2026-11-08T12:00:00" in line
    assert "Marta Gil" not in line


def test_lo_que_no_se_guarda_no_deja_auditoria(session, caplog):
    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    session.post("/asistente/aprobacion-alcance", {"accion": "aprobar", "approved_by": ""})
    session.post("/asistente/dependencias", {"purpose": ""})

    assert not [r for r in caplog.records if r.name == "ens_evidence.ui.audit"]
