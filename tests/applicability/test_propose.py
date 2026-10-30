from datetime import timedelta

import pytest
import yaml

from ens_evidence.applicability import propose_applicability, validate_applicability
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import init_measures, load_measures, save_measures
from ens_evidence.categorization import (
    CATEGORIZATION_KEY,
    CategorizationNotCurrent,
    CategorizationState,
)
from ens_evidence.models.contracts import Review
from ens_evidence.models.enums import Applicability, MeasureAssessment, Role
from ens_evidence.scope import ScopeNotApproved, revoke_approval
from ens_evidence.workspace import LocalStore

from .helpers import (
    LATER,
    NOW,
    categorize,
    decide,
    measures_by_id,
    prepared_workspace,
    seed_scope,
    uniform,
)

MEASURES_KEY = "controls/measures.yaml"


def required_in_media():
    return {
        card.id
        for card in load_catalog().measures
        if not card.levels[
            "MEDIA" if card.applies_by.value == "category" else "MEDIO"
        ].not_applicable
    }


def test_propose_exige_el_alcance_aprobado(workspace):
    revoke_approval(workspace, "cambio de alcance", NOW)

    with pytest.raises(ScopeNotApproved):
        propose_applicability(workspace, LATER)


def test_propose_exige_la_categorizacion_aprobada(tmp_path):
    store = LocalStore(tmp_path / "ws")
    seed_scope(store)
    init_measures(store)

    with pytest.raises(CategorizationNotCurrent) as raised:
        propose_applicability(store, LATER)

    assert raised.value.status.state is CategorizationState.INVALID


def test_propose_rechaza_una_categorizacion_modificada_tras_aprobarse(workspace):
    text = workspace.read_text(CATEGORIZATION_KEY)
    workspace.write_text(CATEGORIZATION_KEY, text.replace("MEDIO", "ALTO", 1))

    with pytest.raises(CategorizationNotCurrent) as raised:
        propose_applicability(workspace, LATER)

    assert raised.value.status.state is CategorizationState.STALE


def test_propose_rechaza_una_categorizacion_caducada(workspace):
    with pytest.raises(CategorizationNotCurrent) as raised:
        propose_applicability(workspace, NOW + timedelta(days=366))

    assert raised.value.status.state is CategorizationState.EXPIRED


def test_propose_marca_applicable_exactamente_lo_que_exige_el_anexo_ii(workspace):
    result = propose_applicability(workspace, LATER)

    measures = measures_by_id(workspace)
    applicable = {i for i, m in measures.items() if m.applicability is Applicability.APPLICABLE}
    assert applicable == required_in_media()
    assert set(result.proposed) == applicable
    assert all(measures[i].justification for i in applicable)


def test_propose_nunca_marca_not_applicable_ni_crea_revisiones(workspace):
    propose_applicability(workspace, LATER)

    measures = measures_by_id(workspace).values()
    assert not [m for m in measures if m.applicability is Applicability.NOT_APPLICABLE]
    assert all(m.review is None for m in measures)
    assert {m.assessment for m in measures} == {MeasureAssessment.NOT_ASSESSED}
    assert all(not m.evidence for m in measures)


def test_propose_deja_sin_determinar_lo_que_el_anexo_no_exige(workspace):
    propose_applicability(workspace, LATER)

    undetermined = {
        i
        for i, m in measures_by_id(workspace).items()
        if m.applicability is Applicability.UNDETERMINED
    }
    assert undetermined == {card.id for card in load_catalog().measures} - required_in_media()
    assert undetermined


