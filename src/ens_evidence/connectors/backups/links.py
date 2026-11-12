"""Enlaza los checks `backups.*` con las cláusulas de mp.info.6 que miden.

Lo usa `box.sh kit enable copias`. Es el mismo enlace que declara `matrix link`: dice qué
cláusulas cubre el check, no que se cumplan; el resultado lo siguen decidiendo el check y la
revisión de una persona. BACKUP-006 (copia en otro lugar) no se enlaza: el kit no la configura.
"""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.connectors.backups.checks import (
    INTEGRITY,
    RECENT,
    REPOSITORY_ACCESSIBLE,
    RESTORE_TEST,
)
from ens_evidence.matrix import MatrixRejected, add_link, build_link, plan_obligations
from ens_evidence.workspace import WorkspaceStore

MEASURE = "mp.info.6"
COVERS = {
    RECENT: ("mp.info.6.1",),
    REPOSITORY_ACCESSIBLE: ("mp.info.6.1",),
    INTEGRITY: ("mp.info.6.1",),
    RESTORE_TEST: ("mp.info.6.r1.1",),
}


@dataclass(frozen=True)
class BackupsLinks:
    """`linked`: (check, cláusulas) enlazados; `skipped`: por qué no se enlazó el resto."""

    linked: tuple[tuple[str, tuple[str, ...]], ...]
    skipped: tuple[str, ...]


def manual_commands() -> list[str]:
    return [
        f"ens-evidence matrix link {MEASURE} --check {check} --covers {','.join(covers)}"
        for check, covers in COVERS.items()
    ]


def link_backups_checks(store: WorkspaceStore, now: datetime) -> BackupsLinks:
    """Enlaza cada check a las cláusulas de mp.info.6 que la Declaración vigente obliga.

    Sin Declaración vigente, sin matriz o sin entrada de mp.info.6 no cambia nada y lo dice.
    Repetirlo no duplica nada: `add_link` une las cláusulas.
    """
    plan = plan_obligations(store, now)
    row = plan.row(MEASURE) if plan.current else None
    if row is None:
        reason = (
            "la Declaración de Aplicabilidad no está vigente"
            if not plan.current
            else f"{MEASURE} no está en la Declaración de Aplicabilidad"
        )
        return BackupsLinks((), (reason,))
    clauses = {clause for obligation in row.obligations for clause in obligation.clause_ids}
    linked: list[tuple[str, tuple[str, ...]]] = []
    skipped: list[str] = []
    for check, wanted in COVERS.items():
        covers = tuple(c for c in wanted if c in clauses)
        if not covers:
            skipped.append(f"{check}: {MEASURE} no exige {', '.join(wanted)} en este nivel")
            continue
        try:
            add_link(store, now, MEASURE, build_link("check", check, list(covers)))
        except MatrixRejected as exc:
            skipped.append(f"{check}: {'; '.join(exc.errors)}")
            continue
        linked.append((check, covers))
    return BackupsLinks(tuple(linked), tuple(skipped))
