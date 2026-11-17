from datetime import UTC, datetime
from importlib.metadata import entry_points

import pytest
import yaml

from ens_evidence.checks.registry import CONFIG_MODELS
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.connectors.registry import ENTRY_POINT_GROUP, ConnectorRegistry
from ens_evidence.scope.inventory import ASSETS_KEY
from ens_evidence.scope.models import REQUIRED_FIELDS, AssetType
from ens_evidence.ui.views.conectores_catalogo import (
    CATALOGUE,
    EDITABLE,
    catalogue_for,
    configs_citing,
    document_to_values,
    form_fields_to_document,
)
from ens_evidence.ui.views.help import RUNBOOKS
from ens_evidence.workspace import LocalStore

SPECS = list(CATALOGUE.values())
EDITABLE_SPECS = [CATALOGUE[name] for name in EDITABLE]


def registered():
    registry = ConnectorRegistry()
    registry.load_entry_points()
    return registry


def test_el_catalogo_coincide_con_los_entry_points():
    assert set(CATALOGUE) == {ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)}


def test_el_catalogo_coincide_con_los_modelos_de_configuracion_sin_endpoint():
    assert CATALOGUE.keys() == CONFIG_MODELS.keys() - {"endpoint"}
    assert all(CONFIG_MODELS[name] is spec.model for name, spec in CATALOGUE.items())


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_los_campos_mas_los_conservados_son_los_del_modelo(spec):
    names = [f.name for f in spec.fields]
    assert len(names) == len(set(names))
    assert set(names) | spec.preserved == set(spec.model.model_fields)
    assert not set(names) & spec.preserved


def test_solo_backups_es_de_solo_lectura():
    assert set(CATALOGUE) - set(EDITABLE) == {"backups"}
    assert CATALOGUE["backups"].status_href == "/copias"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_cada_ficha_enlaza_a_una_doc_servida(spec):
    assert spec.doc_href == f"/ayuda/conector-{spec.name}"
    assert spec.doc_href.removeprefix("/ayuda/") in RUNBOOKS


def test_las_credenciales_salen_de_los_conectores():
    assert CATALOGUE["github"].credential.names == ("ENS_GITHUB_TOKEN",)
    assert CATALOGUE["cloudflare"].credential.names == ("ENS_CLOUDFLARE_API_TOKEN",)
    assert CATALOGUE["google-workspace"].credential.names == ("ENS_GOOGLE_SERVICE_ACCOUNT_KEY",)
    assert CATALOGUE["keystore"].credential.names == ("ENS_KEYSTORE_TOKEN",)
    assert CATALOGUE["passwords"].credential.kind == "none"
    assert CATALOGUE["restic"].credential.kind == "settings"
    assert "ENS_BACKUPS_<ID>_RESTIC_PASSWORD" in CATALOGUE["backups"].credential.names


# --- el tipo de activo coincide con lo que exige `prepare` ---------------------------------------

VALID = {
    "github": {"asset_id": "a", "organization": "acme"},
    "cloudflare": {"asset_id": "a", "account_id": "0" * 32},
    "google-workspace": {"asset_id": "a", "admin_subject": "x@acme.com", "domains": ["acme.com"]},
    "keystore": {"asset_id": "a", "max_key_age_days": 90},
    "passwords": {"asset_id": "a", "inactive_days": 90, "max_export_age_minutes": 30},
    "restic": {
        "asset_id": "a",
        "max_backup_age_hours": 24,
        "max_check_age_days": 7,
        "restore_test_validity_days": 90,
        "min_check_read_data_percent": 5,
    },
}


def filled(asset_type):
    """Un activo con todos los campos que su tipo exige, con un valor de la forma que espera."""
    lists, flags = {"repos", "zones", "buckets"}, {"mfa", "encrypted"}
    required = REQUIRED_FIELDS[AssetType(asset_type)]
    return {name: ["x"] if name in lists else True if name in flags else "x" for name in required}


def store_with_asset(tmp_path, asset_type):
    store = LocalStore(tmp_path)
    asset = {"id": "a", "type": asset_type, "deployment": "deployed"} | filled(asset_type)
    store.write_text(ASSETS_KEY, yaml.safe_dump({"assets": [asset]}))
    return store


@pytest.mark.parametrize("spec", EDITABLE_SPECS, ids=lambda s: s.name)
def test_el_tipo_de_activo_de_la_ficha_es_el_que_exige_prepare(spec, tmp_path):
    connector = registered().get(spec.name)()
    config = spec.model.model_validate(VALID[spec.name])

    accepted = connector.prepare(config, store_with_asset(tmp_path / "ok", spec.asset_type.value))
    assert accepted.asset_id == "a"

    other = "storage" if spec.asset_type.value != "storage" else "provider"
    with pytest.raises(Exception, match="asset_id"):
        connector.prepare(config, store_with_asset(tmp_path / "ko", other))