def test_propose_no_sobrescribe_las_decisiones_humanas(workspace):
    review = Review(
        reviewer="Luis Pardo",
        role=Role.SECURITY_OFFICER,
        reviewed_at=NOW,
        valid_until=NOW + timedelta(days=90),
        conclusion="Revisada.",
    )
    decide(workspace, "op.pl.1", Applicability.APPLICABLE, "Texto escrito por la organización.")
    decide(workspace, "op.cont.2", Applicability.NOT_APPLICABLE, "No hay continuidad que exigir.")
    decide(workspace, "org.1", Applicability.APPLICABLE, "Con revisión.", review=review)
    before = {i: measures_by_id(workspace)[i] for i in ("op.pl.1", "op.cont.2", "org.1")}

    propose_applicability(workspace, LATER)

    after = measures_by_id(workspace)
    for measure_id in ("op.cont.2", "org.1"):
        assert after[measure_id].model_dump(exclude={"selection"}) == before[measure_id].model_dump(
            exclude={"selection"}
        )
    assert after["op.pl.1"].justification == "Texto escrito por la organización."
    assert after["op.pl.1"].applicability is Applicability.APPLICABLE


def test_propose_repetido_es_idempotente(workspace):
    propose_applicability(workspace, LATER)
    before = workspace.read_text(MEASURES_KEY)

    second = propose_applicability(workspace, LATER)

    assert workspace.read_text(MEASURES_KEY) == before
    assert not (second.proposed or second.refreshed or second.retracted or second.pending_review)


def test_si_sube_la_categoria_las_propuestas_se_actualizan_y_las_humanas_quedan_pendientes(
    workspace,
):
    decide(workspace, "op.pl.1", Applicability.APPLICABLE, "Texto escrito por la organización.")
    propose_applicability(workspace, LATER)

    categorize(workspace, uniform("ALTO"), at=LATER)
    result = propose_applicability(workspace, LATER)

    assert "op.pl.1" in result.pending_review
    assert "op.pl.1" not in result.refreshed
    assert result.refreshed
    assert (
        measures_by_id(workspace)["op.pl.1"].justification == "Texto escrito por la organización."
    )


def test_si_baja_la_categoria_se_retiran_las_propuestas_que_ya_no_se_exigen(tmp_path):
    store = prepared_workspace(tmp_path / "ws", uniform("ALTO"))
    propose_applicability(store, LATER)

    categorize(store, uniform("BAJO"), at=LATER)
    result = propose_applicability(store, LATER)

    measures = measures_by_id(store)
    assert result.retracted
    assert all(measures[i].applicability is Applicability.UNDETERMINED for i in result.retracted)
    assert all(measures[i].justification is None for i in result.retracted)


def test_propose_cli_informa_de_que_es_una_seleccion_y_no_una_conformidad(workspace, ens_cli):
    result = ens_cli("applicability", "propose")

    assert result.exit_code == 0, result.output
    assert "no una conformidad" in result.output
    assert yaml.safe_load(workspace.read_text(MEASURES_KEY))["measures"]


def test_propose_no_pone_fundamento_a_una_decision_humana_sin_el(workspace):
    decide(
        workspace, "op.pl.1", Applicability.APPLICABLE, "Texto de la organización.", selection=None
    )

    result = propose_applicability(workspace, LATER)

    assert "op.pl.1" in result.pending_review
    assert measures_by_id(workspace)["op.pl.1"].selection is None


def test_confirm_fija_el_fundamento_vigente_de_una_decision_humana(workspace):
    decide(
        workspace, "op.pl.1", Applicability.APPLICABLE, "Texto de la organización.", selection=None
    )

    result = propose_applicability(workspace, LATER, confirm=["op.pl.1"])

    assert result.confirmed == ["op.pl.1"]
    measure = measures_by_id(workspace)["op.pl.1"]
    assert measure.selection is not None
    assert measure.justification == "Texto de la organización."
    assert not any("op.pl.1" in e for e in validate_applicability(workspace, LATER).errors)


