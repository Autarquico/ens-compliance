"""Las 11 secciones: fuentes reales (clave, huella y marcas de tiempo), sin veredictos."""

import hashlib

import pytest
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier import build_snapshot
from ens_evidence.assessment.dossier.methodology import METHODOLOGY_VERSION, methodology_sha256
from ens_evidence.assessment.dossier.model import SECTION_TITLES, SectionState
from ens_evidence.assessment.dossier.sections import PRE_AUDIT_PENDING

from .worlds import INPUTS, add_risk_analysis


@pytest.fixture
def snapshot(ready):
    add_risk_analysis(ready.store)
    return build_snapshot(ready.store, INPUTS)


def by_kind(section, kind):
    return [s for s in section.sources if s.kind == kind]


def test_hay_once_secciones_con_el_titulo_de_la_guia(snapshot):
    assert [(s.number, s.title) for s in snapshot.sections] == list(
        enumerate(SECTION_TITLES, start=1)
    )


def test_cada_fuente_existe_en_el_workspace_y_su_huella_coincide(ready, snapshot):
    sources = [(s.number, source) for s in snapshot.sections for source in s.sources]

    assert sources
    for number, source in sources:
        assert ready.store.exists(source.key), (number, source.key)
        assert source.sha256 == hashlib.sha256(ready.store.read_bytes(source.key)).hexdigest()
        assert source.timestamps


def test_las_fuentes_de_cada_seccion_son_las_de_su_area(snapshot):
    sections = {s.number: s for s in snapshot.sections}

    assert {s.key for s in sections[2].sources} >= {"scope/assets.yaml", "scope/approval.yaml"}
    assert {s.key for s in sections[3].sources} == {
        "scope/categorization.yaml",
        "scope/categorization-approval.yaml",
    }
    assert {s.key for s in sections[4].sources} == {
        "risk/methodology.md",
        "risk/methodology.yaml",
        "risk/register.yaml",
        "risk/approval.yaml",
    }
    assert {s.key for s in sections[5].sources} == {
        "controls/applicability.yaml",
        "controls/measures.yaml",
    }
    assert [s.key for s in sections[7].sources] == ["controls/matrix.yaml"]
    assert sections[6].sources == []


@pytest.mark.parametrize(
    "kind",
    [
        "evidence-manifest",
        "check-evidence",
        "review",
        "org-document",
        "check-run",
        "incident-record",
    ],
)
def test_la_seccion_de_evidencias_cita_cada_tipo_de_fuente(snapshot, kind):
    assert by_kind(snapshot.sections[7], kind)


def test_cada_revision_de_la_matriz_figura_como_fuente(snapshot):
    rows = snapshot.sections[6].content["matrix"]["rows"]
    keys = {r["last_review"]["key"] for r in rows if r["last_review"]}

    assert keys <= {s.key for s in by_kind(snapshot.sections[7], "review")}
    assert keys


def test_la_marca_de_una_aprobacion_es_la_de_su_firma(snapshot):
    categorization = snapshot.sections[2]

    assert categorization.content["approval"]["approved_at"] == (
        categorization.sources[0].timestamps[0].isoformat().replace("+00:00", "Z")
    )


def test_la_metodologia_lleva_su_version_y_huella(snapshot):
    content = snapshot.sections[5].content

    assert content["version"] == METHODOLOGY_VERSION
    assert content["sha256"] == methodology_sha256()
    assert "anexo III 2.1.b" in content["text"]


def test_riesgos_aprobados_quedan_registrados(snapshot):
    risks = snapshot.sections[3]

    assert risks.state is SectionState.RECORDED
    assert risks.content["register"]["risks"][0]["id"] == "R-001"


def test_sin_analisis_de_riesgos_la_seccion_queda_incompleta(ready):
    section = build_snapshot(ready.store, INPUTS).sections[3]

    assert section.state is SectionState.INCOMPLETE
    assert section.content["register"] is None
    assert section.content["validation_errors"]


def test_pre_auditoria_conclusiones_y_aprobacion_quedan_pendientes(snapshot):
    treatment, conclusions, approval = snapshot.sections[8:]

    assert treatment.content["pre_audit"] == PRE_AUDIT_PENDING
    assert PRE_AUDIT_PENDING == "pre-auditoría no ejecutada; revisión humana pendiente"
    for section in (treatment, conclusions, approval):
        assert section.state is SectionState.PENDING_HUMAN_REVIEW
    assert conclusions.content["conclusions"] is None
    assert approval.content["declared"] is None


def test_el_plan_de_tratamiento_aparece_en_la_seccion_9(snapshot):
    plan = snapshot.sections[8].content["treatment_plan"]

    assert plan["actions"][0]["id"] == "A-001"


def test_los_incidentes_no_exportan_su_texto_libre(snapshot):
    incidents = snapshot.sections[7].content["incidents"]

    assert incidents
    assert all(
        set(i) == {"id", "simulated", "status", "detected_at", "records", "problems"}
        for i in incidents
    )


def test_ninguna_seccion_dice_conforme(snapshot):
    texts = snapshot.model_dump_json().lower()

    assert snapshot.commitment == "La herramienta no declara conformidad."
    assert 'state":"conforme' not in texts
    assert NOW  # el mundo está congelado en NOW
