import pytest
from conftest import SHA

from ens_evidence.categorization import (
    CategorizationNotCurrent,
    DossierCategoryMismatch,
    approve_categorization,
    open_dossier,
    require_dossier_category,
)
from ens_evidence.models import Dossier, DossierState, Readiness, Role, TransitionError, transition
from ens_evidence.models.enums import Category
from ens_evidence.models.readiness import ReadinessResult

from .helpers import NOW, write_category

READY = ReadinessResult(readiness=Readiness.READY, blockers=[], pending_human_review=False)


def approved(store, level):
    write_category(store, level)
    approve_categorization(store, "Luis Pardo", NOW)


def test_categorizacion_media_aprobada_abre_un_expediente_media_preparado_para_certificacion(store):
    approved(store, "MEDIO")

    opened = open_dossier(store, NOW)

    assert opened.dossier.category is Category.MEDIA
    assert opened.commitment == "preparado para certificación"
    assert "no certifica" in opened.notice


def test_el_expediente_media_no_admite_declared(store):
    approved(store, "MEDIO")
    dossier = open_dossier(store, NOW).dossier

    with pytest.raises(TransitionError):
        dossier = transition(
            dossier,
            DossierState.SELF_ASSESSED,
            actor="x",
            role=Role.SYSTEM_ADMINISTRATOR,
            at=NOW,
            dossier_sha256=SHA,
        )
        transition(
            dossier,
            DossierState.DECLARED,
            actor="x",
            role=Role.SECURITY_OFFICER,
            at=NOW,
            dossier_sha256=SHA,
            readiness=READY,
        )


def test_un_expediente_de_otra_categoria_se_rechaza(store):
    approved(store, "MEDIO")

    with pytest.raises(DossierCategoryMismatch, match="BASICA.*MEDIA"):
        require_dossier_category(Dossier(category=Category.BASICA), store, NOW)


def test_un_expediente_de_la_categoria_aprobada_devuelve_su_compromiso(store):
    approved(store, "BAJO")

    assert (
        require_dossier_category(Dossier(category=Category.BASICA), store, NOW) == "autoevaluación"
    )


def test_sin_categorizacion_vigente_no_se_abre_expediente(store):
    write_category(store, "MEDIO")

    with pytest.raises(CategorizationNotCurrent):
        open_dossier(store, NOW)
