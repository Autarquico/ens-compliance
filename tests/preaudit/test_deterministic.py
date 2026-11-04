"""Capa determinista sobre mundos reales del expediente: tabla de decisión y fuentes."""

from datetime import timedelta

import pytest
import yaml
from assessment.dossier.worlds import INPUTS
from checks.acceptance_helpers import COLLECTED, make_world
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier import build_snapshot, current_inputs
from ens_evidence.assessment.dossier.model import DossierSnapshot, snapshot_export
from ens_evidence.matrix.messages import blocker_text
from ens_evidence.models.preaudit import (
    PROPOSAL_NOTICE,
    Classification,
    DeterministicRule,
    Layer,
    RefKind,
)
from ens_evidence.models.readiness import Blocker, BlockerKind
from ens_evidence.preaudit.deterministic import (
    DOSSIER_SCOPE,
    RULE_LABELS,
    deterministic_findings,
)

CHECK_MEASURE = "op.exp.1"
CHECK = "github.secret_scanning_enabled"


@pytest.fixture
def ready(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "ready")


def failing_world(world) -> DossierSnapshot:
    world.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    world.check_all(NOW - timedelta(minutes=45))
    return build_snapshot(world.store, INPUTS)


def by_rule(findings, rule):
    return [f for f in findings if f.criterion == rule]


def with_matrix(snapshot: DossierSnapshot, change) -> DossierSnapshot:
    data = snapshot.model_dump(mode="json")
    change(data["sections"][6]["content"]["matrix"])
    return DossierSnapshot.model_validate(data)


def with_blocker(snapshot: DossierSnapshot, kind: str, detail: str, measure="op.pl.1"):
    blocker = {"kind": kind, "measure_id": measure, "detail": detail}
    return with_matrix(snapshot, lambda matrix: matrix["blockers"].append(blocker))


def test_un_expediente_preparado_no_genera_hallazgos(ready):
    assert deterministic_findings(build_snapshot(ready.store, INPUTS)) == []


def test_un_check_fallido_se_propone_como_menor_citando_its_e_ic(ready):
    findings = deterministic_findings(failing_world(ready))

    [fail] = by_rule(findings, DeterministicRule.CHECK_FAIL)
    assert fail.layer is Layer.DETERMINISTIC
    assert fail.proposal is Classification.NC_MENOR
    assert fail.measure == CHECK_MEASURE
    assert CHECK in fail.refs[0].id
    assert fail.refs[0].kind is RefKind.SECTION
    assert "ITS de Auditoría (BOE-A-2018-4573) §VI.2" in fail.source
    assert "IC-01/19" in fail.source
    assert PROPOSAL_NOTICE not in fail.reason  # el aviso va en su propia línea, una vez


def test_la_falta_de_evidencia_derivada_del_fallo_queda_sin_clasificar(ready):
    findings = deterministic_findings(failing_world(ready))

    missing = by_rule(findings, DeterministicRule.EVIDENCE_MISSING)
    assert missing
    assert {f.proposal for f in missing} == {Classification.SIN_CLASIFICAR}
    assert all("persona" in f.source for f in missing)


def test_solo_el_check_fallido_es_una_no_conformidad_propuesta(ready):
    findings = deterministic_findings(failing_world(ready))

    proposed = {f.proposal for f in findings}
    assert proposed == {Classification.NC_MENOR, Classification.SIN_CLASIFICAR}
    assert Classification.NC_MAYOR not in proposed


def test_la_evidencia_caducada_queda_sin_clasificar_y_cita_la_evidencia(ready):
    snapshot = build_snapshot(ready.store, current_inputs(NOW + timedelta(days=31)))

    expired = by_rule(deterministic_findings(snapshot), DeterministicRule.EVIDENCE_EXPIRED)

    assert expired
    assert {f.proposal for f in expired} == {Classification.SIN_CLASIFICAR}
    assert all(any(r.kind is RefKind.EVIDENCE for r in f.refs) for f in expired)


def test_un_artefacto_alterado_es_hash_que_no_cuadra(ready):
    manifest = yaml.safe_load(ready.store.read_text(ready.collector_manifest()))
    ready.store.write_bytes(manifest["artifact"]["key"], b"alterado")

    findings = deterministic_findings(build_snapshot(ready.store, INPUTS))

    mismatch = by_rule(findings, DeterministicRule.HASH_MISMATCH)
    assert mismatch
    assert {f.proposal for f in mismatch} == {Classification.SIN_CLASIFICAR}


