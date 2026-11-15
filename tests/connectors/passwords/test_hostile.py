"""Un export hostil: se rechaza sin guardarlo ni citarlo."""

import json
import logging

import pytest

from ens_evidence.connectors.passwords import reader
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scheduling.collect import _leaked_paths

from .conftest import export_doc, user

SECRET = "hunter2-SECRETO-no-debe-salir-9f3a"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"


def everything(result, caplog) -> str:
    return result.model_dump_json() + caplog.text


@pytest.mark.parametrize("marker", [SECRET, "ana@example.com", KEY])
def test_un_campo_email_extra_se_rechaza_y_no_se_guarda(collect, caplog, marker):
    caplog.set_level(logging.DEBUG)
    bad = {**user(), "email": marker}
    result = collect(export_doc([bad]))

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"] == reader.INVALID
    assert marker not in everything(result, caplog)
    assert "email" not in everything(result, caplog)


def test_campo_extra_a_nivel_raiz_se_rechaza(collect, caplog):
    result = collect(export_doc(ciphers=[{"data": SECRET}]))

    assert result.data["error"] == reader.INVALID
    assert SECRET not in everything(result, caplog)


@pytest.mark.parametrize("field", ["id", "created_at", "last_active"])
def test_un_secreto_en_un_campo_permitido_no_llega_a_nada(collect, caplog, field):
    caplog.set_level(logging.DEBUG)
    result = collect(export_doc([user(**{field: SECRET})]))

    assert result.data["error"] == reader.INVALID
    assert SECRET not in everything(result, caplog)


def test_un_secreto_en_la_migracion_valida_no_se_copia_a_la_evidencia(collect, caplog):
    result = collect(export_doc(vaultwarden_migration=SECRET))

    assert result.collection_status is CollectionStatus.SUCCESS
    assert SECRET not in everything(result, caplog)


def test_el_error_no_lleva_valores_de_entrada(collect, tmp_path):
    result = collect(raw=json.dumps({"format": SECRET, "users": SECRET}).encode())

    assert SECRET not in result.model_dump_json()
    assert set(result.data) == {"thresholds", "error"}


def test_la_ruta_no_se_cita_en_la_evidencia(collect, tmp_path):
    missing = tmp_path / "no-existe-xyz.json"

    assert "no-existe-xyz" not in collect(export_path=str(missing)).model_dump_json()


def test_la_evidencia_buena_pasa_la_frontera_de_secretos(collect):
    assert _leaked_paths(collect(export_doc([user(), user(last_active=None)]))) == []
