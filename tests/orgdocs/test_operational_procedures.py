"""Procedimientos operativos de BÁSICA en `procedimientos-seguridad`: el documento cubre cláusulas
op./mp. y se cita junto a la medida, pero cubrir una cláusula nunca suple su evidencia."""

from datetime import timedelta

import pytest
from assessment.dossier.stages import AFTER, ID, basic, restore, run_at
from matrix.acceptance_helpers import APPROVER
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier.store import load_snapshot
from ens_evidence.catalog import load_catalog
from ens_evidence.models.preaudit import DeterministicRule
from ens_evidence.orgdocs import approve_document, load_templates
from ens_evidence.preaudit.deterministic import deterministic_findings
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload
from ens_evidence.workspace import LocalStore

from .orgdocs_helpers import SIGNATORY, traceability_rows

DOCUMENT = "procedimientos-seguridad"
OPERATIONAL_COVERS = {
    "info_acceso": {"org.3.4", "op.acc.2.2", "op.acc.4.4"},
    "info_copias": {"org.3.4", "mp.info.6.1", "mp.info.6.2"},
    "analisis_riesgos": {"op.pl.1.intro"},
    "inventario_activos": {"op.exp.1.1"},
    "gestion_cuentas": {"op.acc.1.3", "op.acc.1.4"},
    "credenciales": {"op.acc.6.1", "op.acc.6.2", "op.acc.6.4", "op.acc.6.5", "op.acc.6.6"},
    "actualizaciones": {"op.exp.4.1", "op.exp.4.2", "op.exp.4.3"},
    "codigo_danino": {"op.exp.6.1", "op.exp.6.3"},
    "registro_actividad": {"op.exp.8.1"},
    "perimetro": {"mp.com.1.2"},
    "puesta_en_produccion": {"mp.sw.2.intro", "mp.sw.2.1"},
}
MEASURE = "op.exp.4"


def template():
    return next(t for t in load_templates() if t.id == DOCUMENT)


def test_cada_procedimiento_operativo_cubre_sus_clausulas_oficiales():
    sections = {s.id: set(s.covers) for s in template().template.sections}

    assert {s: sections.get(s) for s in OPERATIONAL_COVERS} == OPERATIONAL_COVERS


def test_los_procedimientos_operativos_son_de_medidas_aplicables_en_basica():
    catalog = load_catalog()
    measures = {
        clause.rsplit(".", 1)[0] for covers in OPERATIONAL_COVERS.values() for clause in covers
    } - {"org.3"}

    for measure_id in measures:
        card = catalog.get(measure_id)
        level = "BASICA" if card.applies_by.value == "category" else "BAJO"
        assert not card.levels[level].not_applicable, measure_id


def test_el_documento_generado_muestra_cada_procedimiento_y_su_trazabilidad(built):
    document = built.read_text(f"org/docs/{DOCUMENT}.md")
    titles = {s.id: s.title for s in template().template.sections}

    rows = {(ref.rsplit(", ", 1)[-1], section) for ref, _, section in traceability_rows(document)}

    for section, covers in OPERATIONAL_COVERS.items():
        assert f"## {titles[section]}" in document
        for clause in covers:
            label = (
                f"{clause.removesuffix('.intro')} (introducción)"
                if clause.endswith(".intro")
                else clause
            )
            assert (label, titles[section]) in rows, (section, clause)
    assert "Consola de actualizaciones" in document


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def approved_without_evidence(stages, tmp_path, monkeypatch):
    """Expediente BÁSICA con los procedimientos aprobados y `op.exp.4` sin ninguna evidencia."""
    world = restore(stages, "generated", tmp_path, monkeypatch)
    role = APPROVER[DOCUMENT]
    approve_document(world.store, DOCUMENT, SIGNATORY[role], role, NOW - timedelta(hours=1))
    unlinked = run_at(world, "matrix", "unlink", MEASURE, "--evidence", _evidence_of(stages))
    assert unlinked.exit_code == 0, unlinked.output
    generated = run_at(world, "assessment", "dossier", "generate", at=AFTER)
    assert generated.exit_code == 0, generated.output
    return world.store, ID.search(generated.output).group()


def _evidence_of(stages) -> str:
    """La única evidencia que el expediente de partida enlaza a la medida."""
    store = LocalStore(stages.trees["generated"] / "ws")
    payload = build_payload(store, stages.dossier, load_pack())
    (evidence,) = next(m for m in payload.data["dossier"]["measures"] if m["id"] == MEASURE)[
        "evidence"
    ]
    return evidence


def test_el_documento_aprobado_llega_al_payload_como_documento_de_la_medida(
    approved_without_evidence,
):
    store, dossier = approved_without_evidence

    content = build_payload(store, dossier, load_pack()).data["dossier"]

    measure = next(m for m in content["measures"] if m["id"] == MEASURE)
    link = next(ln for ln in content["links"] if ln["measure"] == MEASURE and ln["ref"] == DOCUMENT)
    assert measure["documents"] == [DOCUMENT] and measure["evidence"] == []
    assert link["kind"] == "document" and link["via"] == ["template"]
    assert set(link["covers"]) == OPERATIONAL_COVERS["actualizaciones"]


def test_cubrir_la_clausula_con_el_documento_no_suple_la_evidencia(approved_without_evidence):
    store, dossier = approved_without_evidence

    findings = deterministic_findings(load_snapshot(store, dossier))

    missing = {
        f.refs[0].id.rsplit(":", 1)[-1]
        for f in findings
        if f.measure == MEASURE and f.criterion == DeterministicRule.EVIDENCE_MISSING
    }
    assert OPERATIONAL_COVERS["actualizaciones"] <= missing
