from datetime import timedelta

import pytest
from conftest import NOW, SHA
from pydantic import ValidationError

from ens_evidence.models import (
    Category,
    Dossier,
    DossierState,
    Readiness,
    Role,
    TransitionError,
    transition,
)
from ens_evidence.models.dossier import CorrectiveMeasure, record_sha256
from ens_evidence.models.readiness import Blocker, BlockerKind, ReadinessResult

READY = ReadinessResult(readiness=Readiness.READY, blockers=[], pending_human_review=False)
READY_PENDING = ReadinessResult(readiness=Readiness.READY, blockers=[], pending_human_review=True)
BLOCKER = Blocker(kind=BlockerKind.MISSING_EVIDENCE, measure_id="m1")
NOT_READY = ReadinessResult(
    readiness=Readiness.NOT_READY, blockers=[BLOCKER], pending_human_review=False
)
MEASURES = [CorrectiveMeasure(description="rotar claves", addressee=Role.SYSTEM_OWNER)]


ELEVATION = {"elevated_to": "Ana Gil", "elevated_at": NOW, "elevation_confirmed": True}


def go(dossier, to_state, role, **kwargs):
    if to_state is DossierState.ANALYZED:
        kwargs = {**ELEVATION, **kwargs}
    if (
        to_state is DossierState.DECLARED
        and dossier.transitions
        and "analysis_sha256" not in kwargs
    ):
        kwargs["analysis_sha256"] = record_sha256(dossier.transitions[-1])
    kwargs.setdefault("at", NOW)
    return transition(dossier, to_state, actor="x", role=role, dossier_sha256=SHA, **kwargs)


def self_assessed(category=Category.BASICA):
    return go(Dossier(category=category), DossierState.SELF_ASSESSED, Role.SYSTEM_ADMINISTRATOR)


def analyzed(category=Category.BASICA, manual=False):
    return go(
        self_assessed(category),
        DossierState.ANALYZED,
        Role.SECURITY_OFFICER,
        conclusions="ok",
        corrective_measures=MEASURES,
        manual_judgment_review=manual,
    )


def dossier_in(state, category=Category.BASICA):
    dossier = Dossier(category=category)
    if state is DossierState.GENERATED:
        return dossier
    dossier = self_assessed(category)
    if state is DossierState.SELF_ASSESSED:
        return dossier
    dossier = analyzed(category)
    if state is DossierState.ANALYZED:
        return dossier
    return go(dossier, DossierState.DECLARED, Role.MANAGEMENT, readiness=READY)


def test_camino_feliz_registra_cada_transicion():
    declared = go(analyzed(manual=True), DossierState.DECLARED, Role.MANAGEMENT, readiness=READY)
    assert declared.state is DossierState.DECLARED
    assert [t.to_state for t in declared.transitions] == list(DossierState)[1:]
    assert all(
        t.dossier_sha256 == SHA and t.actor == "x" and t.at == NOW for t in declared.transitions
    )


def test_transition_no_muta_el_expediente_original():
    original = Dossier(category=Category.BASICA)
    go(original, DossierState.SELF_ASSESSED, Role.SYSTEM_ADMINISTRATOR)
    assert original.state is DossierState.GENERATED
    assert original.transitions == []


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (DossierState.GENERATED, DossierState.ANALYZED),
        (DossierState.GENERATED, DossierState.DECLARED),
        (DossierState.GENERATED, DossierState.GENERATED),
        (DossierState.SELF_ASSESSED, DossierState.DECLARED),
        (DossierState.SELF_ASSESSED, DossierState.SELF_ASSESSED),
        (DossierState.ANALYZED, DossierState.SELF_ASSESSED),
        (DossierState.DECLARED, DossierState.GENERATED),
    ],
)
def test_saltos_y_retrocesos_de_estado_se_rechazan(current, target):
    dossier = dossier_in(current)
    with pytest.raises(TransitionError, match="cannot go"):
        go(
            dossier,
            target,
            Role.SECURITY_OFFICER,
            conclusions="c",
            corrective_measures=MEASURES,
            readiness=READY,
        )


