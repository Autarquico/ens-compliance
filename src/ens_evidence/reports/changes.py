"""Diferencias del informe diario frente a su base canónica.

La base es el último informe diario legible y verificado del periodo anterior más reciente
(`canonical_previous_daily`). Sin base, o con una base ilegible, el resultado es `Unavailable`:
«sin cambios» solo se afirma comparando con un informe real.
"""

from collections.abc import Callable
from typing import Any

from pydantic_core import to_jsonable_python

from ens_evidence.reports.model import (
    ChangeItem,
    Changes,
    DailyReport,
    Section,
    Sourced,
    SourceRef,
    Unavailable,
)
from ens_evidence.reports.store import Canonical, canonical_previous_daily
from ens_evidence.workspace import WorkspaceStore


def changes_since_base(store: WorkspaceStore, current: DailyReport) -> Section[Changes]:
    base = canonical_previous_daily(store, current.period)
    if isinstance(base, Unavailable):
        return Unavailable(reason=f"sin base de comparación: {base.reason}")
    return Sourced[Changes](
        value=Changes(base_period=base.report.period, items=diff(base.report, current)),
        sources=[_base_ref(base)],
    )


def _base_ref(base: Canonical) -> SourceRef:
    return SourceRef(
        kind="report",
        key=base.pair.json_key,
        sha256=base.json_sha256,
        at=base.report.generated_at,
    )


def diff(base: DailyReport, current: DailyReport) -> list[ChangeItem]:
    """Cambios de contenido; ni los instantes de generación ni las fuentes cuentan."""
    items: list[ChangeItem] = []
    items += _section("assets", base.assets, current.assets, _assets)
    items += _section(
        "assets_without_agent", base.assets_without_agent, current.assets_without_agent, _members
    )
    items += _connectors(base, current)
    items += _section("readiness", base.readiness, current.readiness, _readiness)
    items += _section("expiring", base.expiring, current.expiring, _expiring)
    return sorted(items, key=lambda i: (i.subject, i.change))


def _section[T](
    name: str,
    before: Section[T],
    after: Section[T],
    compare: Callable[[Any, Any], list[ChangeItem]],
) -> list[ChangeItem]:
    gone_before, gone_after = isinstance(before, Unavailable), isinstance(after, Unavailable)
    if gone_before and gone_after:
        return []
    if gone_before or gone_after:
        return [ChangeItem(subject=f"{name}: disponibilidad del dato", change="changed")]
    assert isinstance(before, Sourced) and isinstance(after, Sourced)
    return compare(before.value, after.value)


def _assets(before: Any, after: Any) -> list[ChangeItem]:
    items = []
    if before.total != after.total:
        items.append(ChangeItem(subject="assets.total", change="changed"))
    for kind in sorted(set(before.by_type) | set(after.by_type)):
        if before.by_type.get(kind, 0) != after.by_type.get(kind, 0):
            items.append(ChangeItem(subject=f"assets.{kind}", change="changed"))
    return items


def _set_changes(prefix: str, before: set[str], after: set[str]) -> list[ChangeItem]:
    return [
        *(ChangeItem(subject=f"{prefix}{s}", change="added") for s in sorted(after - before)),
        *(ChangeItem(subject=f"{prefix}{s}", change="removed") for s in sorted(before - after)),
    ]


def _members(before: list[str], after: list[str]) -> list[ChangeItem]:
    return _set_changes("assets_without_agent: ", set(before), set(after))


def _readiness(before: Any, after: Any) -> list[ChangeItem]:
    items = []
    if before.readiness != after.readiness:
        items.append(ChangeItem(subject="readiness", change="changed"))
    items += _set_changes("blocker: ", set(before.blockers), set(after.blockers))
    return items


def _expiring(before: list[Any], after: list[Any]) -> list[ChangeItem]:
    def names(entries: list[Any]) -> set[str]:
        return {f"{e.kind} {e.subject}" for e in entries}

    return _set_changes("expiring: ", names(before), names(after))


def _connectors(base: DailyReport, current: DailyReport) -> list[ChangeItem]:
    before = {c.connector: c for c in base.connectors}
    after = {c.connector: c for c in current.connectors}
    items = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old is None or new is None:
            items.append(
                ChangeItem(
                    subject=f"connector: {name}", change="added" if old is None else "removed"
                )
            )
        elif _fingerprint(old) != _fingerprint(new):
            items.append(ChangeItem(subject=f"connector: {name}", change="changed"))
    return items


def _fingerprint(entry: Any) -> tuple[Any, Any]:
    """Lo comparable de un conector: estado de recogida y recuentos (o su no disponibilidad)."""

    def content(section: Section[Any]) -> Any:
        if isinstance(section, Unavailable):
            return ("unavailable", section.reason)
        return ("value", to_jsonable_python(section.value))

    return content(entry.collection), content(entry.checks)
