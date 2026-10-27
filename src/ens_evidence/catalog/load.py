"""Carga del catálogo empaquetado: un fichero por grupo, en el orden del anexo (org, op, mp)."""

import hashlib
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable

import yaml

from ens_evidence.catalog.model import Catalog, Group, MeasureCard, Section

_DATA = resources.files("ens_evidence.catalog") / "data"


def _data_files() -> list[Traversable]:
    return [_DATA / f"{group.value}.yaml" for group in Group]


@cache
def load_catalog() -> Catalog:
    groups: list[Section] = []
    subgroups: list[Section] = []
    measures: list[MeasureCard] = []
    for file in _data_files():
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
        groups.append(Section.model_validate(raw["group"]))
        subgroups.extend(Section.model_validate(item) for item in raw["subgroups"])
        measures.extend(MeasureCard.model_validate(item) for item in raw["measures"])
    return Catalog(groups=groups, subgroups=subgroups, measures=measures)


@cache
def catalog_sha256() -> str:
    digest = hashlib.sha256()
    for file in _data_files():
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()
