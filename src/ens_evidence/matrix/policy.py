"""Política de fuentes por medida: qué evidencia puede sustentar una medida.

Es un dato, no una condición suelta: `SOURCE_POLICIES` dice, por medida, la única fuente de
evidencia que la sustenta. Se aplica al enlazar (`links`) y al evaluar (`evaluate.link_supports`,
que comparten `assess` y `prepare_review`), de modo que un enlace escrito a mano en `matrix.yaml`
no la esquiva.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE

OP_EXP_9_DETAIL = "op_exp_9_requires_incident_history"


@dataclass(frozen=True)
class SourcePolicy:
    """`evidence_source`: el `source` del manifiesto que sustenta la medida; nada más cuenta
    (ni otra evidencia, ni un check, ni un documento). `detail`: el bloqueo cuando no se cumple."""

    evidence_source: str
    detail: str


SOURCE_POLICIES: Mapping[str, SourcePolicy] = {
    "op.exp.9": SourcePolicy(INCIDENT_HISTORY_SOURCE, OP_EXP_9_DETAIL),
}


def source_policy(measure_id: str) -> SourcePolicy | None:
    return SOURCE_POLICIES.get(measure_id)


def admits(policy: SourcePolicy | None, evidence_source: str | None) -> bool:
    """`evidence_source` es None si la fuente no es una evidencia (check, documento) o no se lee."""
    return policy is None or evidence_source == policy.evidence_source