def test_una_justificacion_humana_igual_al_texto_propuesto_no_se_trata_como_propuesta(workspace):
    propose_applicability(workspace, LATER)
    proposed_text = measures_by_id(workspace)["op.pl.1"].justification
    decide(workspace, "op.pl.1", Applicability.APPLICABLE, proposed_text)

    categorize(workspace, uniform("ALTO"), at=LATER)
    result = propose_applicability(workspace, LATER)

    assert "op.pl.1" in result.pending_review
    assert "op.pl.1" not in result.refreshed


def edit_justification(store, measure_id, text, **extra):
    document = load_measures(store)
    updated = [
        m.model_copy(update={"justification": text, **extra}) if m.id == measure_id else m
        for m in document.measures
    ]
    save_measures(store, document.model_copy(update={"measures": updated}))


def test_una_propuesta_editada_pasa_a_humana_y_no_se_reescribe_al_cambiar_la_categoria(workspace):
    propose_applicability(workspace, LATER)
    edit_justification(workspace, "org.1", "Texto humano")

    categorize(workspace, uniform("ALTO"), at=LATER)
    result = propose_applicability(workspace, LATER)

    assert "org.1" in result.pending_review
    assert "org.1" not in result.refreshed
    assert measures_by_id(workspace)["org.1"].justification == "Texto humano"


def test_una_propuesta_editada_no_se_retira_al_bajar_la_categoria(tmp_path):
    store = prepared_workspace(tmp_path / "ws", uniform("ALTO"))
    propose_applicability(store, LATER)
    edit_justification(store, "mp.eq.3", "Texto humano")

    categorize(store, uniform("BAJO"), at=LATER)
    result = propose_applicability(store, LATER)

    assert "mp.eq.3" not in result.retracted
    assert measures_by_id(store)["mp.eq.3"].justification == "Texto humano"


def test_confirmar_una_propuesta_con_revision_la_hace_humana_y_validate_no_se_queja(workspace):
    propose_applicability(workspace, LATER)
    review = Review(
        reviewer="Luis Pardo",
        role=Role.SECURITY_OFFICER,
        reviewed_at=LATER,
        valid_until=LATER + timedelta(days=30),
        conclusion="Revisada.",
    )
    edit_justification(
        workspace, "org.1", measures_by_id(workspace)["org.1"].justification, review=review
    )
    categorize(workspace, uniform("ALTO"), at=LATER)

    result = propose_applicability(workspace, LATER, confirm=["org.1"])

    assert result.confirmed == ["org.1"]
    assert measures_by_id(workspace)["org.1"].proposed is False
    assert not any("org.1" in e for e in validate_applicability(workspace, LATER).errors)


def test_el_error_de_una_propuesta_editada_solo_sugiere_quitar_proposed(workspace):
    propose_applicability(workspace, LATER)
    edit_justification(workspace, "org.1", "Texto humano")

    errors = [e for e in validate_applicability(workspace, LATER).errors if "org.1" in e]

    assert len(errors) == 1
    assert "quita `proposed`" in errors[0]
    assert "applicability propose`" not in errors[0]


@pytest.mark.parametrize("ids", [["no.existe"], ["org.1"]])
def test_confirm_de_un_id_inexistente_o_sin_revision_pendiente_se_informa(workspace, ids):
    propose_applicability(workspace, LATER)

    result = propose_applicability(workspace, LATER, confirm=ids)

    assert result.confirmed == []
    assert result.unconfirmed == ids


def test_propose_cli_lista_las_medidas_que_no_pudo_confirmar(workspace, ens_cli):
    result = ens_cli("applicability", "propose", "--confirm", "op.pl1")

    assert result.exit_code == 0, result.output
    assert "No confirmadas: op.pl1" in result.output


def test_propose_cli_confirma_las_decisiones_que_se_le_indican(workspace, ens_cli):
    decide(
        workspace, "op.pl.1", Applicability.APPLICABLE, "Texto de la organización.", selection=None
    )

    result = ens_cli("applicability", "propose", "--confirm", "op.pl.1")

    assert result.exit_code == 0, result.output
    assert "confirmadas 1" in result.output
