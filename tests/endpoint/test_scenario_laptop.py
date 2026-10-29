"""Scenario: evidencia de un portátil (script + `evidence import --source endpoint`)."""

import hashlib
from datetime import timedelta

from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence import list_manifests
from ens_evidence.models.contracts import ProvenanceKind

from .support import collect, members_of, normalized

runner = CliRunner()


def ens(settings, *args):
    return runner.invoke(app, ["--settings", str(settings), *args])


def import_cli(settings, archive, asset="lap-ana"):
    return ens(
        settings,
        *("evidence", "import", str(archive)),
        *("--source", "endpoint", "--asset", asset, "--by", "ana"),
    )


def test_aparece_la_evidencia_normalizada_y_el_tar_con_hash(tmp_path, settings, store):
    run = collect(tmp_path / "run")

    result = import_cli(settings, run.archive)

    assert result.exit_code == 0, result.output
    manifests = {m.source: m for m in list_manifests(store).manifests}
    assert set(manifests) == {"endpoint-script", "endpoint-package"}
    package = manifests["endpoint-package"]
    assert package.artifact.sha256 == hashlib.sha256(run.data).hexdigest()
    assert store.read_bytes(package.artifact.key) == run.data
    assert package.artifact.sha256 in result.output
    assert normalized(store)["source"] == "endpoint-script"


def test_ambas_evidencias_son_manuales_con_autor_declarado_y_caducidad_configurada(
    tmp_path, settings, store
):
    run = collect(tmp_path / "run")

    import_cli(settings, run.archive)

    manifests = list_manifests(store).manifests
    assert len(manifests) == 2
    for manifest in manifests:
        assert manifest.provenance.kind is ProvenanceKind.MANUAL
        assert manifest.provenance.submitted_by == "ana"
        assert manifest.asset_id == "lap-ana"
        assert manifest.collected_at.isoformat().replace("+00:00", "Z") == run.meta["collected_at"]
        assert manifest.valid_until == manifest.collected_at + timedelta(days=30)
        assert manifest.collector is not None
        assert manifest.collector.name == "endpoint-script"


def test_la_integridad_de_lo_importado_se_verifica_sin_huerfanos(tmp_path, settings, store):
    import_cli(settings, collect(tmp_path / "run").archive)

    result = ens(settings, "evidence", "verify", "--all")

    assert result.exit_code == 0, result.output


def test_un_servidor_linux_se_importa_con_sus_consultas_de_plataforma(
    tmp_path, settings, store, server_package
):
    path = tmp_path / "srv.tar.gz"
    path.write_bytes(server_package)

    result = import_cli(settings, path, asset="srv-app")

    assert result.exit_code == 0, result.output
    assert normalized(store)["data"]["package"]["platform"] == "linux"


def test_el_nombre_de_quien_aporta_se_declara_no_se_autentica(tmp_path, settings, store):
    result = import_cli(settings, collect(tmp_path / "run").archive)

    assert "atestación manual" in result.output
    assert "no se autentica" in result.output


def test_collected_at_y_valid_until_no_se_aceptan_por_la_linea_de_comandos(
    tmp_path, settings, store
):
    run = collect(tmp_path / "run")

    result = ens(
        settings,
        *("evidence", "import", str(run.archive), "--source", "endpoint"),
        *("--asset", "lap-ana", "--by", "ana", "--valid-until", "2099-01-01T00:00:00+00:00"),
    )

    assert result.exit_code == 2
    assert store.list("evidence") == []


def test_el_paquete_importado_conserva_los_resultados_sin_alterarlos(tmp_path, settings, store):
    run = collect(tmp_path / "run")

    import_cli(settings, run.archive)

    package_hash = hashlib.sha256(run.data).hexdigest()
    assert normalized(store)["data"]["package"]["sha256"] == package_hash
    assert set(members_of(run.data)) >= {"meta.json", "SHA256SUMS"}


def test_reimportar_el_mismo_paquete_dice_cual_era_la_evidencia_y_sale_con_error(
    tmp_path, settings, store
):
    run = collect(tmp_path / "run")
    first = import_cli(settings, run.archive)
    assert first.exit_code == 0, first.output

    again = import_cli(settings, run.archive)

    assert again.exit_code == 2
    assert "ya estaba importado como" in again.output
    assert len(list_manifests(store).manifests) == 2
