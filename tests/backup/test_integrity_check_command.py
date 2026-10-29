import json
from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.backup import BackupError, list_checks, run_check
from ens_evidence.backup.records import check_key

from .conftest import CANARY, PASSWORD, REPOSITORY_ID, ens


def summary(**fields) -> str:
    return json.dumps({"message_type": "summary", **fields}) + "\n"


def only_check(workspace):
    checks, unreadable = list_checks(workspace)
    assert unreadable == []
    assert len(checks) == 1
    return checks[0]


def test_check_sin_errores_queda_completed_con_el_id_del_repositorio(
    settings, workspace, scripted_restic
):
    scripted_restic.reply("check", out=summary(num_errors=0, broken_packs=None))

    result = ens(settings, "backup", "check")

    assert result.exit_code == 0, result.output
    check = only_check(workspace)
    assert check.check_id in result.output
    assert (check.status, check.exit_code, check.num_errors, check.broken_packs) == (
        "completed",
        0,
        0,
        0,
    )
    assert check.repository_id == REPOSITORY_ID
    assert check.failure is None


def test_check_con_errores_sale_con_1_y_registra_failed_con_los_recuentos(
    settings, workspace, scripted_restic
):
    scripted_restic.reply(
        "check", out=summary(num_errors=3, broken_packs=["a" * 64, "b" * 64]), code=1
    )

    result = ens(settings, "backup", "check")

    assert result.exit_code == 1
    check = only_check(workspace)
    assert (check.status, check.exit_code, check.num_errors, check.broken_packs) == (
        "failed",
        1,
        3,
        2,
    )
    assert check.failure == "repository_errors"


def test_check_sin_resumen_de_restic_queda_failed_y_no_registra_su_salida(
    settings, workspace, scripted_restic
):
    scripted_restic.reply("check", out=f"{CANARY} {PASSWORD}\n", err=f"{CANARY} {PASSWORD}", code=1)

    result = ens(settings, "backup", "check")

    assert result.exit_code == 1
    check = only_check(workspace)
    assert (check.status, check.failure, check.num_errors) == ("failed", "no_summary", None)
    stored = workspace.read_text(check_key(check.check_id))
    for text in (result.output, stored):
        assert CANARY not in text
        assert PASSWORD not in text


def test_check_con_exit_0_pero_con_errores_en_el_resumen_no_se_da_por_completado(
    settings, workspace, scripted_restic
):
    scripted_restic.reply("check", out=summary(num_errors=1, broken_packs=None), code=0)

    result = ens(settings, "backup", "check")

    assert result.exit_code == 1
    assert only_check(workspace).status == "failed"


@pytest.mark.parametrize(
    ("options", "scope", "percent", "restic_args"),
    [
        ([], "metadata", 0, ["check", "--json"]),
        (
            ["--read-data-subset", "25%"],
            "subset:25%",
            25,
            ["check", "--json", "--read-data-subset", "25%"],
        ),
        (["--read-data"], "all", 100, ["check", "--json", "--read-data"]),
    ],
)
def test_check_registra_el_alcance_exacto_que_se_ejecuto(
    settings, workspace, scripted_restic, options, scope, percent, restic_args
):
    scripted_restic.reply("check", out=summary(num_errors=0, broken_packs=None))

    result = ens(settings, "backup", "check", *options)

    assert result.exit_code == 0, result.output
    check = only_check(workspace)
    assert (check.scope, check.read_data_percent) == (scope, percent)
    assert restic_args in scripted_restic.calls()


@pytest.mark.parametrize(
    "options",
    [
        ["--read-data-subset", "5x"],
        ["--read-data-subset", "0%"],
        ["--read-data-subset", "101%"],
        ["--read-data-subset", "1/5"],
        ["--read-data-subset", "10%", "--read-data"],
    ],
)
def test_check_rechaza_un_alcance_no_comparable_sin_ejecutar_ni_registrar(
    settings, workspace, scripted_restic, options
):
    result = ens(settings, "backup", "check", *options)

    assert result.exit_code != 0
    assert list_checks(workspace) == ([], [])
    assert not any("check" in call for call in scripted_restic.calls())


def test_check_registra_inicio_y_fin_en_utc_sin_futuro(settings, workspace, scripted_restic):
    scripted_restic.reply("check", out=summary(num_errors=0, broken_packs=None))
    before = datetime.now(UTC)

    ens(settings, "backup", "check")

    check = only_check(workspace)
    assert check.started_at.utcoffset() == timedelta(0)
    assert check.finished_at is not None and check.finished_at.utcoffset() == timedelta(0)
    assert before <= check.started_at <= check.finished_at <= datetime.now(UTC)


class InterruptedRestic:
    def check(self, read_data_subset=None, read_data=False):
        raise KeyboardInterrupt


class UnavailableRestic:
    def check(self, read_data_subset=None, read_data=False):
        raise BackupError(f"restic no está instalado {CANARY}")


def test_check_interrumpido_deja_el_intento_en_started(workspace):
    with pytest.raises(KeyboardInterrupt):
        run_check(workspace, InterruptedRestic(), REPOSITORY_ID, lambda: datetime.now(UTC))

    check = only_check(workspace)
    assert (check.status, check.finished_at, check.exit_code) == ("started", None, None)


def test_check_con_restic_no_disponible_queda_failed_sin_citar_el_error(workspace):
    check = run_check(workspace, UnavailableRestic(), REPOSITORY_ID, lambda: datetime.now(UTC))

    assert (check.status, check.failure) == ("failed", "restic_unavailable")
    assert CANARY not in workspace.read_text(check_key(check.check_id))


def test_cada_intento_es_un_registro_nuevo_y_no_pisa_los_anteriores(
    settings, workspace, scripted_restic
):
    scripted_restic.reply("check", out=summary(num_errors=0, broken_packs=None))
    ens(settings, "backup", "check")
    scripted_restic.reply("check", out=summary(num_errors=1, broken_packs=None), code=1)
    ens(settings, "backup", "check")

    checks, unreadable = list_checks(workspace)

    assert unreadable == []
    assert [c.status for c in checks] == ["completed", "failed"]
