from datetime import timedelta

import pytest

from ens_evidence.backup import dump
from ens_evidence.backup.records import restore_key
from ens_evidence.backup.restore_test import restore_test_key

from .test_restore_test_record import (
    NOW,
    instant,
    mutated,
    record,
    restore,
    stored,
    valid_declaration,
)
from .test_restore_test_record import box as restore_box  # noqa: F401 - fixture reutilizada


@pytest.fixture
def box(restore_box):  # noqa: F811
    return restore_box


def test_performed_at_anterior_al_fin_de_la_restauracion_no_se_registra(settings, box, tmp_path):
    declaration = mutated("performed_at", instant(-timedelta(days=4)))

    result = record(settings, tmp_path, declaration)

    assert result.exit_code == 2
    assert "anterior al fin de la restauración" in result.output
    assert stored(box) == []


def test_performed_at_mas_de_siete_dias_despues_de_la_restauracion_no_se_registra(
    settings, box, tmp_path
):
    box.write_bytes(
        restore_key("RS-OK"),
        dump(restore(started_at=NOW - timedelta(days=40), finished_at=NOW - timedelta(days=39))),
    )

    result = record(settings, tmp_path, valid_declaration())

    assert result.exit_code == 2
    assert "menos de 7 días" in result.output
    assert stored(box) == []


def test_performed_at_justo_a_siete_dias_de_la_restauracion_se_registra(settings, box, tmp_path):
    finished = NOW - timedelta(days=8)
    box.write_bytes(
        restore_key("RS-OK"),
        dump(restore(started_at=finished - timedelta(minutes=5), finished_at=finished)),
    )
    declaration = mutated("performed_at", (finished + timedelta(days=7)).isoformat())

    assert record(settings, tmp_path, declaration).exit_code == 0


def test_una_restauracion_ya_citada_por_otro_test_no_se_cita_de_nuevo(settings, box, tmp_path):
    assert record(settings, tmp_path, valid_declaration()).exit_code == 0
    second = mutated("test_id", "RT-2")

    result = record(settings, tmp_path, second)

    assert result.exit_code == 2
    assert "ya la cita otro restore test" in result.output
    assert stored(box) == [restore_test_key("RT-1")]
