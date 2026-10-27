"""Ficha de medida del anexo II del RD 311/2022 (docs/decisions/0007)."""

import re
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from ens_evidence.models.contracts import SHA256_PATTERN, Strict
from ens_evidence.models.enums import Category

BASE = "base"
_REINFORCEMENT_ID = re.compile(r"^R\d+$")


class Group(StrEnum):
    ORG = "org"
    OP = "op"
    MP = "mp"


class Axis(StrEnum):
    CATEGORY = "category"
    DIMENSION = "dimension"


class Dimension(StrEnum):
    C = "C"
    I = "I"  # noqa: E741 - inicial de "integridad", como en el anexo
    T = "T"
    A = "A"
    D = "D"


class SecurityLevel(StrEnum):
    BAJO = "BAJO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"


LEVELS_BY_AXIS: dict[Axis, tuple[str, ...]] = {
    Axis.CATEGORY: tuple(Category),
    Axis.DIMENSION: tuple(SecurityLevel),
}


class Clause(Strict):
    """Párrafo del cuerpo de la medida; `id` es None si el anexo no lo etiqueta."""

    id: str | None = None
    label: str | None = Field(
        default=None, description="Etiqueta literal del anexo cuando difiere de `id`."
    )
    text: str


class Reinforcement(Strict):
    id: str
    title: str
    required_at: list[str] = Field(
        description="Niveles en los que la tabla del anexo exige este refuerzo; vacía si ninguno."
    )
    alternative_at: list[str] = Field(
        description="Niveles en los que es una alternativa dentro de un grupo `[Rn o Rm]`, no una "
        "exigencia por sí solo; vacía si ninguno."
    )
    clauses: list[Clause]

    @model_validator(mode="after")
    def _id_shape(self) -> Self:
        if not _REINFORCEMENT_ID.match(self.id):
            raise ValueError(f"refuerzo con id inválido: {self.id}")
        return self


class LevelRequirement(Strict):
    """Exigencia de un nivel: `all_of` (todas) y `any_of` (una por grupo) sobre `base` y Rn."""

    not_applicable: bool
    all_of: list[str] = Field(default_factory=list)
    any_of: list[list[str]] = Field(default_factory=list)
    expression: str = Field(description="Celda del anexo tal cual; vacía si la celda lo está.")

    @model_validator(mode="after")
    def _not_applicable_is_empty(self) -> Self:
        if self.not_applicable and (self.all_of or self.any_of):
            raise ValueError("not_applicable no admite requisitos")
        if not self.not_applicable and BASE not in self.all_of:
            raise ValueError("una medida aplicable exige siempre la base")
        return self

    def alternatives(self) -> set[str]:
        return {ref for group in self.any_of for ref in group}

    def references(self) -> set[str]:
        return {*self.all_of, *self.alternatives()}


class Source(Strict):
    regulation: str
    annex: str
    section: str
    url: str
    consolidation_date: date
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)


class MeasureCard(Strict):
    id: str
    name: str
    group: Group
    subgroup: str | None = Field(description="p. ej. `op.pl`; None en el marco organizativo.")
    applies_by: Axis
    dimensions: list[Dimension] = Field(
        min_length=1, description="Las cinco si el anexo dice «Todas»."
    )
    preamble: list[str] = Field(default_factory=list)
    diagram_refs: list[str] = Field(
        default_factory=list,
        description="Imágenes (diagramas de flujo) que el anexo intercala; no se descargan.",
    )
    base: list[Clause]
    reinforcements: list[Reinforcement] = Field(default_factory=list)
    levels: dict[str, LevelRequirement]
    notes: list[str] = Field(
        default_factory=list, description="Divergencias del propio anexo aceptadas al extraer."
    )
    source: Source

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if set(self.levels) != set(LEVELS_BY_AXIS[self.applies_by]):
            raise ValueError(f"{self.id}: niveles {sorted(self.levels)} no casan con el eje")
        known = {BASE, *(r.id for r in self.reinforcements)}
        if len(known) != len(self.reinforcements) + 1:
            raise ValueError(f"{self.id}: refuerzos duplicados")
        used = set().union(*(level.references() for level in self.levels.values()))
        if not used <= known:
            raise ValueError(
                f"{self.id}: referencias a refuerzos inexistentes {sorted(used - known)}"
            )
        for reinforcement in self.reinforcements:
            if reinforcement.required_at != self.levels_requiring(reinforcement.id):
                raise ValueError(f"{self.id}: {reinforcement.id}.required_at no casa con la tabla")
            if reinforcement.alternative_at != self.levels_offering(reinforcement.id):
                raise ValueError(
                    f"{self.id}: {reinforcement.id}.alternative_at no casa con la tabla"
                )
        return self

    def levels_requiring(self, requirement_id: str) -> list[str]:
        """Niveles que exigen `requirement_id` (en `all_of`), en el orden canónico del eje."""
        return [
            level
            for level in LEVELS_BY_AXIS[self.applies_by]
            if requirement_id in self.levels[level].all_of
        ]

    def levels_offering(self, requirement_id: str) -> list[str]:
        """Niveles que lo admiten como alternativa (en un grupo de `any_of`)."""
        return [
            level
            for level in LEVELS_BY_AXIS[self.applies_by]
            if requirement_id in self.levels[level].alternatives()
        ]


class Section(Strict):
    """Grupo (`op`) o subgrupo (`op.acc`) del anexo con el texto literal que lo introduce."""

    id: str
    title: str
    text: list[str] = Field(
        default_factory=list, description="Párrafos que el anexo escribe antes de sus medidas."
    )


class Catalog(Strict):
    groups: list[Section]
    subgroups: list[Section]
    measures: list[MeasureCard]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for kind, ids in (
            ("grupo", [s.id for s in self.groups]),
            ("subgrupo", [s.id for s in self.subgroups]),
            ("medida", [m.id for m in self.measures]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"ids de {kind} duplicados")
        if [s.id for s in self.groups] != [g.value for g in Group]:
            raise ValueError("los grupos no son org, op, mp en el orden del anexo")
        subgroups = {s.id for s in self.subgroups}
        unknown = {m.subgroup for m in self.measures if m.subgroup} - subgroups
        if unknown:
            raise ValueError(f"subgrupos sin sección: {sorted(unknown)}")
        return self

    def subgroup(self, subgroup_id: str | None) -> Section | None:
        return next((s for s in self.subgroups if s.id == subgroup_id), None)

    def group(self, group: Group) -> Section:
        return next(s for s in self.groups if s.id == group.value)

    def get(self, measure_id: str) -> MeasureCard | None:
        return next((m for m in self.measures if m.id == measure_id), None)
