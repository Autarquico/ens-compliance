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


def _lines(caplog):
    return [r for r in caplog.records if r.name == "ens_evidence.ui.audit"]


def test_un_rechazo_se_audita_como_rejected_sin_claves_ni_valores(caplog):
    from datetime import UTC, datetime

    from ens_evidence.ui.audit import audit_write

    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")

    audit_write(
        identity="ana@example.com",
        step="documentos",
        action="aprobar",
        keys=["org/docs/secreto.md"],
        at=datetime(2026, 11, 8, 12, tzinfo=UTC),
        roles=["management"],
        rejected=True,
    )

    (record,) = _lines(caplog)
    line = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "outcome=rejected" in line and "keys=-" in line
    assert "identity=ana@example.com" in line and "roles=management" in line
    assert "secreto" not in line


def test_rejected_y_failed_no_se_combinan():
    from datetime import UTC, datetime

    import pytest

    from ens_evidence.ui.audit import audit_write

    with pytest.raises(ValueError):
        audit_write(
            identity="a",
            step="s",
            action=None,
            keys=[],
            at=datetime(2026, 11, 8, tzinfo=UTC),
            failed=True,
            rejected=True,
        )


def test_las_llamadas_existentes_siguen_igual(caplog):
    from datetime import UTC, datetime

    from ens_evidence.ui.audit import audit_write

    caplog.set_level(logging.INFO, logger="ens_evidence.ui.audit")
    at = datetime(2026, 11, 8, tzinfo=UTC)

    audit_write(identity="a", step="s", action="x", keys=["scope/a.yaml"], at=at)
    audit_write(identity="a", step="s", action="x", keys=["scope/a.yaml"], at=at, failed=True)

    saved, failed = _lines(caplog)
    assert "outcome=saved" in saved.getMessage() and "keys=scope/a.yaml" in saved.getMessage()
    assert failed.levelno == logging.ERROR and "outcome=failed" in failed.getMessage()
