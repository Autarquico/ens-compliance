from datetime import timedelta

import pytest
import yaml
from scope.scope_helpers import edit
from typer.testing import CliRunner

from ens_evidence.checks.store import scan_runs
from ens_evidence.cli import app
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.evidence.importer import import_collected
from ens_evidence.evidence.layout import CHECKS_PREFIX

from .test_engine import (
    COLLECTED,
    GITHUB_RESULTS,
    LIVE,
    NOW,
    SECRET,
    collect,
    crash_in,
    gh_evidence,
    store,  # noqa: F401  (fixture)
    write_config,
)

FOOTER = "Un check en PASS no da por implantada ninguna medida"


@pytest.fixture
def cli(store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(store._root)}}})
    )

    def run(*args: str):
        return CliRunner().invoke(app, ["--settings", str(settings), *args])

    return run


def collect_with_secret_scanning_off(workspace, repo="infra", *, at=COLLECTED):
    evidence = gh_evidence(LIVE, collected_at=at)
    for item in evidence.data["sections"]["secret_scanning"]["items"]:
        if item["repository"] == repo:
            item["state"] = "disabled"
    import_collected(workspace, evidence, collector_version="1", now=at)


def test_sin_all_falla_con_2(cli):
    result = cli("check")

    assert result.exit_code == 2
    assert "Indica --all" in result.output


def test_no_tiene_opcion_at(cli):
    assert cli("check", "--all", "--at", "2026-01-01T00:00:00+00:00").exit_code == 2


def test_con_el_alcance_cambiado_tras_aprobarlo_sale_con_2(cli, store):  # noqa: F811
    edit(store, "assets", "repos: [facturas, infra]", "repos: [facturas, infra, nuevo]")

    result = cli("check", "--all")

    assert result.exit_code == 2
    assert "alcance no está aprobado" in result.output
    assert not store.list(CHECKS_PREFIX)


def test_todo_pass_sale_con_0_y_lista_los_conectores_no_configurados(cli, store):  # noqa: F811
    collect(store)

    result = cli("check", "--all")

    assert result.exit_code == 0, result.output
    assert "github: ejecución escrita evidence/checks/github/000001-" in result.output
    assert f"PASS {GITHUB_RESULTS}  FAIL 0  UNKNOWN 0  ERROR 0" in result.output
    for connector in ("cloudflare", "endpoint", "google-workspace", "restic"):
        assert f"{connector}: no configurado" in result.output
    assert FOOTER in result.output
    assert "Siguiente paso" not in result.output


def test_un_fail_sale_con_4_y_nombra_el_resultado(cli, store):  # noqa: F811
    collect_with_secret_scanning_off(store)

    result = cli("check", "--all")

    assert result.exit_code == 4
    assert f"FAIL {SECRET} infra" in result.output
    assert f"PASS {GITHUB_RESULTS - 1}  FAIL 1" in result.output
    assert FOOTER in result.output
    assert "Siguiente paso: corrige lo que falla, vuelve a recoger y ejecuta" in result.output
    assert "ens-evidence assessment status" in result.output


def test_sin_evidencia_da_unknown_y_sale_con_3(cli):
    result = cli("check", "--all")

    assert result.exit_code == 3
    assert f"UNKNOWN {SECRET} infra (no_evidence)" in result.output
    assert "PASS 0" in result.output


def test_un_fail_manda_sobre_un_unknown_de_otro_conector(cli, store):  # noqa: F811
    collect_with_secret_scanning_off(store)
    write_config(
        store,
        "cloudflare",
        {"asset_id": "cf-taller", "account_id": "0123456789abcdef0123456789abcdef"},
    )

    result = cli("check", "--all")

    assert result.exit_code == 4
    assert "cloudflare: ejecución escrita" in result.output


def test_un_check_que_falla_con_excepcion_sale_con_3_sin_su_mensaje(cli, store, monkeypatch):  # noqa: F811
    collect(store)
    crash_in(monkeypatch, "github", SECRET, message="token-secreto-123")

    result = cli("check", "--all")

    assert result.exit_code == 3
    assert f"check_crashed:{SECRET}:RuntimeError" in result.output
    assert "token-secreto-123" not in result.output


def test_repetir_sin_cambios_no_escribe_y_dice_desde_que_ejecucion(cli, store):  # noqa: F811
    collect(store)
    cli("check", "--all")
    first = scan_runs(store).last("github").key

    result = cli("check", "--all")

    assert result.exit_code == 0
    assert f"github: sin cambios desde {first}" in result.output
    assert len(scan_runs(store).runs["github"]) == 1


def test_una_recogida_nueva_añade_una_ejecucion(cli, store):  # noqa: F811
    collect(store)
    cli("check", "--all")
    collect_with_secret_scanning_off(store, at=COLLECTED + timedelta(hours=1))

    result = cli("check", "--all")

    assert result.exit_code == 4
    assert "github: ejecución escrita evidence/checks/github/000002-" in result.output


def test_sin_ningun_conector_configurado_no_es_un_exito(cli, store):  # noqa: F811
    (store._root / connector_config_key("github")).unlink()

    result = cli("check", "--all")

    assert result.exit_code == 3
    assert "Ningún conector configurado" in result.output
    assert not store.list(CHECKS_PREFIX)


def test_una_configuracion_invalida_sale_con_2(cli, store):  # noqa: F811
    store.write_text(connector_config_key("github"), "asset_id: [roto")

    result = cli("check", "--all")

    assert result.exit_code == 2
    assert "config_invalid" in result.output


def test_un_historial_ilegible_sale_con_2_y_no_se_amplia(cli, store):  # noqa: F811
    collect(store)
    cli("check", "--all")
    key = scan_runs(store).last("github").key
    store.write_text(key, "no es json")
    before = store.list(CHECKS_PREFIX)

    result = cli("check", "--all")

    assert result.exit_code == 2
    assert "no se registra la ejecución" in result.output
    assert key in result.output
    assert store.list(CHECKS_PREFIX) == before


def test_no_hay_conformidad_en_la_salida(cli, store):  # noqa: F811
    collect(store)

    output = cli("check", "--all").output.lower()

    assert "conforme" not in output and "cumple" not in output


def test_sin_evidencia_el_paso_es_recoger_y_no_corregir_lo_que_falla(cli):
    result = cli("check", "--all")

    assert "Siguiente paso: recoge con `ens-evidence collect github`" in result.output
    assert "corrige lo que falla" not in result.output
    assert FOOTER in result.output


def test_el_rechazo_por_reloj_solo_da_su_paso_y_no_el_pie_generico(cli, store, monkeypatch):  # noqa: F811
    collect_with_secret_scanning_off(store)
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW + timedelta(days=2))
    assert cli("check", "--all").exit_code == 4
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: NOW)

    result = cli("check", "--all")

    assert result.exit_code == 2
    assert "--clock-corrected" in result.output
    assert result.output.count("Siguiente paso") == 1
    assert FOOTER not in result.output
