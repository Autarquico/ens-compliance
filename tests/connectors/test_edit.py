import threading
import time

import pytest
import yaml

from ens_evidence.connectors import edit
from ens_evidence.connectors.cloudflare import CloudflareConfig
from ens_evidence.connectors.config import (
    ConnectorConfigError,
    credential_fields,
    load_connector_config,
    reject_config_credentials,
)
from ens_evidence.connectors.edit import ConnectorEditRejected, inspect, save_connector_config
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.connectors.restic.config import ResticConfig
from ens_evidence.workspace import LocalStore, WorkspaceError

SECRET = "ghp_" + "A1b2C3d4E5" * 4
ACCOUNT = "0123456789abcdef0123456789abcdef"
GITHUB = {"asset_id": "gh-org", "organization": "acme"}
ROUTE = {"hostname": "a.example", "path": "/agent/*", "reason": "pública por diseño"}
RESTIC = {
    "asset_id": "bk",
    "max_backup_age_hours": 26,
    "max_check_age_days": 35,
    "restore_test_validity_days": 90,
    "min_check_read_data_percent": 5,
}


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path)


def put(store, name, document) -> None:
    text = document if isinstance(document, str) else yaml.safe_dump(document)
    store.write_text(f"connectors/{name}.yaml", text)


def text(store, name) -> str:
    return store.read_text(f"connectors/{name}.yaml")


def save(store, name, model, fields, replace=False) -> bool:
    return save_connector_config(store, name, model, fields, replace_invalid=replace)


def rejected(store, name, model, fields, replace=False) -> ConnectorEditRejected:
    with pytest.raises(ConnectorEditRejected) as error:
        save(store, name, model, fields, replace)
    return error.value


def test_politica_unica_token_kind_permitido_pero_su_valor_no_puede_ser_un_secreto():
    reject_config_credentials({"token_kind": "user"})
    assert credential_fields({"token_kind": SECRET, "asset_id": "x"}) == ("token_kind",)


def test_politica_marca_claves_de_credencial_y_valores_sin_citar_el_valor():
    assert credential_fields({"api_token": "x", "organization": SECRET, "ok": "v"}) == (
        "api_token",
        "organization",
    )
    with pytest.raises(ConnectorConfigError) as error:
        reject_config_credentials({"organization": SECRET})
    assert SECRET not in str(error.value)


def test_inspect_distingue_ausente_ilegible_invalido_y_valido(store):
    assert inspect(store, "github", GitHubConfig).state == "absent"
    put(store, "github", "a: [unclosed")
    assert inspect(store, "github", GitHubConfig).state == "unparseable"
    put(store, "github", "- lista")
    assert inspect(store, "github", GitHubConfig).state == "unparseable"
    put(store, "github", {"asset_id": "x"})
    assert inspect(store, "github", GitHubConfig).state == "invalid"
    put(store, "github", GITHUB)
    assert inspect(store, "github", GitHubConfig).state == "valid"


def test_guarda_fichero_nuevo_que_el_cargador_acepta(store):
    assert save(store, "github", GitHubConfig, GITHUB) is True
    assert load_connector_config(store, "github", GitHubConfig).organization == "acme"


def test_guardado_valido_de_cloudflare_con_token_kind(store):
    fields = {"asset_id": "cf", "account_id": ACCOUNT, "token_kind": "user", "ui_hostname": None}
    assert save(store, "cloudflare", CloudflareConfig, fields) is True
    loaded = load_connector_config(store, "cloudflare", CloudflareConfig)
    assert loaded.token_kind == "user"
    assert "ui_hostname" not in yaml.safe_load(text(store, "cloudflare"))


def test_conserva_claves_no_editables_de_un_fichero_valido(store):
    put(store, "cloudflare", {"asset_id": "cf", "account_id": ACCOUNT, "public_routes": [ROUTE]})
    fields = {"asset_id": "cf2", "account_id": ACCOUNT, "token_kind": "account"}

    assert save(store, "cloudflare", CloudflareConfig, fields) is True

    saved = yaml.safe_load(text(store, "cloudflare"))
    assert saved["public_routes"] == [ROUTE] and saved["asset_id"] == "cf2"


def test_conserva_offsite_de_restic(store):
    offsite = {
        "location": "s3",
        "administrative_separation": "otra cuenta",
        "reviewer": "ana",
        "role": "system_owner",
        "reviewed_at": "2026-01-01T00:00:00+00:00",
        "valid_until": "2027-01-01T00:00:00+00:00",
    }
    put(store, "restic", {**RESTIC, "offsite": offsite})
    fields = {**RESTIC, "max_backup_age_hours": 24}

    save(store, "restic", ResticConfig, fields)
    assert yaml.safe_load(text(store, "restic"))["offsite"] == offsite


def test_enteros_estrictos_se_guardan_como_enteros(store):
    save(store, "restic", ResticConfig, RESTIC)
    assert yaml.safe_load(text(store, "restic"))["max_backup_age_hours"] == 26


def test_sin_cambios_devuelve_false_y_no_escribe(store, monkeypatch):
    save(store, "github", GitHubConfig, GITHUB)
    monkeypatch.setattr(store, "write_text", lambda *a: pytest.fail("escribió"))
    assert save(store, "github", GitHubConfig, GITHUB) is False


