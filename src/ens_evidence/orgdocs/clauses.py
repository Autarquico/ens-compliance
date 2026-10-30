"""Cláusulas oficiales citables: anexo II (del catálogo) y articulado (de la fuente revisada)."""

from dataclasses import dataclass
from functools import cache
from typing import Literal

from ens_evidence.catalog import load_catalog
from ens_evidence.orgdocs.sources import load_articulado

INTRO = "intro"


@dataclass(frozen=True)
class OfficialClause:
    id: str
    reference: str
    text: str
    origin: Literal["catalog", "articulado"]
    measure_id: str | None


class UnknownClause(KeyError):
    pass


@cache
def clause_index() -> dict[str, OfficialClause]:
    index: dict[str, OfficialClause] = {}

    def add(clause: OfficialClause) -> None:
        if clause.id in index:
            raise ValueError(f"cláusula duplicada: {clause.id}")
        index[clause.id] = clause

    for measure in load_catalog().measures:
        intro_done = False
        for clause in measure.base:
            if clause.id is None:
                if intro_done:
                    continue
                intro_done = True
                clause_id, reference = (
                    f"{measure.id}.{INTRO}",
                    f"anexo II, {measure.id} (introducción)",
                )
            else:
                clause_id, reference = clause.id, f"anexo II, {clause.id}"
            add(
                OfficialClause(
                    clause_id, f"RD 311/2022, {reference}", clause.text, "catalog", measure.id
                )
            )
        for reinforcement in measure.reinforcements:
            for clause in reinforcement.clauses:
                if clause.id is not None:
                    reference = f"RD 311/2022, anexo II, {clause.id}"
                    add(OfficialClause(clause.id, reference, clause.text, "catalog", measure.id))
    for clause in load_articulado().clauses:
        add(OfficialClause(clause.id, clause.reference, clause.text, "articulado", None))
    return index


def official_clause(clause_id: str) -> OfficialClause:
    try:
        return clause_index()[clause_id]
    except KeyError:
        raise UnknownClause(clause_id) from None


def measure_clause_ids(measure_id: str) -> list[str]:
    """Ids de las cláusulas de cuerpo (base) de la medida, incluida la introducción si la hay."""
    measure = load_catalog().get(measure_id)
    if measure is None:
        raise UnknownClause(measure_id)
    ids = []
    for clause in measure.base:
        if clause.id is not None:
            ids.append(clause.id)
        elif f"{measure_id}.{INTRO}" not in ids:
            ids.append(f"{measure_id}.{INTRO}")
    return ids
