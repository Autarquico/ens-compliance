"""Escenarios de `docs/specs/checks-engine.md` sobre `assessment status`, por la CLI.

Workspace real (ver `checks/acceptance_helpers.py`): sin fuentes falsas ni regla de preparación
parcheada. `ready` es un mundo READY de verdad: 69 medidas revisadas y checks en PASS.
"""

import json
from datetime import timedelta

import pytest
from checks.acceptance_helpers import (
    CHECK_MEASURE,
    COLLECTED,
    PENDING,
    SECRET,
    make_world,
)
from matrix.evaluate_helpers import NOW

REPO_FAILS = f"el check {SECRET} ha fallado en «infra»"
NO_RUN = f"check_no_results:{SECRET}"


@pytest.fixture
def linked(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "linked")


@pytest.fixture
def ready(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "ready")


def blocker_texts(world, measure=CHECK_MEASURE) -> list[str]:
    return [b["text"] for b in world.blockers() if b["measure_id"] == measure]


# --- escenario: NOT READY ---------------------------------------------------------------------


def test_un_repo_sin_secret_scanning_deja_la_preparacion_en_no_preparada_con_el_bloqueo(linked):
    linked.collect(secret_scanning_off=True)

    checked = linked.check_all()
    status = linked.status()

    assert checked.exit_code == 4
    assert status.exit_code == 1
    assert "Preparación: no preparada" in status.output
    assert f"{CHECK_MEASURE}: {REPO_FAILS}" in status.output
    assert ("NOT_COMPLIANT", f"check_failed:{SECRET}:infra") in linked.blockers_of(CHECK_MEASURE)


def test_sin_ejecutar_check_all_la_medida_tecnica_pide_la_ejecucion(linked):
    linked.collect(secret_scanning_off=True)

    status = linked.status()

    assert status.exit_code == 1
    assert "Preparación: no preparada" in status.output
    assert ("MISSING_EVIDENCE", NO_RUN) in linked.blockers_of(CHECK_MEASURE)
    assert any("ens-evidence check --all" in t for t in blocker_texts(linked))


def test_un_solo_fail_tumba_un_mundo_preparado_y_solo_bloquea_su_medida(ready):
    assert ready.status().exit_code == 0
    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))

    assert ready.check_all(NOW - timedelta(minutes=10)).exit_code == 4
    status = ready.status()

    assert status.exit_code == 1
    assert {b["measure_id"] for b in ready.blockers()} == {CHECK_MEASURE}
    assert ("NOT_COMPLIANT", f"check_failed:{SECRET}:infra") in ready.blockers_of(CHECK_MEASURE)


# --- escenario: juicio no ejecutado -----------------------------------------------------------


def test_no_preparada_indica_revision_humana_pendiente_en_texto_y_json(linked):
    linked.collect(secret_scanning_off=True)
    linked.check_all()

    text = linked.status()
    document = json.loads(linked.status(NOW, "--format", "json").output)

    assert text.exit_code == 1 and PENDING in text.output
    assert document["readiness"] == "NOT_READY"
    assert document["pending_human_review"] is True


def test_preparada_sale_con_0_y_sigue_indicando_revision_humana_pendiente(ready):
    text = ready.status()
    document = json.loads(ready.status(NOW, "--format", "json").output)

    assert text.exit_code == 0
    assert "Preparación: preparada" in text.output
    assert PENDING in text.output
    assert document["readiness"] == "READY" and document["blockers"] == []
    assert document["pending_human_review"] is True


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_preparada_no_dice_conforme_ni_cumple(ready, fmt):
    output = ready.status(NOW, "--format", fmt).output.lower()

    assert "conforme" not in output and "cumple" not in output


# --- el PASS no sustituye a la revisión -------------------------------------------------------


def only_review_blocks(world, kind: str, detail: str, at=NOW) -> None:
    """Todos los checks en PASS y la medida técnica bloqueada solo por su revisión."""
    status = world.status(at)
    assert status.exit_code == 1
    assert "PASS 7  FAIL 0  UNKNOWN 0  ERROR 0" in status.output
    assert {b["measure_id"] for b in world.blockers(at)} == {CHECK_MEASURE}
    assert (kind, detail) in world.blockers_of(CHECK_MEASURE, at)
    assert not any(d.startswith("check_failed") for _, d in world.blockers_of(CHECK_MEASURE, at))


def test_con_todos_los_checks_en_pass_y_sin_revision_no_esta_preparada(linked):
    linked.collect()
    assert linked.check_all().exit_code == 0

    status = linked.status()

    assert status.exit_code == 1
    assert "PASS 7  FAIL 0  UNKNOWN 0  ERROR 0" in status.output
    assert ("MISSING_REVIEW", "review_missing") in linked.blockers_of(CHECK_MEASURE)


def test_con_los_checks_en_pass_una_revision_futura_no_sustenta_la_medida(ready):
    before_the_reviews = NOW - timedelta(minutes=45)

    status = ready.status(before_the_reviews)

    assert status.exit_code == 1
    assert "PASS 7  FAIL 0  UNKNOWN 0  ERROR 0" in status.output
    assert ("MISSING_REVIEW", "review_missing") in ready.blockers_of(
        CHECK_MEASURE, before_the_reviews
    )


def test_con_los_checks_en_pass_una_revision_caducada_no_sustenta_la_medida(ready):
    later = NOW + timedelta(days=10)  # la medida se revisa cada 7 días; el resto, cada 365
    ready.collect(at=later - timedelta(hours=1))
    assert ready.check_all(later).exit_code == 0

    only_review_blocks(ready, "EXPIRED_REVIEW", "", later)


def test_con_los_checks_en_pass_una_revision_invalidada_por_cambiar_los_enlaces_no_sustenta(ready):
    ready.at(NOW)
    relinked = ready.run(
        *("matrix", "link", CHECK_MEASURE, "--check", "github.default_branch_protected"),
        *("--covers", "base"),
    )
    assert relinked.exit_code == 0, relinked.output

    only_review_blocks(ready, "EXPIRED_REVIEW", "links_changed")
