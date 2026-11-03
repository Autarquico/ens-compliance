"""Mundos y atajos de las transiciones: sobre el código real, sin sustituir internals."""

import atexit
import functools
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
from matrix.acceptance_helpers import APPROVER, generate_and_approve_documents
from matrix.evaluate_helpers import NOW
from orgdocs.orgdocs_helpers import SIGNATORY

from ens_evidence.assessment.dossier import build_snapshot
from ens_evidence.assessment.dossier.package import save_package
from ens_evidence.assessment.dossier.store import save_snapshot
from ens_evidence.assessment.dossier.transitions import record_transition
from ens_evidence.models.enums import Category, DossierState, Role
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
    """Como `dossier generate`: en MEDIA/ALTA guarda también el paquete por medida."""
    snapshot = build_snapshot(store, INPUTS)
    dossier = save_snapshot(store, snapshot)
    if snapshot.category is not Category.BASICA:
        save_package(store, dossier, snapshot)
    return dossier


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
    """Los mundos de prueba dejan secciones INCOMPLETE (p. ej. Riesgos): se reconocen salvo que
    el test diga lo contrario."""
    values = {"by": DIRECTION, "acknowledge_incomplete": True} | kwargs
    return record_transition(
        store,
        dossier,
        DossierState.DECLARED,
        role=Role.MANAGEMENT,
        at=at,
        now=now,
        **values,
    )


@pytest.fixture
def docs(tmp_path):
    return docs_world(tmp_path / "ws")
