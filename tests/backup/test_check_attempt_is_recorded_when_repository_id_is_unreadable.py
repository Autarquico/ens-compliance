"""Un `backup check` deja registro aunque no pueda leer el id del repositorio."""

from ens_evidence.backup import list_checks

from .conftest import PASSWORD, ens


def test_check_con_el_repositorio_ilegible_registra_un_intento_failed_sin_id(
    settings, workspace, scripted_restic
):
    scripted_restic.reply("cat", err=PASSWORD, code=12)

    result = ens(settings, "backup", "check")

    assert result.exit_code == 1
    (check,) = list_checks(workspace)[0]
    assert (check.status, check.failure, check.repository_id) == (
        "failed",
        "restic_unavailable",
        None,
    )
    assert PASSWORD not in result.output


def test_check_con_el_repositorio_ilegible_no_ejecuta_restic_check(settings, scripted_restic):
    scripted_restic.reply("cat", err=PASSWORD, code=12)

    ens(settings, "backup", "check")

    assert not any("check" in call for call in scripted_restic.calls())
