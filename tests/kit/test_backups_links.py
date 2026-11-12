"""Activar el kit de copias deja los checks `backups.*` enlazados a mp.info.6 (sin fabricarlos).

El enlace es el de `matrix link`: declara qué cláusulas mide cada check, no que se cumplan.
"""

from datetime import timedelta

import pytest
from applicability.helpers import (
    LATER,
    decide,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)
from matrix.evaluate_helpers import assess_now, row_of
from matrix.helpers import COMPENSATION, SIGNED_AT

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.connectors.backups import checks as backups_checks
from ens_evidence.connectors.backups import link_backups_checks
from ens_evidence.connectors.backups.links import COVERS, manual_commands
from ens_evidence.matrix import CheckLink, init_matrix
from ens_evidence.matrix.links import load_matrix
from ens_evidence.models.enums import Applicability


def signed_workspace(tmp_path, level: str):
    store = prepared_workspace(tmp_path / "ws", uniform(level))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    decide(store, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    approve_declaration(store, "Luis Pardo", SIGNED_AT)
    return store


def links_of(store) -> dict[str, list[str]]:
    entry = next(e for e in load_matrix(store).entries if e.id == "mp.info.6")
    return {link.check_id: list(link.covers) for link in entry.links if isinstance(link, CheckLink)}


def test_los_checks_del_conector_son_los_que_se_enlazan():
    assert set(COVERS) == {
        backups_checks.RECENT,
        backups_checks.REPOSITORY_ACCESSIBLE,
        backups_checks.INTEGRITY,
        backups_checks.RESTORE_TEST,
    }


def test_con_nivel_medio_cada_check_queda_enlazado_a_sus_clausulas(tmp_path):
    store = signed_workspace(tmp_path, "MEDIO")
    init_matrix(store, SIGNED_AT)

    outcome = link_backups_checks(store, SIGNED_AT)

    assert outcome.skipped == ()
    assert links_of(store) == {
        "backups.backup_001_recent": ["mp.info.6.1"],
        "backups.backup_003_repository_accessible": ["mp.info.6.1"],
        "backups.backup_004_integrity": ["mp.info.6.1"],
        "backups.backup_005_restore_test": ["mp.info.6.r1.1"],
    }


def test_con_nivel_bajo_no_se_enlaza_la_prueba_de_restauracion_porque_no_se_exige(tmp_path):
    store = signed_workspace(tmp_path, "BAJO")
    init_matrix(store, SIGNED_AT)

    outcome = link_backups_checks(store, SIGNED_AT)

    assert "backups.backup_005_restore_test" not in links_of(store)
    assert len(links_of(store)) == 3
    assert [s.split(":")[0] for s in outcome.skipped] == ["backups.backup_005_restore_test"]


def test_repetir_la_activacion_no_duplica_enlaces(tmp_path):
    store = signed_workspace(tmp_path, "MEDIO")
    init_matrix(store, SIGNED_AT)

    link_backups_checks(store, SIGNED_AT)
    before = links_of(store)
    link_backups_checks(store, SIGNED_AT + timedelta(minutes=1))

    assert links_of(store) == before


def test_la_matriz_evalua_los_enlaces_de_la_activacion_y_sin_resultados_no_cubre_nada(tmp_path):
    store = signed_workspace(tmp_path, "MEDIO")
    init_matrix(store, SIGNED_AT)
    link_backups_checks(store, SIGNED_AT)

    row = row_of(assess_now(store, SIGNED_AT + timedelta(hours=1)), "mp.info.6")

    assert row.covered == frozenset()
    assert {s.link.check_id for s in row.supports} == set(COVERS)
    assert {
        (b.kind.value, b.detail) for b in row.blockers if b.detail.startswith("check_no_results")
    } >= {("MISSING_EVIDENCE", f"check_no_results:{check}") for check in COVERS}


def test_sin_declaracion_vigente_no_cambia_nada_y_da_los_comandos_manuales(tmp_path):
    store = prepared_workspace(tmp_path / "ws", uniform("MEDIO"))

    outcome = link_backups_checks(store, SIGNED_AT)

    assert outcome.linked == ()
    assert "Declaración" in outcome.skipped[0]
    assert all(
        c.startswith("ens-evidence matrix link mp.info.6 --check backups.")
        for c in manual_commands()
    )
    assert len(manual_commands()) == len(COVERS)


def test_sin_matriz_inicializada_no_se_enlaza_nada(tmp_path):
    store = signed_workspace(tmp_path, "MEDIO")

    outcome = link_backups_checks(store, SIGNED_AT)

    assert outcome.linked == ()
    assert len(outcome.skipped) == len(COVERS)
    assert all("matrix init" in s for s in outcome.skipped)


@pytest.mark.parametrize("command", manual_commands())
def test_los_comandos_manuales_son_validos(command):
    assert command.split()[:4] == ["ens-evidence", "matrix", "link", "mp.info.6"]
