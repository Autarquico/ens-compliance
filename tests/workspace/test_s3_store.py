import sys

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from ens_evidence.workspace import (
    AccessDenied,
    InvalidKey,
    NotFound,
    S3Store,
    StorageUnavailable,
)

BUCKET = "evidencias"


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield


def test_escribe_lee_y_comprueba_existencia(aws):
    store = S3Store(BUCKET, "org")

    store.write_text("a/b.txt", "hola")

    assert store.read_text("a/b.txt") == "hola"
    assert store.exists("a/b.txt")
    assert not store.exists("a/otro.txt")


def test_leer_clave_inexistente_lanza_not_found():
    with pytest.raises(NotFound):
        S3Store(BUCKET, "org").read_text("nada.txt")


def test_prefijo_org_no_ve_org_otra():
    org = S3Store(BUCKET, "org")
    otra = S3Store(BUCKET, "org-otra")
    org.write_text("a.txt", "1")
    otra.write_text("b.txt", "2")

    assert org.list() == ["a.txt"]
    assert otra.list() == ["b.txt"]
    assert not org.exists("b.txt")
    with pytest.raises(NotFound):
        org.read_text("b.txt")


def test_lista_con_semantica_de_directorio():
    store = S3Store(BUCKET, "org")
    store.write_text("scope/a.txt", "1")
    store.write_text("scope-otro/b.txt", "2")

    assert store.list("scope") == ["scope/a.txt"]
    assert store.list() == ["scope-otro/b.txt", "scope/a.txt"]


def test_lista_mas_de_mil_objetos_recorriendo_todas_las_paginas():
    store = S3Store(BUCKET, "org")
    client = boto3.client("s3")
    for i in range(1005):
        client.put_object(Bucket=BUCKET, Key=f"org/lote/{i:05d}.txt", Body=b"x")

    assert len(store.list("lote")) == 1005


@pytest.mark.parametrize("key", ["../x", "/abs", ""])
def test_claves_invalidas_se_rechazan(key):
    store = S3Store(BUCKET, "org")

    with pytest.raises(InvalidKey):
        store.read_text(key)
    with pytest.raises(InvalidKey):
        store.write_text(key, "x")


def test_prefijo_de_workspace_invalido_se_rechaza():
    with pytest.raises(InvalidKey):
        S3Store(BUCKET, "../otra")


def test_endpoint_url_y_region_se_pasan_al_cliente(monkeypatch):
    captured = {}

    def fake_client(service, **kwargs):
        captured.update(service=service, **kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)

    S3Store(BUCKET, "org", endpoint_url="https://r2.example.com", region="auto")

    assert captured == {
        "service": "s3",
        "endpoint_url": "https://r2.example.com",
        "region_name": "auto",
    }


class _FailingClient:
    def __init__(self, error):
        self._error = error

    def get_object(self, **_kwargs):
        raise self._error

    head_object = get_object


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


def test_acceso_denegado_se_distingue_de_no_encontrado():
    store = S3Store(BUCKET, "org", client=_FailingClient(_client_error("AccessDenied")))

    with pytest.raises(AccessDenied):
        store.read_text("a.txt")
    with pytest.raises(AccessDenied):
        store.exists("a.txt")


def test_403_en_head_es_acceso_denegado_y_no_falso():
    store = S3Store(BUCKET, "org", client=_FailingClient(_client_error("403")))

    with pytest.raises(AccessDenied):
        store.exists("a.txt")


def test_error_de_red_es_storage_unavailable():
    error = EndpointConnectionError(endpoint_url="https://x")
    store = S3Store(BUCKET, "org", client=_FailingClient(error))

    with pytest.raises(StorageUnavailable):
        store.read_text("a.txt")


def test_bucket_inexistente_es_storage_unavailable_en_listado():
    with pytest.raises(StorageUnavailable):
        S3Store("no-existe", "org").list()


def test_sin_boto3_el_mensaje_indica_el_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(StorageUnavailable, match=r"ens-evidence\[s3\]"):
        S3Store(BUCKET, "org")