def test_un_check_que_no_pudo_valorar_queda_sin_clasificar(ready):
    snapshot = with_blocker(
        build_snapshot(ready.store, INPUTS),
        "MISSING_EVIDENCE",
        f"check_unknown_subject:{CHECK}:infra",
    )

    [unknown] = deterministic_findings(snapshot)

    assert unknown.criterion == DeterministicRule.CHECK_UNKNOWN
    assert unknown.proposal is Classification.SIN_CLASIFICAR


def test_los_bloqueos_fuera_de_la_tabla_no_generan_hallazgo(ready):
    snapshot = build_snapshot(ready.store, INPUTS)
    for kind, detail in (
        ("NOT_COMPLIANT", "NOT_ASSESSED"),
        ("MISSING_REVIEW", "review_missing"),
        ("EXPIRED_REVIEW", ""),
        ("OPEN_FINDING", "x"),
    ):
        assert deterministic_findings(with_blocker(snapshot, kind, detail)) == []


def test_un_problema_de_integridad_queda_sin_clasificar_con_aviso(ready):
    snapshot = build_snapshot(ready.store, INPUTS)

    [broken] = deterministic_findings(snapshot, integrity=["la cadena de transiciones se rompe"])

    assert broken.criterion == DeterministicRule.DOSSIER_INTEGRITY
    assert broken.proposal is Classification.SIN_CLASIFICAR
    assert broken.measure == DOSSIER_SCOPE
    assert "la cadena de transiciones se rompe" in broken.reason


def test_una_seccion_de_resultados_ilegible_es_un_hallazgo_de_integridad(ready):
    snapshot = build_snapshot(ready.store, INPUTS)
    unreadable = with_matrix(snapshot, lambda matrix: matrix.pop("rows"))

    [broken] = deterministic_findings(unreadable)

    assert broken.criterion == DeterministicRule.DOSSIER_INTEGRITY


def test_los_ficheros_ilegibles_de_la_matriz_son_integridad(ready):
    snapshot = with_matrix(
        build_snapshot(ready.store, INPUTS), lambda m: m["errors"].append("controls/matrix.yaml")
    )

    [broken] = deterministic_findings(snapshot)

    assert "controls/matrix.yaml" in broken.reason


def test_es_determinista_y_no_toca_la_instantanea(ready):
    snapshot = failing_world(ready)
    before = snapshot.model_dump_json()

    first = deterministic_findings(snapshot)
    second = deterministic_findings(snapshot)

    assert [f.model_dump_json() for f in first] == [f.model_dump_json() for f in second]
    assert snapshot.model_dump_json() == before
    assert len({f.id for f in first}) == len(first)


def test_el_motivo_es_el_texto_llano_del_bloqueo_con_su_siguiente_paso(ready):
    snapshot = failing_world(ready)
    texts = {blocker_text(b) for b in snapshot_export(snapshot).blockers}

    findings = deterministic_findings(snapshot)

    assert findings and all(f.reason in texts for f in findings)
    [fail] = by_rule(findings, DeterministicRule.CHECK_FAIL)
    assert "Siguiente paso" in fail.reason and "check_failed" not in fail.reason


def test_un_documento_sin_aprobar_no_se_explica_como_falta_de_evidencia(ready):
    detail = "document_not_approved:politica-seguridad:GENERATED"
    snapshot = with_blocker(build_snapshot(ready.store, INPUTS), "EXPIRED_EVIDENCE", detail)

    [finding] = deterministic_findings(snapshot)

    expected = Blocker(kind=BlockerKind.EXPIRED_EVIDENCE, measure_id="op.pl.1", detail=detail)
    assert finding.reason == blocker_text(expected)
    assert "docs approve politica-seguridad" in finding.reason
    assert "falta evidencia" not in finding.reason and detail not in finding.reason


def test_cada_regla_tiene_su_etiqueta_en_castellano():
    assert set(RULE_LABELS) == set(DeterministicRule)
    assert all(label != rule.value for rule, label in RULE_LABELS.items())
