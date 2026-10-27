import io
import json
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
import yaml
from botocore.exceptions import ClientError
from moto import mock_aws

from ens_evidence.backup import verify_immutability
from ens_evidence.backup.immutability import PROTECTED_PREFIXES
from ens_evidence.backup.records import ImmutabilityCheck

from .conftest import BACKUP_KEY, BACKUP_SECRET, ens

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
BUCKET = "copias"
REPOSITORY = f"s3:https://s3.amazonaws.com/{BUCKET}/repo"
EXISTING = {
    "repo/config": b"config de restic",
    "repo/data/ab/abcdef": b"datos reales",
    "repo/index/i1": b"indice",
    "repo/snapshots/s1": b"snapshot",
    "repo/keys/k1": b"clave",
    "repo/locks/l1": b"bloqueo",
}


@pytest.fixture
def bucket(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        for key, body in EXISTING.items():
            client.put_object(Bucket=BUCKET, Key=key, Body=body)
        yield client


def snapshot_of(client) -> dict[str, tuple[bytes, str]]:
    listed = client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    return {
        o["Key"]: (client.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read(), o["ETag"])
        for o in listed
        if "ens-canary-" not in o["Key"]
    }


def test_destino_que_permite_borrar_informa_fallo_en_cada_prefijo(bucket):
    check = verify_immutability(bucket, REPOSITORY, BUCKET, "repo", datetime.now(UTC))

    assert not check.protected
    assert [r.prefix for r in check.results] == list(PROTECTED_PREFIXES)
    assert not any(r.protected for r in check.results)


def test_verify_immutability_solo_toca_claves_canario(bucket):
    before = snapshot_of(bucket)

    verify_immutability(bucket, REPOSITORY, BUCKET, "repo", datetime.now(UTC))

    assert snapshot_of(bucket) == before
    assert set(before) == set(EXISTING)


class ImmutableBucket:
    """Frontera de red: un destino con bloqueo que rechaza borrar y sobrescribir."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        if Key in self.objects:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):  # noqa: N803
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def test_destino_inmutable_se_informa_como_protegido():
    check = verify_immutability(ImmutableBucket(), REPOSITORY, BUCKET, "repo", datetime.now(UTC))

    assert check.protected


def client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, operation)


class ScriptedBucket:
    """Frontera de red: cada prefijo protegido se comporta según un guion.

    protected: rechaza borrar y sobrescribir. deleted: el borrado surte efecto.
    overwritten: la sobrescritura surte efecto. read_fails_after_delete / _after_overwrite:
    la lectura falla con un error que no es NoSuchKey/404.
    """

    def __init__(self, behaviour: dict[str, str]) -> None:
        self.behaviour = behaviour
        self.objects: dict[str, bytes] = {}
        self.reads: dict[str, int] = {}

    def _mode(self, key: str) -> str:
        return next(mode for prefix, mode in self.behaviour.items() if f"/{prefix}/" in f"/{key}")

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        if Key not in self.objects:
            self.objects[Key] = Body
        elif self._mode(Key) == "overwritten":
            self.objects[Key] = Body
        else:
            raise client_error("AccessDenied", "PutObject")

    def delete_object(self, Bucket, Key):  # noqa: N803
        if self._mode(Key) == "deleted":
            del self.objects[Key]
        else:
            raise client_error("AccessDenied", "DeleteObject")

    def get_object(self, Bucket, Key):  # noqa: N803
        self.reads[Key] = self.reads.get(Key, 0) + 1
        mode = self._mode(Key)
        if mode == "read_fails_after_delete" or (
            mode == "read_fails_after_overwrite" and self.reads[Key] == 2
        ):
            raise client_error("InternalError", "GetObject")
        if Key not in self.objects:
            raise client_error("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def uniform(mode: str) -> dict[str, str]:
    return dict.fromkeys(PROTECTED_PREFIXES, mode)


def check_with(behaviour: dict[str, str]) -> ImmutabilityCheck:
    return verify_immutability(
        ScriptedBucket(behaviour), REPOSITORY, BUCKET, "repo", datetime.now(UTC)
    )


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("read_fails_after_delete", id="tras-borrar"),
        pytest.param("read_fails_after_overwrite", id="tras-sobrescribir"),
    ],
)
def test_una_lectura_con_error_generico_es_no_concluyente_nunca_protegida(mode):
    check = check_with(uniform(mode))

    assert check.status == "inconclusive"
    assert not check.protected
    assert {r.status for r in check.results} == {"inconclusive"}


def test_canario_que_no_existe_tras_borrar_es_no_protegido():
    check = check_with(uniform("deleted"))

    assert check.status == "unprotected"
    assert {r.status for r in check.results} == {"unprotected"}


def test_canario_con_contenido_cambiado_es_no_protegido():
    check = check_with(uniform("overwritten"))

    assert check.status == "unprotected"
    assert {r.status for r in check.results} == {"unprotected"}


@pytest.mark.parametrize(
    "modes, expected",
    [
        pytest.param(["protected"] * 4, "protected", id="todo-protegido"),
        pytest.param(
            ["protected", "read_fails_after_delete", "protected", "protected"],
            "inconclusive",
            id="inconcluyente-gana-a-protegido",
        ),
        pytest.param(
            ["protected", "deleted", "protected", "protected"],
            "unprotected",
            id="no-protegido-gana-a-protegido",
        ),
        pytest.param(
            ["read_fails_after_delete", "deleted", "protected", "overwritten"],
            "unprotected",
            id="no-protegido-gana-a-inconcluyente",
        ),
    ],
)
def test_el_estado_agregado_prioriza_no_protegido_sobre_no_concluyente_sobre_protegido(
    modes, expected
):
    check = check_with(dict(zip(PROTECTED_PREFIXES, modes, strict=True)))

    assert check.status == expected
    assert check.protected is (expected == "protected")


@pytest.fixture
def s3_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}},
                "backup": {"restic_repository": REPOSITORY},
            }
        )
    )
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", "pw")
    monkeypatch.setenv("ENS_BACKUP_AWS_ACCESS_KEY_ID", BACKUP_KEY)
    monkeypatch.setenv("ENS_BACKUP_AWS_SECRET_ACCESS_KEY", BACKUP_SECRET)
    return path


def recorded_checks(tmp_path) -> list:
    return list((tmp_path / "ws" / "evidence" / "backups" / "immutability").glob("*.yaml"))


def test_cli_informa_fallo_registra_la_prueba_y_sale_con_1(bucket, tmp_path, s3_settings):
    before = snapshot_of(bucket)

    result = ens(s3_settings, "backup", "verify-immutability")

    assert result.exit_code == 1
    assert "NO protegido" in result.output
    assert BACKUP_SECRET not in result.output
    assert len(recorded_checks(tmp_path)) == 1
    assert snapshot_of(bucket) == before


@pytest.mark.parametrize(
    "mode, exit_code, text",
    [
        pytest.param("protected", 0, "protegido", id="protegido"),
        pytest.param("deleted", 1, "NO protegido", id="no-protegido"),
        pytest.param("read_fails_after_delete", 1, "NO CONCLUYENTE", id="no-concluyente"),
    ],
)
def test_cli_sale_con_0_solo_si_el_destino_esta_protegido(
    tmp_path, s3_settings, monkeypatch, mode, exit_code, text
):
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ScriptedBucket(uniform(mode)))

    result = ens(s3_settings, "backup", "verify-immutability")

    assert result.exit_code == exit_code, result.output
    assert text in result.output
    assert len(recorded_checks(tmp_path)) == 1


@pytest.mark.parametrize("mode", ["protected", "deleted", "read_fails_after_delete"])
def test_el_registro_escrito_valida_contra_el_esquema_publicado(
    tmp_path, s3_settings, monkeypatch, mode
):
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ScriptedBucket(uniform(mode)))
    ens(s3_settings, "backup", "verify-immutability")
    (path,) = recorded_checks(tmp_path)
    record = yaml.safe_load(path.read_text())
    schema = json.loads((SCHEMAS / "immutability-check.schema.json").read_text())
    result_schema = schema["$defs"]["CanaryResult"]

    ImmutabilityCheck.model_validate(record)
    assert set(schema["required"]) <= set(record) <= set(schema["properties"])
    for result in record["results"]:
        assert set(result_schema["required"]) <= set(result) <= set(result_schema["properties"])
        assert result["status"] in result_schema["properties"]["status"]["enum"]