def test_desde_declared_no_hay_siguiente_estado():
    dossier = dossier_in(DossierState.DECLARED)
    for target in DossierState:
        with pytest.raises(TransitionError):
            go(dossier, target, Role.MANAGEMENT, readiness=READY)


@pytest.mark.parametrize("role", [Role.SECURITY_OFFICER, Role.SYSTEM_OWNER])
def test_self_assessed_exige_administrador_o_delegado(role):
    with pytest.raises(TransitionError, match="SELF_ASSESSED"):
        go(Dossier(category=Category.BASICA), DossierState.SELF_ASSESSED, role)


def test_self_assessed_admite_al_delegado():
    result = go(
        Dossier(category=Category.BASICA),
        DossierState.SELF_ASSESSED,
        Role.SYSTEM_ADMINISTRATOR_DELEGATE,
    )
    assert result.state is DossierState.SELF_ASSESSED


@pytest.mark.parametrize("role", [Role.SYSTEM_ADMINISTRATOR, Role.SYSTEM_OWNER])
def test_analyzed_exige_responsable_de_la_seguridad(role):
    with pytest.raises(TransitionError, match="security_officer"):
        go(
            self_assessed(),
            DossierState.ANALYZED,
            role,
            conclusions="c",
            corrective_measures=MEASURES,
        )


@pytest.mark.parametrize("conclusions", [None, "", "  "])
def test_analyzed_exige_conclusiones(conclusions):
    with pytest.raises(TransitionError, match="conclusions"):
        go(
            self_assessed(),
            DossierState.ANALYZED,
            Role.SECURITY_OFFICER,
            conclusions=conclusions,
            corrective_measures=MEASURES,
        )


@pytest.mark.parametrize("measures", [None, []])
def test_analyzed_admite_conclusiones_sin_medidas_correctoras(measures):
    result = go(
        self_assessed(),
        DossierState.ANALYZED,
        Role.SECURITY_OFFICER,
        conclusions="c",
        corrective_measures=measures,
    )
    assert result.state is DossierState.ANALYZED
    assert result.transitions[-1].corrective_measures == []


def test_medidas_correctoras_van_dirigidas_al_system_owner():
    with pytest.raises(ValidationError):
        CorrectiveMeasure(description="x", addressee=Role.SECURITY_OFFICER)


@pytest.mark.parametrize("category", [Category.MEDIA, Category.ALTA])
def test_declared_fuera_de_basica_se_rechaza_incluso_con_ready(category):
    with pytest.raises(TransitionError, match="BASICA"):
        go(
            analyzed(category, manual=True),
            DossierState.DECLARED,
            Role.MANAGEMENT,
            readiness=READY,
        )


@pytest.mark.parametrize("readiness", [None, NOT_READY])
def test_declared_exige_readiness_ready(readiness):
    with pytest.raises(TransitionError, match="READY"):
        go(analyzed(manual=True), DossierState.DECLARED, Role.MANAGEMENT, readiness=readiness)


def test_declared_con_pending_human_review_sin_manual_judgment_review_se_rechaza():
    with pytest.raises(TransitionError, match="manual_judgment_review"):
        go(
            analyzed(manual=False),
            DossierState.DECLARED,
            Role.MANAGEMENT,
            readiness=READY_PENDING,
        )


def test_declared_con_pending_human_review_y_manual_judgment_review_se_admite():
    declared = go(
        analyzed(manual=True), DossierState.DECLARED, Role.MANAGEMENT, readiness=READY_PENDING
    )
    assert declared.state is DossierState.DECLARED


def test_declared_sin_pending_human_review_no_exige_manual_judgment_review():
    declared = go(analyzed(manual=False), DossierState.DECLARED, Role.MANAGEMENT, readiness=READY)
    assert declared.state is DossierState.DECLARED


