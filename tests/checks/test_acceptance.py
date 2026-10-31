"""Idempotencia y manipulación del historial de `check --all`, por la CLI y de punta a punta.

Mismo mundo que `tests/assessment/test_acceptance.py`: un workspace real; la única entrada que no
pasa por la CLI es la recogida de GitHub (la hace el collector con red).
"""

from datetime import timedelta

import pytest
from matrix.evaluate_helpers import NOW

from ens_evidence.checks.store import record_run, scan_runs
from ens_evidence.evidence.layout import CHECKS_PREFIX

from .acceptance_helpers import CHECK_MEASURE, COLLECTED, REVIEWED_AT, SECRET, make_world
from .test_engine import crash_in
from .test_inputs import rewrite_manifest
from .test_store import stored

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


# --- SEC-1: el último registro ya no se puede borrar para lavar un FAIL ------------------------


def test_borrar_la_ultima_ejecucion_con_fail_no_devuelve_el_pass_anterior(ready):
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    assert ready.check_all(AFTER_THE_RUN).exit_code == 4
    last = scan_runs(ready.store).last("github").key

    (ready.store._root / last).unlink()

    only_blocked_by(ready, "check_run_missing:github")
    assert "hay evidencia recogida después de la última ejecución" in ready.status().output
    assert "ens-evidence check --all" in ready.status().output


def test_una_recogida_nueva_sin_check_all_bloquea_hasta_ejecutarlo(ready):
    ready.collect(at=COLLECTED + timedelta(hours=3))

    only_blocked_by(ready, "check_run_missing:github")
    assert ready.check_all(NOW - timedelta(minutes=10)).exit_code == 0
    assert ready.status().exit_code == 0


def test_con_el_reloj_atrasado_el_fail_nuevo_no_se_registra_ni_deja_el_pass_en_pie(ready):
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    runs_before = ready.runs()

    rejected = ready.check_all(AFTER_THE_RUN - timedelta(hours=2))

    assert rejected.exit_code == 2
    assert "es anterior a la última ejecución guardada de github" in rejected.output
    assert ready.runs() == runs_before
    only_blocked_by(ready, "check_run_missing:github")


def test_con_el_reloj_adelantado_el_fail_cuenta_y_no_se_puede_registrar_nada_anterior(ready):
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    ahead = NOW + 2 * THE_NEXT_DAY
    assert ready.check_all(ahead).exit_code == 4

    only_blocked_by(ready, f"check_failed:{SECRET}:infra", at=ahead)
    assert ready.check_all(NOW).exit_code == 2


# --- SEC-2, PROD-8..10: el reloj adelantado llega a la preparación y tiene salida ---------------


def with_a_fail_recorded_by_a_clock_two_days_ahead(world) -> None:
    world.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    assert world.check_all(NOW + 2 * THE_NEXT_DAY).exit_code == 4
    world.at(AFTER_THE_RUN)  # el reloj vuelve a ser el correcto


def test_assessment_status_detecta_la_ejecucion_con_fecha_futura(ready):
    with_a_fail_recorded_by_a_clock_two_days_ahead(ready)

    only_blocked_by(ready, "check_run_in_future:github")
    text = ready.status().output
    assert "la última ejecución de los checks de github tiene fecha futura (2026-11-01" in text
    assert "--clock-corrected" in text
    assert "ens-evidence check --all" in text


def test_matrix_status_y_assessment_status_cuentan_lo_mismo_del_reloj_adelantado(ready):
    with_a_fail_recorded_by_a_clock_two_days_ahead(ready)

    matrix = ready.run("matrix", "status", "--at", NOW.isoformat())

    assert matrix.exit_code == 1
    assert "tiene fecha futura" in matrix.output and "--clock-corrected" in matrix.output


def test_el_rechazo_por_reloj_se_explica_en_llano_y_con_el_siguiente_paso(ready):
    with_a_fail_recorded_by_a_clock_two_days_ahead(ready)

    rejected = ready.check_all(AFTER_THE_RUN)

    assert rejected.exit_code == 2
    assert "es anterior a la última ejecución guardada de github" in rejected.output
    assert "Siguiente paso" in rejected.output and "--clock-corrected" in rejected.output
    assert "finished_at" not in rejected.output


def test_con_la_hora_corregida_clock_corrected_registra_una_ejecucion_nueva_sin_borrar_nada(ready):
    with_a_fail_recorded_by_a_clock_two_days_ahead(ready)
    before = ready.runs()

    corrected = ready.check_all_corrected(AFTER_THE_RUN)

    assert corrected.exit_code == 4  # el FAIL real sigue ahí: la salida no lo borra
    assert "corrección del reloj" in corrected.output
    after = ready.runs()
    assert after[: len(before)] == before and len(after) == len(before) + 1
    only_blocked_by(ready, f"check_failed:{SECRET}:infra")
    assert "fecha futura" not in ready.status().output


def test_tras_la_correccion_un_registro_posterior_sano_devuelve_la_preparacion(ready):
    with_a_fail_recorded_by_a_clock_two_days_ahead(ready)
    assert ready.check_all_corrected(AFTER_THE_RUN).exit_code == 4

    ready.collect(at=COLLECTED + timedelta(hours=4))

    assert ready.check_all(AFTER_THE_RUN + timedelta(minutes=1)).exit_code == 0
    assert ready.status().exit_code == 0


def test_clock_corrected_sin_ejecuciones_futuras_se_comporta_como_check_all(ready):
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    runs_before = ready.runs()

    ready.at(AFTER_THE_RUN)
    result = ready.run("check", "--all", "--clock-corrected")

    assert result.exit_code == 4
    assert "corrección del reloj" not in result.output
    assert len(ready.runs()) == len(runs_before) + 1


# --- CODE-7: una evidencia que no se puede listar no se lee como «no hay evidencia posterior» ---


def test_borrar_la_ultima_ejecucion_con_fail_y_dejar_un_manifiesto_ilegible_bloquea(ready):
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    assert ready.check_all(AFTER_THE_RUN).exit_code == 4
    (ready.store._root / scan_runs(ready.store).last("github").key).unlink()
    ready.store.write_text("evidence/manifests/EV-20260601-deadbeef.yaml", "id: [roto")

    only_blocked_by(ready, "evidence_unreadable:github")
    assert "no se puede leer algún registro de evidencia" in ready.status().output


# --- CODE-8 y followups: un directorio ajeno o una configuración borrada no rompen nada ---------


def test_un_directorio_de_conector_desconocido_se_informa_sin_romper_los_comandos(ready):
    record_run(ready.store, stored(connector="foo"), NOW)

    for args in (("assessment", "status"), ("matrix", "status")):
        result = ready.run(*args, "--at", NOW.isoformat())
        assert "Traceback" not in result.output and "KeyError" not in result.output
        assert "check_connector_unknown:foo" in result.output
        assert result.exit_code == 1


def test_con_la_configuracion_del_conector_borrada_el_mensaje_dice_que_hacer(ready):
    (ready.store._root / "connectors/github.yaml").unlink()

    only_blocked_by(ready, "check_config_missing:github")
    text = ready.status().output
    assert "connectors/github.yaml" in text and "matrix unlink" in text


def test_la_configuracion_cambiada_nombra_el_conector(ready):
    ready.store.write_text(
        "connectors/github.yaml", ready.store.read_text("connectors/github.yaml") + "# x\n"
    )

    only_blocked_by(ready, "check_config_changed:github")
    assert "la configuración del conector github (connectors/github.yaml)" in ready.status().output
