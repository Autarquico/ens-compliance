from datetime import timedelta

import pytest
from applicability.helpers import (
    LATER,
    decide,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)
from matrix.acceptance_helpers import Clock, runner, settings_file
from matrix.evaluate_helpers import NOW
from matrix.helpers import COMPENSATION, SIGNED_AT

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.models.enums import Applicability


@pytest.fixture
def signed(tmp_path):
    """Alcance aprobado, categoría MEDIA y Declaración de Aplicabilidad firmada y vigente."""
    store = prepared_workspace(tmp_path / "ws", uniform("MEDIO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    decide(store, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    decide(store, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    return store


@pytest.fixture
def cli(tmp_path, signed, monkeypatch):
    clock = Clock(monkeypatch)
    clock.now = NOW - timedelta(hours=1)
    return runner(
        settings_file(tmp_path, {"backend": "local", "local": {"path": str(tmp_path / "ws")}})
    )
