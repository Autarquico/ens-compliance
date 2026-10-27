import boto3
import pytest
import yaml

from ens_evidence.catalog import load_catalog
from ens_evidence.workspace import LocalStore, S3Store

from .conftest import BUCKET

KEY = "controls/measures.yaml"


@pytest.fixture
def workspace(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


def measures_of(text: str) -> dict:
    return {m["id"]: m for m in yaml.safe_load(text)["measures"]}


def test_controls_init_en_workspace_vacio_deja_cada_medida_sin_determinar(ens, workspace):
    result = ens("controls", "init")

    assert result.exit_code == 0, result.output
    document = yaml.safe_load(workspace.read_text(KEY))
    measures = measures_of(workspace.read_text(KEY))
    assert set(measures) == {m.id for m in load_catalog().measures}
    assert len(measures) == 73
    assert {m["applicability"] for m in measures.values()} == {"UNDETERMINED"}
    assert {m["assessment"] for m in measures.values()} == {"NOT_ASSESSED"}
    version = load_catalog().measures[0].source
    assert document["catalog_version"] == {
        "snapshot_sha256": version.snapshot_sha256,
        "consolidation_date": version.consolidation_date.isoformat(),
    }


def test_controls_init_conserva_los_estados_existentes_y_anade_las_medidas_nuevas(ens, workspace):
    ens("controls", "init")
    document = yaml.safe_load(workspace.read_text(KEY))
    kept = next(m for m in document["measures"] if m["id"] == "op.pl.1")
    kept["applicability"] = "APPLICABLE"
    del document["measures"][-1]
    workspace.write_text(KEY, yaml.safe_dump(document, allow_unicode=True))

    result = ens("controls", "init")

    assert result.exit_code == 0, result.output
    measures = measures_of(workspace.read_text(KEY))
    assert len(measures) == 73
    assert measures["op.pl.1"]["applicability"] == "APPLICABLE"
    assert "Creadas 1 medidas" in result.output


def test_controls_init_repetido_no_cambia_el_fichero(ens, workspace):
    ens("controls", "init")
    before = workspace.read_text(KEY)

    result = ens("controls", "init")

    assert result.exit_code == 0, result.output
    assert workspace.read_text(KEY) == before
    assert "ya existían" in result.output


def test_controls_init_rechaza_ids_ajenos_al_catalogo_sin_escribir(ens, workspace):
    ens("controls", "init")
    document = yaml.safe_load(workspace.read_text(KEY))
    document["measures"].append(
        {"id": "xx.fake.1", "applicability": "UNDETERMINED", "assessment": "NOT_ASSESSED"}
    )
    tampered = yaml.safe_dump(document, allow_unicode=True)
    workspace.write_text(KEY, tampered)

    result = ens("controls", "init")

    assert result.exit_code == 1
    assert "xx.fake.1" in result.output
    assert "no está en el catálogo" in result.output
    assert workspace.read_text(KEY) == tampered


def test_controls_init_con_ids_repetidos_sale_con_1_los_lista_y_no_modifica_el_fichero(
    ens, workspace
):
    ens("controls", "init")
    document = yaml.safe_load(workspace.read_text(KEY))
    document["measures"].append(dict(document["measures"][0]))
    tampered = yaml.safe_dump(document, allow_unicode=True)
    workspace.write_text(KEY, tampered)

    result = ens("controls", "init")

    assert result.exit_code == 1
    assert document["measures"][0]["id"] in result.output
    assert workspace.read_text(KEY) == tampered


def test_controls_init_escribe_las_medidas_en_orden_org_op_mp(ens, workspace):
    ens("controls", "init")

    ids = [m["id"] for m in yaml.safe_load(workspace.read_text(KEY))["measures"]]

    assert ids[0] == "org.1"
    assert ids[-1].startswith("mp.")
    assert ids == [m.id for m in load_catalog().measures]


def test_controls_init_informa_del_cambio_de_version_del_catalogo(ens, workspace):
    ens("controls", "init")
    document = yaml.safe_load(workspace.read_text(KEY))
    document["catalog_version"] = {
        "snapshot_sha256": "0" * 64,
        "consolidation_date": "2020-01-01",
    }
    workspace.write_text(KEY, yaml.safe_dump(document, allow_unicode=True))

    result = ens("controls", "init")

    assert result.exit_code == 0, result.output
    assert "versión del catálogo cambió" in result.output
    assert "2020-01-01" in result.output
    version = yaml.safe_load(workspace.read_text(KEY))["catalog_version"]
    assert version["snapshot_sha256"] == load_catalog().measures[0].source.snapshot_sha256


def test_controls_init_con_fichero_ilegible_no_lo_sobrescribe(ens, workspace):
    workspace.write_text(KEY, "measures: [\n")

    result = ens("controls", "init")

    assert result.exit_code == 1
    assert workspace.read_text(KEY) == "measures: [\n"


def test_controls_init_en_s3_crea_conserva_y_rechaza_ajenos(ens, s3):
    store = S3Store(BUCKET, "org")

    assert ens("controls", "init", settings_file=s3).exit_code == 0
    document = yaml.safe_load(store.read_text(KEY))
    assert len(document["measures"]) == 73
    document["measures"][0]["applicability"] = "APPLICABLE"
    first = document["measures"][0]["id"]
    store.write_text(KEY, yaml.safe_dump(document, allow_unicode=True))

    again = ens("controls", "init", settings_file=s3)

    assert again.exit_code == 0, again.output
    assert measures_of(store.read_text(KEY))[first]["applicability"] == "APPLICABLE"

    document["measures"].append(
        {"id": "xx.fake.1", "applicability": "UNDETERMINED", "assessment": "NOT_ASSESSED"}
    )
    tampered = yaml.safe_dump(document, allow_unicode=True)
    store.write_text(KEY, tampered)

    rejected = ens("controls", "init", settings_file=s3)

    assert rejected.exit_code == 1
    assert store.read_text(KEY) == tampered
    assert boto3.client("s3").list_objects_v2(Bucket=BUCKET)["KeyCount"] == 1
