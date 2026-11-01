"""Mundos de prueba del expediente: MEDIA (el de checks) y BÁSICA (ligero, sin CLI)."""

from datetime import timedelta

import yaml
from applicability.helpers import decide_all_undetermined, prepared_workspace, uniform
from matrix.evaluate_helpers import NOW
from matrix.helpers import SIGNED_AT
from risk.helpers import (
    METHODOLOGY,
    METHODOLOGY_MD,
    action,
    adopted_methodology,
    dump,
    risk,
    write_risks,
)

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.assessment.dossier import current_inputs
from ens_evidence.risk import approve_risk, init_risk
from ens_evidence.workspace import LocalStore

INPUTS = current_inputs(NOW)
PASSWORD = "MiClave123"


def basic_store(path, *, declared: bool = True) -> LocalStore:
    """Categoría BÁSICA aprobada y, si `declared`, Declaración de Aplicabilidad firmada."""
    store = prepared_workspace(path, uniform("BAJO"))
    if declared:
        propose_applicability(store, SIGNED_AT - timedelta(hours=1))
        decide_all_undetermined(store)
        approve_declaration(store, "Luis Pardo", SIGNED_AT)
    return store


def add_risk_analysis(store: LocalStore, at=NOW - timedelta(hours=2)) -> None:
    """Metodología adoptada, un riesgo y una acción, aprobados por el responsable."""
    init_risk(store)
    store.write_text(METHODOLOGY_MD, "# Metodología\n\nLa que usa la organización.\n")
    dump(store, METHODOLOGY, adopted_methodology())
    write_risks(store, [risk()], [action()])
    approve_risk(store, "Marta Gil", at)


def edit_answers(store: LocalStore, field: str, text: str) -> None:
    answers = yaml.safe_load(store.read_text("org/answers.yaml"))
    answers[field] = text
    store.write_text("org/answers.yaml", yaml.safe_dump(answers, allow_unicode=True))
