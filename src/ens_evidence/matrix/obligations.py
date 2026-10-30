"""Filas y obligaciones de la matriz, derivadas de la Declaración de Aplicabilidad vigente.

Las filas son exactamente la unión de `selected`, `not_selected` y `required_but_absent_assets`.
Una medida seleccionada se descompone en obligaciones (`base`, refuerzos y, si hay compensación,
`compensation`, art. 28.3) y cada una en cláusulas del anexo II. Funciones puras salvo
`plan_obligations`, que lee el estado de la Declaración.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ens_evidence.applicability import Declaration, DeclarationState, declaration_status
from ens_evidence.catalog import Catalog, load_catalog
from ens_evidence.catalog.model import BASE, Clause, MeasureCard
from ens_evidence.matrix.fingerprint import measure_context_sha256
from ens_evidence.workspace import WorkspaceStore

COMPENSATION = "compensation"


class RowKind(StrEnum):
    SELECTED = "selected"
    NOT_SELECTED = "not_selected"
    ABSENT_ASSETS = "absent_assets"


@dataclass(frozen=True)
class ClauseRef:
    """Cláusula del anexo II; `id` es None si el anexo no la etiqueta."""

    id: str | None
    label: str | None
    text: str


@dataclass(frozen=True)
class Obligation:
    """`base`, un refuerzo `Rn` o `compensation`. `replaces` solo se rellena en esta última."""

    id: str
    clauses: tuple[ClauseRef, ...]
    replaces: tuple[str, ...] = ()
    compensating_measures: tuple[str, ...] = ()

    @property
    def clause_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.clauses if c.id)

    @property
    def units(self) -> tuple[str, ...]:
        """Lo que hay que cubrir: sus cláusulas con id o, si no tiene ninguna, ella misma."""
        return self.clause_ids or (self.id,)


@dataclass(frozen=True)
class MatrixRowSpec:
    measure_id: str
    kind: RowKind
    obligations: tuple[Obligation, ...]
    measure_context_sha256: str

    @property
    def compensated_clauses(self) -> frozenset[str]:
        """Cláusulas del anexo II que sustituye la compensación."""
        return frozenset(c for o in self.obligations if o.id == COMPENSATION for c in o.clause_ids)

    def obligation(self, obligation_id: str) -> Obligation | None:
        return next((o for o in self.obligations if o.id == obligation_id), None)


@dataclass(frozen=True)
class ObligationPlan:
    """`rows` se calcula siempre que haya Declaración legible; solo vale con `state == CURRENT`."""

    state: DeclarationState
    errors: list[str] = field(default_factory=list)
    rows: tuple[MatrixRowSpec, ...] = ()

    @property
    def current(self) -> bool:
        return self.state is DeclarationState.CURRENT

    def row(self, measure_id: str) -> MatrixRowSpec | None:
        return next((r for r in self.rows if r.measure_id == measure_id), None)


@dataclass(frozen=True)
class CoverExpansion:
    """`units` son cláusulas con id o ids de obligación sin cláusulas con id."""

    units: frozenset[str]
    errors: tuple[str, ...] = ()


def _clause_ref(clause: Clause) -> ClauseRef:
    return ClauseRef(id=clause.id, label=clause.label or clause.id, text=clause.text)


def _clauses_of(card: MeasureCard, obligation_id: str) -> tuple[ClauseRef, ...]:
    if obligation_id == BASE:
        clauses = card.base
    else:
        clauses = next(r.clauses for r in card.reinforcements if r.id == obligation_id)
    return tuple(_clause_ref(c) for c in clauses)


def _in_play(selected) -> list[str]:
    """Base, refuerzos exigidos y elegidos, sin repetir y en el orden de la ficha."""
    wanted = {BASE, *selected.required_reinforcements, *selected.chosen_reinforcements}
    return sorted(wanted, key=lambda i: (i != BASE, int(i[1:]) if i != BASE else 0))


def _compensation_for(declaration: Declaration, measure_id: str):
    return next((c for c in declaration.compensations if c.replaces == measure_id), None)


def obligations_for(
    selected, card: MeasureCard, declaration: Declaration
) -> tuple[Obligation, ...]:
    """Obligaciones de una medida seleccionada; lo sustituido por compensación pasa a una sola."""
    compensation = _compensation_for(declaration, selected.id)
    replaced = set(compensation.obligations) if compensation else set()
    result = [Obligation(o, _clauses_of(card, o)) for o in _in_play(selected) if o not in replaced]
    if compensation:
        known = {BASE, *(r.id for r in card.reinforcements)}
        ids = [o for o in compensation.obligations if o in known]
        result.append(
            Obligation(
                COMPENSATION,
                tuple(c for o in ids for c in _clauses_of(card, o)),
                replaces=tuple(ids),
                compensating_measures=tuple(compensation.measures),
            )
        )
    return tuple(result)


def rows_from_declaration(
    declaration: Declaration, catalog: Catalog | None = None
) -> tuple[MatrixRowSpec, ...]:
    catalog = catalog or load_catalog()
    rows: list[MatrixRowSpec] = []
    for selected in declaration.selected:
        card = catalog.get(selected.id)
        if card is None:
            raise LookupError(f"{selected.id} no está en el catálogo")
        rows.append(
            MatrixRowSpec(
                selected.id,
                RowKind.SELECTED,
                obligations_for(selected, card, declaration),
                measure_context_sha256(declaration, selected.id, catalog),
            )
        )
    others = [
        (RowKind.NOT_SELECTED, [m.id for m in declaration.not_selected]),
        (RowKind.ABSENT_ASSETS, [m.id for m in declaration.required_but_absent_assets]),
    ]
    for kind, ids in others:
        rows.extend(
            MatrixRowSpec(i, kind, (), measure_context_sha256(declaration, i, catalog)) for i in ids
        )
    return tuple(rows)


def plan_obligations(store: WorkspaceStore, now: datetime) -> ObligationPlan:
    status = declaration_status(store, now)
    if status.declaration is None:
        return ObligationPlan(status.state, list(status.errors))
    return ObligationPlan(
        status.state, list(status.errors), rows_from_declaration(status.declaration)
    )


def expand_covers(covers: Iterable[str], row: MatrixRowSpec) -> CoverExpansion:
    """Atajos (`base`, `Rn`, `compensation`) e ids de cláusula a unidades de cobertura.

    Rechaza lo que no es de la medida: un atajo sin esa obligación (p. ej. `base` si la sustituye
    la compensación) o una cláusula que no pertenece a ninguna de sus obligaciones.
    """
    by_id = {o.id: o for o in row.obligations}
    by_clause = {c: o for o in row.obligations for c in o.clause_ids}
    units: set[str] = set()
    errors: list[str] = []
    for token in covers:
        if token in by_id:
            units.update(by_id[token].units)
        elif token in by_clause:
            units.add(token)
        else:
            valid = sorted({*by_id, *by_clause})
            errors.append(
                f"{row.measure_id}: «{token}» no es una obligación ni una cláusula de la medida "
                f"({', '.join(valid) or 'sin obligaciones'})"
            )
    return CoverExpansion(frozenset(units), tuple(errors))