def test_manual_judgment_review_queda_registrado_en_la_transicion_analyzed():
    assert analyzed(manual=True).transitions[-1].manual_judgment_review is True
    assert analyzed(manual=False).transitions[-1].manual_judgment_review is False


def test_transicion_rechazada_no_deja_rastro():
    dossier = Dossier(category=Category.BASICA)
    with pytest.raises(TransitionError):
        go(dossier, DossierState.ANALYZED, Role.SECURITY_OFFICER)
    assert dossier.transitions == []


def test_declared_registra_el_readiness_en_la_transicion():
    declared = go(analyzed(), DossierState.DECLARED, Role.MANAGEMENT, readiness=READY)
    assert declared.transitions[-1].readiness == READY


@pytest.mark.parametrize("category", [Category.MEDIA, Category.ALTA])
def test_construir_declared_fuera_de_basica_falla(category):
    valid = dossier_in(DossierState.DECLARED)
    with pytest.raises(ValidationError, match="BASICA"):
        Dossier(category=category, state=valid.state, transitions=valid.transitions)


def test_construir_declared_sin_historial_falla():
    with pytest.raises(ValidationError, match="does not match"):
        Dossier(category=Category.BASICA, state=DossierState.DECLARED)


@pytest.mark.parametrize("state", [DossierState.SELF_ASSESSED, DossierState.ANALYZED])
def test_construir_estado_sin_historial_falla(state):
    with pytest.raises(ValidationError, match="does not match"):
        Dossier(category=Category.BASICA, state=state)


def test_construir_con_estado_distinto_del_ultimo_to_state_falla():
    valid = self_assessed()
    with pytest.raises(ValidationError, match="does not match"):
        Dossier(
            category=Category.BASICA,
            state=DossierState.ANALYZED,
            transitions=valid.transitions,
        )


def test_construir_con_historial_que_se_salta_un_estado_falla():
    valid = analyzed()
    with pytest.raises(ValidationError, match="cannot go"):
        Dossier(
            category=Category.BASICA,
            state=DossierState.ANALYZED,
            transitions=valid.transitions[1:],
        )


def test_construir_con_historial_que_viola_una_guarda_falla():
    valid = self_assessed()
    record = valid.transitions[0].model_copy(update={"role": Role.SYSTEM_OWNER})
    with pytest.raises(ValidationError, match="SELF_ASSESSED"):
        Dossier(category=Category.BASICA, state=valid.state, transitions=[record])


def test_dossier_valido_hace_round_trip_json():
    declared = dossier_in(DossierState.DECLARED)
    assert Dossier.model_validate_json(declared.model_dump_json()) == declared


def test_ready_con_bloqueos_falla():
    with pytest.raises(ValidationError, match="zero blockers"):
        ReadinessResult(readiness=Readiness.READY, blockers=[BLOCKER], pending_human_review=False)


def test_not_ready_sin_bloqueos_falla():
    with pytest.raises(ValidationError, match="at least one"):
        ReadinessResult(readiness=Readiness.NOT_READY, blockers=[], pending_human_review=False)


def test_transicion_con_huella_ajena_se_rechaza():
    with pytest.raises(TransitionError, match="same dossier_sha256"):
        transition(
            self_assessed(),
            DossierState.ANALYZED,
            actor="x",
            role=Role.SECURITY_OFFICER,
            at=NOW,
            dossier_sha256="b" * 64,
            conclusions="ok",
            **ELEVATION,
        )


def test_transicion_anterior_a_la_previa_se_rechaza():
    with pytest.raises(TransitionError, match="precede"):
        go(
            self_assessed(),
            DossierState.ANALYZED,
            Role.SECURITY_OFFICER,
            conclusions="ok",
            at=NOW - timedelta(seconds=1),
        )


