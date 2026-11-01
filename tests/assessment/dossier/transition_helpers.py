"""Mundos y atajos de las transiciones.

`stand_ins` sustituye tres comprobaciones que un mundo BÁSICA ligero no puede cumplir (revisión
en cada fila, preparación de la instantánea y de la evaluación actual): el mundo READY BÁSICA real
lo construye qa (Q1). Los demás tests usan el código sin sustituir.
"""

import atexit
import functools
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from matrix.acceptance_helpers import APPROVER, generate_and_approve_documents
from matrix.evaluate_helpers import NOW
from orgdocs.orgdocs_helpers import SIGNATORY

from ens_evidence.assessment.dossier import build_snapshot
from ens_evidence.assessment.dossier import transitions as service
from ens_evidence.assessment.dossier.store import save_snapshot
from ens_evidence.assessment.dossier.transitions import record_transition
from ens_evidence.models.enums import DossierState, Readiness, Role
from ens_evidence.models.readiness import Blocker, BlockerKind, ReadinessResult
from ens_evidence.orgdocs import approve_document
from ens_evidence.workspace import LocalStore

from .worlds import INPUTS, basic_store

AT = NOW + timedelta(hours=1)
RECORDED = NOW + timedelta(hours=2)
SECURITY_OFFICER = "Lucía Navarro Gil"
SYSTEM_OWNER = "Hugo Serrano Ortiz"
DIRECTION = "Marta Ibáñez Soler"
CONCLUSIONS = "Las medidas revisadas se han analizado y no se detectan desviaciones relevantes."
DESIGNATION = "designacion-responsables"
KEY = "evidence/assessments/{}/transitions"


@functools.cache
def _template() -> Path:
    root = Path(tempfile.mkdtemp(prefix="ens-dossier-docs-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    generate_and_approve_documents(basic_store(root))
    return root


def docs_world(path) -> LocalStore:
    """BÁSICA con Declaración y los seis documentos de org-docs aprobados (designación incluida)."""
    shutil.copytree(_template(), path)
    return LocalStore(path)


def approve_designation(store) -> None:
    """El mundo MEDIA solo aprueba el procedimiento de incidentes: falta la designación."""
    role = APPROVER[DESIGNATION]
    approve_document(store, DESIGNATION, SIGNATORY[role], role, NOW - timedelta(days=1))


def generate(store) -> str:
    return save_snapshot(store, build_snapshot(store, INPUTS))


def self_assess(store, dossier, *, at=AT, now=RECORDED, **kwargs):
    return record_transition(
        store,
        dossier,
        DossierState.SELF_ASSESSED,
        role=Role.SYSTEM_ADMINISTRATOR,
        at=at,
        now=now,
        **({"by": "Ana"} | kwargs),
    )


def analyze(store, dossier, *, at=AT + timedelta(minutes=5), now=RECORDED, **kwargs):
    values = {
        "by": SECURITY_OFFICER,
        "conclusions": CONCLUSIONS,
        "elevated_to": SYSTEM_OWNER,
        "elevation_confirmed": True,
        "manual_judgment_review": True,
    } | kwargs
    return record_transition(
        store,
        dossier,
        DossierState.ANALYZED,
        role=Role.SECURITY_OFFICER,
        at=at,
        now=now,
        **values,
    )


def declare(store, dossier, *, at=AT + timedelta(minutes=10), now=RECORDED, **kwargs):
    values = {"by": DIRECTION} | kwargs
    return record_transition(
        store,
        dossier,
        DossierState.DECLARED,
        role=Role.MANAGEMENT,
        at=at,
        now=now,
        **values,
    )


def fake_assess(readiness: Readiness = Readiness.READY, errors=()):
    blockers = (
        []
        if readiness is Readiness.READY
        else [Blocker(kind=BlockerKind.MISSING_REVIEW, measure_id="op.exp.1")]
    )
    result = ReadinessResult(readiness=readiness, blockers=blockers, pending_human_review=True)
    return lambda *args, **kwargs: SimpleNamespace(readiness=result, errors=tuple(errors))


@pytest.fixture
def docs(tmp_path):
    return docs_world(tmp_path / "ws")


@pytest.fixture
def reviewed(monkeypatch):
    """Da por hecha la revisión de cada fila (el mundo ligero no las tiene)."""
    monkeypatch.setattr(service, "_missing_reviews", lambda snapshot: [])


@pytest.fixture
def stand_ins(monkeypatch, reviewed):
    """Además, instantánea y evaluación actual READY."""
    monkeypatch.setattr(service, "_snapshot_readiness", lambda snapshot: Readiness.READY)
    monkeypatch.setattr(service, "assess", fake_assess())


@pytest.fixture
def declared_world(docs, stand_ins):
    """Cadena hasta ANALYZED sobre BÁSICA con las comprobaciones sustituidas."""
    dossier = generate(docs)
    self_assess(docs, dossier)
    analyze(docs, dossier)
    return docs, dossier
