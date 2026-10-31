"""Idempotencia y manipulación del historial de `check --all`, por la CLI y de punta a punta.

Mismo mundo que `tests/assessment/test_acceptance.py`: un workspace real; la única entrada que no
pasa por la CLI es la recogida de GitHub (la hace el collector con red).
"""

from datetime import timedelta

import pytest
from matrix.evaluate_helpers import NOW

from ens_evidence.checks.store import scan_runs
from ens_evidence.evidence.layout import CHECKS_PREFIX

from .acceptance_helpers import CHECK_MEASURE, COLLECTED, REVIEWED_AT, SECRET, make_world
from .test_engine import crash_in
from .test_inputs import rewrite_manifest

AFTER_THE_RUN = REVIEWED_AT + timedelta(minutes=10)
THE_NEXT_DAY = timedelta(days=1)


@pytest.fixture
def linked(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "linked")


@pytest.fixture
def ready(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "ready")


def only_blocked_by(world, fragment: str, at=NOW) -> None:
    """La preparación cae y el único bloqueo es de la medida técnica, por esta causa."""
    status = world.status(at)
    assert status.exit_code == 1
    details = world.blockers_of(CHECK_MEASURE, at)
    assert any(fragment in detail for _, detail in details), details
    assert {b["measure_id"] for b in world.blockers(at)} == {CHECK_MEASURE}


# --- escenario: idempotencia ------------------------------------------------------------------


def test_repetir_check_all_con_la_misma_evidencia_no_cambia_ni_duplica(linked):
    linked.collect()
    first = linked.check_all()
    keys = linked.check_keys()
    status = linked.status(NOW).output

    second = linked.check_all(linked.clock.now + THE_NEXT_DAY)

    assert first.exit_code == second.exit_code == 0
    assert f"github: sin cambios desde {keys[0]}" in second.output
    assert linked.check_keys() == keys
    assert linked.status(NOW).output == status


def test_una_recogida_nueva_anade_exactamente_una_ejecucion(linked):
    linked.collect()
    linked.check_all()
    linked.collect(at=COLLECTED + timedelta(hours=3))

    again = linked.check_all(linked.clock.now + THE_NEXT_DAY)

    assert "ejecución escrita" in again.output
    assert len(linked.runs()) == 2
    assert len(linked.check_keys()) == 2


def test_a_luego_b_luego_a_deja_tres_ejecuciones(linked):
    linked.collect()
    a1 = linked.check_all(NOW - timedelta(hours=3))
    linked.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    b = linked.check_all(NOW - timedelta(hours=2))
    linked.collect(at=COLLECTED + timedelta(hours=6))
    a2 = linked.check_all(NOW - timedelta(hours=1))

    assert (a1.exit_code, b.exit_code, a2.exit_code) == (0, 4, 0)
    assert [r.run.seq for r in scan_runs(linked.store).runs["github"]] == [1, 2, 3]
    assert len(linked.store.list(CHECKS_PREFIX)) == 3


# --- regresiones de manipulación --------------------------------------------------------------


def test_un_manifiesto_retocado_tras_la_ejecucion_bloquea_con_check_evidence_changed(ready):
    assert ready.status().exit_code == 0
    manifest = ready.collector_manifest()
    evidence_id = manifest.rsplit("/", 1)[1].removesuffix(".yaml")

    rewrite_manifest(ready.store, evidence_id, r"^  version: '1'$", "  version: '2'")

    only_blocked_by(ready, "check_evidence_changed")


def test_collected_at_retocado_antes_de_la_ejecucion_da_evidence_inconsistent(ready):
    ready.collect(at=COLLECTED + timedelta(hours=3))
    evidence_id = ready.collector_manifest().rsplit("/", 1)[1].removesuffix(".yaml")
    rewrite_manifest(
        ready.store, evidence_id, r"^collected_at: .*$", "collected_at: '2026-10-30T08:00:00Z'"
    )

    checked = ready.check_all(AFTER_THE_RUN)

    assert checked.exit_code == 3
    assert "evidence_inconsistent" in checked.output
    assert "PASS 0" in checked.output
    assert ready.status().exit_code == 1


def test_borrar_una_ejecucion_intermedia_hace_el_historial_ilegible(ready):
    ready.collect(at=COLLECTED + timedelta(hours=3))
    ready.check_all(NOW - timedelta(minutes=20))
    ready.collect(at=COLLECTED + timedelta(hours=6))
    ready.check_all(NOW - timedelta(minutes=10))
    middle = scan_runs(ready.store).runs["github"][1].key
    assert ready.status().exit_code == 0

    (ready.store._root / middle).unlink()

    status = ready.status()
    assert status.exit_code == 1
    assert "check_history_unreadable" in status.output
    blocked = ready.check_all(NOW - timedelta(minutes=5))
    assert blocked.exit_code == 2
    assert "no se registra la ejecución" in blocked.output


def test_dar_de_alta_un_repo_y_reaprobar_el_alcance_bloquea_con_check_scope_changed(ready):
    assert ready.status().exit_code == 0

    valid_again = ready.register_repository_and_reapprove(AFTER_THE_RUN)
    status = ready.status(valid_again)

    assert status.exit_code == 1
    assert "check_scope_changed:github" in status.output
    assert "el alcance ha cambiado desde la última ejecución de los checks de github" in (
        status.output
    )


def test_un_fail_con_revision_favorable_sigue_bloqueando(ready):
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    assert ready.check_all(AFTER_THE_RUN).exit_code == 4

    only_blocked_by(ready, f"check_failed:{SECRET}:infra")
    exported = ready.run("matrix", "export", "--stdout")
    assert '"assessment": "COMPLIANT"' in exported.output  # la revisión favorable sigue ahí


def test_un_ultimo_intento_fallido_no_recupera_el_pass_anterior(ready, monkeypatch):
    assert ready.status().exit_code == 0
    ready.collect(at=COLLECTED + timedelta(hours=3))
    crash_in(monkeypatch, "github", SECRET)

    checked = ready.check_all(AFTER_THE_RUN)

    assert checked.exit_code == 3
    only_blocked_by(ready, "check_last_run_failed:github")
    assert "no se usa el resultado anterior" in ready.status().output