@pytest.mark.parametrize(
    ("fields", "flagged"),
    [
        ({"asset_id": "gh", "organization": SECRET}, "organization"),
        ({"asset_id": SECRET, "organization": "acme"}, "asset_id"),
        ({**GITHUB, "api_token": "x"}, "api_token"),
    ],
)
def test_secreto_en_el_formulario_se_rechaza_sin_escribir_ni_citar_el_valor(store, fields, flagged):
    error = rejected(store, "github", GitHubConfig, fields)

    assert list(error.errors) == [flagged]
    assert SECRET not in repr((error.errors, error.general, str(error)))
    assert inspect(store, "github", GitHubConfig).state == "absent"


@pytest.mark.parametrize(
    ("model", "name", "field", "value"),
    [
        (ResticConfig, "restic", "max_backup_age_hours", f"7 {SECRET}"),
        (GoogleWorkspaceConfig, "google-workspace", "domains", ["a.example", SECRET]),
        (CloudflareConfig, "cloudflare", "token_kind", SECRET),
    ],
)
def test_secreto_en_entero_lista_u_opcion_se_marca(store, model, name, field, value):
    error = rejected(store, name, model, {field: value})

    assert list(error.errors) == [field]
    assert SECRET not in repr(error.errors)
    assert inspect(store, name, model).state == "absent"


def test_errores_de_validacion_solo_por_campo_recibido_y_sin_valores(store):
    fields = {"asset_id": "gh", "organization": "no valido!"}

    error = rejected(store, "github", GitHubConfig, fields)

    assert error.errors == {"organization": ["No tiene el formato esperado."]}
    assert error.general == []
    assert "no valido" not in repr(error.errors)


def test_clave_desconocida_en_fichero_valido_imposible_pero_loc_ajeno_es_general(store):
    # La combinación (loc vacío) no cita ningún campo ni valor.
    fields = {"asset_id": "g", "admin_subject": "ana@fuera.example", "domains": ["dentro.example"]}

    error = rejected(store, "google-workspace", GoogleWorkspaceConfig, fields)

    assert error.errors == {}
    assert error.general == [edit.GENERAL_INVALID]


def test_fichero_invalido_exige_confirmacion_y_no_se_toca(store):
    put(store, "github", {"asset_id": "x"})
    before = text(store, "github")

    error = rejected(store, "github", GitHubConfig, GITHUB)

    assert error.general == [edit.CONFIRM_REQUIRED]
    assert text(store, "github") == before


def test_fichero_ilegible_exige_confirmacion_y_con_ella_se_sustituye(store):
    put(store, "github", "a: [unclosed")
    rejected(store, "github", GitHubConfig, GITHUB)

    assert save(store, "github", GitHubConfig, GITHUB, replace=True) is True
    assert load_connector_config(store, "github", GitHubConfig).asset_id == "gh-org"


def test_reemplazo_confirmado_descarta_clave_extra_y_de_credencial(store):
    put(
        store,
        "cloudflare",
        {
            "asset_id": "viejo",
            "account_id": ACCOUNT,
            "public_routes": [ROUTE],
            "extra": 1,
            "api_token": SECRET,
        },
    )
    fields = {"asset_id": "cf", "account_id": ACCOUNT}

    assert save(store, "cloudflare", CloudflareConfig, fields, replace=True) is True

    saved = yaml.safe_load(text(store, "cloudflare"))
    assert saved == {"asset_id": "cf", "account_id": ACCOUNT, "public_routes": [ROUTE]}
    assert SECRET not in text(store, "cloudflare")


def test_claves_conservadas_invalidas_bloquean_con_mensaje_fijo_del_campo(store):
    bad_route = {"hostname": "a.example", "reason": "  ", "path": "/x"}
    put(store, "cloudflare", {"asset_id": "x", "public_routes": [bad_route], "extra": "y"})
    before = text(store, "cloudflare")

    error = rejected(
        store, "cloudflare", CloudflareConfig, {"asset_id": "cf", "account_id": ACCOUNT}, True
    )

    assert error.general == [edit.fix_by_hand("public_routes")]
    assert "extra" not in repr(error.general)
    assert text(store, "cloudflare") == before


def test_si_el_fichero_se_arregla_antes_el_reemplazo_no_es_ciego(store):
    put(store, "cloudflare", {"asset_id": "cf", "account_id": ACCOUNT, "public_routes": [ROUTE]})

    save(store, "cloudflare", CloudflareConfig, {"asset_id": "cf2", "account_id": ACCOUNT}, True)

    assert yaml.safe_load(text(store, "cloudflare"))["public_routes"] == [ROUTE]


def test_fallo_de_escritura_deja_el_fichero_anterior_y_no_filtra_texto(store, monkeypatch):
    save(store, "github", GitHubConfig, GITHUB)
    before = text(store, "github")

    def boom(*_):
        raise WorkspaceError("fallo")

    monkeypatch.setattr(store, "write_text", boom)
    with pytest.raises(WorkspaceError):
        save(store, "github", GitHubConfig, {**GITHUB, "organization": "otra"})
    assert text(store, "github") == before


def test_los_guardados_concurrentes_se_serializan(store, monkeypatch):
    active = []
    overlap = []
    real = store.write_text

    def slow(key, body):
        active.append(1)
        overlap.append(len(active))
        time.sleep(0.05)
        real(key, body)
        active.pop()

    monkeypatch.setattr(store, "write_text", slow)
    threads = [
        threading.Thread(
            target=save, args=(store, "github", GitHubConfig, {**GITHUB, "organization": f"o{i}"})
        )
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max(overlap) == 1
