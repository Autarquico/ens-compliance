"""Tabla de decisión y agrupación §54 (IC-01/19) sobre medidas reales del catálogo."""

import pytest

from ens_evidence.catalog import load_catalog
from ens_evidence.models.preaudit import (
    DETERMINISTIC_PROPOSAL,
    Classification,
    DeterministicRule,
    Finding,
    Layer,
    Ref,
    RefKind,
    finding_id,
    group_of,
)
from ens_evidence.preaudit.classify import (
    GROUPING_SOURCE,
    group_findings,
    proposal_for,
    source_for,
)

MENOR, MAYOR, OBS = Classification.NC_MENOR, Classification.NC_MAYOR, Classification.OBSERVACION


def judgment(measure: str, proposal: Classification, n: int = 1) -> Finding:
    refs = [Ref(kind=RefKind.DOCUMENT, id=f"doc-{n}")]
    criterion = f"criterio-{n}"
    return Finding(
        id=finding_id(criterion, measure, refs),
        layer=Layer.JUDGMENT,
        proposal=proposal,
        measure=measure,
        refs=refs,
        criterion=criterion,
        reason="motivo; propuesta; la decide una persona",
        quote="cita",
        source="fuente",
        classification_source="fuente de la clase",
    )


def build(spec: list[tuple[str, Classification]]) -> list[Finding]:
    return [judgment(measure, proposal, i) for i, (measure, proposal) in enumerate(spec)]


# (caso, hallazgos, propuestas esperadas como (propuesta, medidas))
CASES = [
    ("una sola menor no se agrupa", [("op.pl.1", MENOR)], []),
    ("dos menores de una medida: una menor", [("op.pl.1", MENOR)] * 2, [(MENOR, ["op.pl.1"])]),
    (
        "menores de dos medidas de un grupo: mayor",
        [("op.pl.1", MENOR), ("op.pl.2", MENOR)],
        [(MAYOR, ["op.pl.1", "op.pl.2"])],
    ),
    ("menores de grupos distintos: nada", [("op.pl.1", MENOR), ("op.acc.1", MENOR)], []),
    (
        "org y op no se mezclan",
        [("org.1", MENOR), ("org.2", MENOR), ("op.pl.1", MENOR)],
        [(MAYOR, ["org.1", "org.2"])],
    ),
    ("las mayores nunca se agrupan", [("op.pl.1", MAYOR), ("op.pl.2", MAYOR)], []),
    ("una mayor y una menor no se agrupan", [("op.pl.1", MAYOR), ("op.pl.2", MENOR)], []),
    ("las observaciones no se agrupan", [("op.pl.1", OBS), ("op.pl.2", OBS)], []),
    (
        "mayor por grupo y menor por medida a la vez",
        [("op.pl.1", MENOR), ("op.pl.1", MENOR), ("op.pl.2", MENOR), ("op.acc.1", MENOR)],
        [(MENOR, ["op.pl.1"]), (MAYOR, ["op.pl.1", "op.pl.2"])],
    ),
]


@pytest.mark.parametrize(("name", "spec", "expected"), CASES, ids=[c[0] for c in CASES])
def test_agrupacion_54(name, spec, expected):
    proposals = group_findings(build(spec))

    assert sorted((p.proposal.value, p.measures) for p in proposals) == sorted(
        (e[0].value, e[1]) for e in expected
    )
    assert all(p.source == GROUPING_SOURCE for p in proposals)
    assert all("persona" in p.justification for p in proposals)


def test_conserva_los_hallazgos_originales_y_los_cita():
    findings = build([("op.pl.1", MENOR), ("op.pl.2", MENOR), ("op.acc.1", MENOR)])
    before = [f.model_dump_json() for f in findings]

    [proposal] = group_findings(findings)

    assert [f.model_dump_json() for f in findings] == before
    assert set(proposal.finding_ids) == {findings[0].id, findings[1].id}


def test_el_resultado_no_depende_del_orden():
    findings = build([("op.pl.1", MENOR), ("op.pl.2", MENOR), ("op.pl.1", MENOR)])

    assert group_findings(findings) == group_findings(list(reversed(findings)))


def test_el_grupo_es_el_prefijo_del_catalogo():
    catalog = load_catalog()
    subgroups = {s.id for s in catalog.subgroups}
    for measure in catalog.measures:
        expected = measure.subgroup or measure.group.value
        assert group_of(measure.id) == expected, measure.id
        assert group_of(measure.id) in subgroups | {"org"}


def test_cada_regla_determinista_tiene_propuesta_y_fuente():
    for rule in DeterministicRule:
        assert proposal_for(rule) is DETERMINISTIC_PROPOSAL[rule]
        assert "IC-01/19" in source_for(rule)
    assert proposal_for(DeterministicRule.CHECK_FAIL) is MENOR
    assert {proposal_for(r) for r in DeterministicRule} == {MENOR, Classification.SIN_CLASIFICAR}