def test_transicion_en_el_mismo_instante_se_admite():
    assert analyzed().state is DossierState.ANALYZED


@pytest.mark.parametrize(
    "role", [Role.SYSTEM_OWNER, Role.SECURITY_OFFICER, Role.SYSTEM_ADMINISTRATOR]
)
def test_declared_exige_direccion(role):
    with pytest.raises(TransitionError, match="management"):
        go(analyzed(manual=True), DossierState.DECLARED, role, readiness=READY)


@pytest.mark.parametrize("sha", [None, "c" * 64])
def test_declared_exige_analysis_sha256_del_registro_analyzed(sha):
    with pytest.raises(TransitionError, match="analysis_sha256"):
        go(
            analyzed(manual=True),
            DossierState.DECLARED,
            Role.MANAGEMENT,
            readiness=READY,
            analysis_sha256=sha,
        )


def test_declared_registra_analysis_sha256_y_evidencia_opcional():
    declared = go(
        analyzed(),
        DossierState.DECLARED,
        Role.MANAGEMENT,
        readiness=READY,
        declaration_evidence_id="ev-1",
    )
    last = declared.transitions[-1]
    assert last.analysis_sha256 == record_sha256(declared.transitions[1])
    assert last.declaration_evidence_id == "ev-1"
    assert dossier_in(DossierState.DECLARED).transitions[-1].declaration_evidence_id is None


@pytest.mark.parametrize(
    "elevation",
    [
        {},
        {"elevated_to": "Ana Gil", "elevated_at": NOW},
        {"elevated_to": " ", "elevated_at": NOW, "elevation_confirmed": True},
        {"elevated_to": "Ana Gil", "elevation_confirmed": True},
    ],
)
def test_analyzed_exige_elevacion_confirmada(elevation):
    with pytest.raises(TransitionError, match="elevat"):
        transition(
            self_assessed(),
            DossierState.ANALYZED,
            actor="x",
            role=Role.SECURITY_OFFICER,
            at=NOW,
            dossier_sha256=SHA,
            conclusions="ok",
            **elevation,
        )


def test_analyzed_sin_medidas_correctoras_registra_la_elevacion():
    result = go(self_assessed(), DossierState.ANALYZED, Role.SECURITY_OFFICER, conclusions="ok")
    last = result.transitions[-1]
    assert last.corrective_measures == []
    assert (last.elevated_to, last.elevated_at, last.elevation_confirmed) == ("Ana Gil", NOW, True)


def test_historial_falsificado_no_deserializa():
    valid = dossier_in(DossierState.DECLARED).model_dump(mode="json")
    forged_sha = {**valid, "transitions": [dict(t) for t in valid["transitions"]]}
    forged_sha["transitions"][1]["dossier_sha256"] = "b" * 64
    backwards = {**valid, "transitions": [dict(t) for t in valid["transitions"]]}
    backwards["transitions"][1]["at"] = (NOW - timedelta(days=1)).isoformat()
    swapped = {**valid, "transitions": [dict(t) for t in valid["transitions"]]}
    swapped["transitions"][1]["conclusions"] = "otro análisis"
    for forged in (forged_sha, backwards, swapped):
        with pytest.raises(ValidationError):
            Dossier.model_validate(forged)


@pytest.mark.parametrize(
    ("state", "role", "field", "value"),
    [
        (DossierState.SELF_ASSESSED, Role.SYSTEM_ADMINISTRATOR, "elevated_to", "Ana"),
        (DossierState.SELF_ASSESSED, Role.SYSTEM_ADMINISTRATOR, "analysis_sha256", SHA),
        (DossierState.SELF_ASSESSED, Role.SYSTEM_ADMINISTRATOR, "declaration_evidence_id", "EV-1"),
    ],
)
def test_un_campo_de_otro_estado_se_rechaza(state, role, field, value):
    with pytest.raises(TransitionError, match=field):
        go(Dossier(category=Category.BASICA), state, role, **{field: value})
