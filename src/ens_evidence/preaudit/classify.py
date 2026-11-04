"""Tabla de decisión de la capa determinista y agrupación de hallazgos (IC-01/19 §54).

Nada de aquí inventa severidades: cada propuesta sale de `DETERMINISTIC_PROPOSAL` (modelo) y cita
la fuente que la sustenta. La agrupación es solo una propuesta derivada; los hallazgos originales
se conservan y la confirma una persona.
"""

from collections import defaultdict
from collections.abc import Iterable

from ens_evidence.models.preaudit import (
    DETERMINISTIC_PROPOSAL,
    Classification,
    DeterministicRule,
    Finding,
    GroupingProposal,
    group_of,
)
from ens_evidence.preaudit.pack import Source

ITS_SOURCE = "ITS de Auditoría (BOE-A-2018-4573) §VI.2"
IC_SOURCE = "CCN-CERT IC-01/19 (octubre 2026)"

_MENOR_SOURCE = (
    f"{ITS_SOURCE}, «No Conformidad Menor» (fallo en la implantación de los requisitos); "
    f"{IC_SOURCE} §53, incumplimiento parcial de alguna medida"
)
_UNCLASSIFIED_SOURCE = (
    f"{ITS_SOURCE} y {IC_SOURCE} §53: no hay evidencia objetiva de incumplimiento, solo de falta "
    "de información; la clasifica una persona"
)
GROUPING_SOURCE = f"{IC_SOURCE} §54"

# Definiciones literales de las clases que la capa de juicio puede proponer. Cada `quote` aparece
# tal cual en su texto oficial (tests/preaudit/test_pack.py lo comprueba contra los fixtures).
JUDGMENT_CLASSIFICATIONS: dict[Classification, tuple[Source, ...]] = {
    Classification.NC_MENOR: (
        Source(
            document="its-auditoria",
            reference=f"{ITS_SOURCE}, «No Conformidad Menor»",
            quote=(
                "Se documentará una «No Conformidad Menor» ante la ausencia o el fallo en la "
                "implantación o mantenimiento de uno o más de los requisitos del ENS"
            ),
        ),
        Source(
            document="ic0119",
            reference=f"{IC_SOURCE} §53, No Conformidad Menor",
            quote=(
                "Ante un incumplimiento parcial de algún artículo del RD 311/2022 y/o el "
                "incumplimiento parcial de alguna medida/control (o algún requisito de alguna "
                "medida/control) del Anexo II"
            ),
        ),
    ),
    Classification.OBSERVACION: (
        Source(
            document="its-auditoria",
            reference=f"{ITS_SOURCE}, Observación",
            quote=(
                "Se documentará una Observación cuando se encuentren evidencias de una debilidad, "
                "una vulnerabilidad o una situación que, sin comprometer cualquier área del "
                "sistema de gestión definida en el ENS o por la organización, pueda, en la "
                "actualidad o en el futuro, derivar en un problema."
            ),
        ),
    ),
}


def judgment_classification_source(proposal: Classification) -> str:
    """La fuente literal de la clase que propone un hallazgo de juicio."""
    return "; ".join(f"{s.reference}: «{s.quote}»" for s in JUDGMENT_CLASSIFICATIONS[proposal])


_SOURCE_BY_PROPOSAL = {
    Classification.NC_MENOR: _MENOR_SOURCE,
    Classification.SIN_CLASIFICAR: _UNCLASSIFIED_SOURCE,
}


def proposal_for(rule: DeterministicRule) -> Classification:
    return DETERMINISTIC_PROPOSAL[rule]


def source_for(rule: DeterministicRule) -> str:
    return _SOURCE_BY_PROPOSAL[proposal_for(rule)]


def group_findings(findings: Iterable[Finding]) -> list[GroupingProposal]:
    """Propuestas de agrupación de las NC menores (§54).

    - Varias menores de una misma medida: una menor.
    - Menores de varias medidas de un mismo grupo: una mayor.
    - Nunca entre grupos distintos; las mayores no se agrupan (se ignoran).
    """
    minors = [f for f in findings if f.proposal is Classification.NC_MENOR]
    by_measure: dict[str, list[Finding]] = defaultdict(list)
    for finding in minors:
        by_measure[finding.measure].append(finding)
    by_group: dict[str, dict[str, list[Finding]]] = defaultdict(dict)
    for measure, items in by_measure.items():
        by_group[group_of(measure)][measure] = items

    proposals = [
        _single_measure(m, items) for m, items in sorted(by_measure.items()) if len(items) > 1
    ]
    proposals += [
        _same_group(group, measures)
        for group, measures in sorted(by_group.items())
        if len(measures) > 1
    ]
    return proposals


def _ids(findings: Iterable[Finding]) -> list[str]:
    return sorted({f.id for f in findings})


def _single_measure(measure: str, findings: list[Finding]) -> GroupingProposal:
    return GroupingProposal(
        proposal=Classification.NC_MENOR,
        measures=[measure],
        finding_ids=_ids(findings),
        justification=(
            f"{len(findings)} no conformidades menores referidas a la medida {measure}: pueden "
            "agruparse en una sola menor; propuesta, la decide una persona"
        ),
        source=GROUPING_SOURCE,
    )


def _same_group(group: str, measures: dict[str, list[Finding]]) -> GroupingProposal:
    return GroupingProposal(
        proposal=Classification.NC_MAYOR,
        measures=sorted(measures),
        finding_ids=_ids(f for items in measures.values() for f in items),
        justification=(
            f"no conformidades menores en {len(measures)} medidas del grupo [{group}.*]: su "
            "agrupación se calificaría como mayor; propuesta, la decide una persona"
        ),
        source=GROUPING_SOURCE,
    )