# --- formulario -> documento -----------------------------------------------------------------


def test_los_enteros_se_convierten_y_el_documento_valida_con_el_modelo():
    spec = CATALOGUE["restic"]
    form = {
        "asset_id": "a",
        "max_backup_age_hours": " 24 ",
        "max_check_age_days": "7",
        "restore_test_validity_days": "90",
        "min_check_read_data_percent": "0",
    }

    document, errors = form_fields_to_document(spec, form)

    assert errors == {}
    assert document["max_backup_age_hours"] == 24
    assert spec.model.model_validate(document).min_check_read_data_percent == 0


def test_las_lineas_pasan_a_lista_sin_blancos():
    document, errors = form_fields_to_document(
        CATALOGUE["google-workspace"],
        {"asset_id": "a", "admin_subject": "x@acme.com", "domains": " acme.com \n\n  otro.es\r\n"},
    )

    assert errors == {}
    assert document["domains"] == ["acme.com", "otro.es"]
    assert "customer" not in document


def test_un_opcional_vacio_es_none_si_el_modelo_lo_admite_y_si_no_se_omite():
    document, errors = form_fields_to_document(
        CATALOGUE["cloudflare"], {"asset_id": "a", "account_id": "0" * 32, "ui_hostname": "  "}
    )

    assert errors == {}
    assert document == {"asset_id": "a", "account_id": "0" * 32, "ui_hostname": None}
    assert CATALOGUE["cloudflare"].model.model_validate(document).token_kind == "account"


@pytest.mark.parametrize("bad", ["abc", "1.5", "-3", "+4", "", "١٢", "1" * 10])
def test_un_entero_mal_formado_da_error_sin_repetir_el_valor(bad):
    form = dict.fromkeys(VALID["keystore"], "x") | {"max_key_age_days": bad}
    _, errors = form_fields_to_document(CATALOGUE["keystore"], form)

    assert errors["max_key_age_days"]
    assert bad not in " ".join(errors["max_key_age_days"]) or bad == ""


def test_una_opcion_fuera_de_las_permitidas_da_error_sin_repetirla():
    _, errors = form_fields_to_document(
        CATALOGUE["cloudflare"],
        {"asset_id": "a", "account_id": "0" * 32, "token_kind": "root-secreto"},
    )

    assert errors == {"token_kind": ["Valor no permitido."]}


def test_los_obligatorios_vacios_dan_error_por_campo():
    _, errors = form_fields_to_document(CATALOGUE["github"], {})

    assert set(errors) == {"asset_id", "organization"}


# --- documento -> valores ----------------------------------------------------------------------


def test_los_valores_de_relleno_unen_listas_y_pasan_enteros_a_texto():
    values = document_to_values(
        CATALOGUE["google-workspace"],
        {"asset_id": "a", "domains": ["a.com", "b.es"], "admin_subject": None, "extra": "x"},
    )
    assert values == {
        "asset_id": "a",
        "customer": "",
        "admin_subject": "",
        "domains": "a.com\nb.es",
    }
    assert (
        document_to_values(CATALOGUE["keystore"], {"max_key_age_days": 90})["max_key_age_days"]
        == "90"
    )
    assert document_to_values(CATALOGUE["github"], {"organization": {"a": 1}})["organization"] == ""


# --- configs_citing ----------------------------------------------------------------------------


def test_configs_citing_encuentra_conectores_que_citan_el_activo(tmp_path):
    store = LocalStore(tmp_path)
    store.write_text(connector_config_key("github"), "asset_id: gh\norganization: acme\n")
    store.write_text(connector_config_key("keystore"), "asset_id: ks\n")
    store.write_text(
        connector_config_key("backups"), "repositories:\n  - id: uno\n    asset_id: gh\n"
    )
    store.write_text(connector_config_key("restic"), "{{ no es yaml")
    store.write_text(connector_config_key("passwords"), "- lista\n")

    assert configs_citing(store, "gh") == ("backups", "github")
    assert configs_citing(store, "otro") == ()


# --- registro en tiempo de ejecución (D12) ----------------------------------------------------


def test_un_conector_registrado_sin_ficha_sale_generico_y_de_solo_lectura():
    specs = catalogue_for(["github", "zeta-nuevo", "backups"])

    assert [s.name for s in specs] == ["backups", "github", "zeta-nuevo"]
    assert specs[1] is CATALOGUE["github"]
    generic = specs[2]
    assert not generic.editable and generic.fields == () and generic.model is None
    assert generic.doc_href is None and generic.credential is None


def test_el_catalogo_cubre_todo_lo_registrado():
    assert all(s.model is not None for s in catalogue_for(registered().names()))


NOW = datetime(2026, 11, 17, tzinfo=UTC)
