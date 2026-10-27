import threading

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from ens_evidence.workspace import (
    AlreadyExists,
    ConditionalWriteUnsupported,
    LocalStore,
    S3Store,
    StorageUnavailable,
)

BUCKET = "evidencias"
WRITERS = 16


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path, monkeypatch):
    if request.param == "local":
        yield LocalStore(tmp_path / "ws")
        return
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield S3Store(BUCKET, "org")


def test_create_bytes_crea_y_se_lee_de_vuelta(store):
    store.create_bytes("a/b.bin", b"\x00\xff datos")

    assert store.read_bytes("a/b.bin") == b"\x00\xff datos"


def test_segunda_creacion_falla_y_conserva_el_contenido_original(store):
    store.create_bytes("a.bin", b"primero")

    with pytest.raises(AlreadyExists):
        store.create_bytes("a.bin", b"segundo")

    assert store.read_bytes("a.bin") == b"primero"


def test_write_bytes_si_sobrescribe(store):
    store.create_bytes("a.bin", b"primero")
    store.write_bytes("a.bin", b"segundo")

    assert store.read_bytes("a.bin") == b"segundo"


def test_creacion_concurrente_con_hilos_solo_la_gana_uno(store):
    outcomes: list[object] = []
    barrier = threading.Barrier(WRITERS)

    def create(index: int) -> None:
        barrier.wait()
        try:
            store.create_bytes("carrera.bin", f"hilo-{index}".encode())
            outcomes.append(index)
        except AlreadyExists:
            outcomes.append("perdido")

    threads = [threading.Thread(target=create, args=(i,)) for i in range(WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [o for o in outcomes if o != "perdido"]
    assert len(outcomes) == WRITERS
    assert len(winners) == 1
    assert store.read_bytes("carrera.bin") == f"hilo-{winners[0]}".encode()


def test_la_creacion_local_no_deja_temporales(tmp_path):
    store = LocalStore(tmp_path / "ws")
    store.create_bytes("a.bin", b"x")
    with pytest.raises(AlreadyExists):
        store.create_bytes("a.bin", b"y")

    assert [p.name for p in (tmp_path / "ws").iterdir()] == ["a.bin"]


class FailingClient:
    def __init__(self, code=None, error=None):
        self._code, self._error = code, error

    def put_object(self, **kwargs):
        if self._error:
            raise self._error
        raise ClientError({"Error": {"Code": self._code}}, "PutObject")


def s3_with(client):
    return S3Store(BUCKET, "org", client=client)


def s3_with_failing(code):
    return s3_with(FailingClient(code))


@pytest.mark.parametrize("code", ["501", "NotImplemented", "InvalidArgument"])
def test_s3_sin_escrituras_condicionales_falla_explicitamente(code):
    with pytest.raises(ConditionalWriteUnsupported, match="If-None-Match"):
        s3_with_failing(code).create_bytes("a.bin", b"x")


def test_s3_con_botocore_antiguo_que_no_conoce_if_none_match_falla_explicitamente():
    class ParamValidationError(Exception):
        pass

    client = FailingClient(error=ParamValidationError("Unknown parameter IfNoneMatch"))

    with pytest.raises(ConditionalWriteUnsupported):
        s3_with(client).create_bytes("a.bin", b"x")


def test_s3_precondition_failed_es_ya_existe():
    with pytest.raises(AlreadyExists):
        s3_with_failing("412").create_bytes("a.bin", b"x")


def test_s3_conflicto_concurrente_409_no_se_trata_como_duplicado():
    with pytest.raises(StorageUnavailable):
        s3_with_failing("409").create_bytes("a.bin", b"x")
