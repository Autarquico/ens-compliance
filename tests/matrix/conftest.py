import boto3
import pytest
from applicability.helpers import (
    LATER,
    decide,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)
from moto import mock_aws

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.models.enums import Applicability
from ens_evidence.workspace import LocalStore, S3Store

from .helpers import COMPENSATION, SIGNED_AT


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path, monkeypatch):
    if request.param == "local":
        yield LocalStore(tmp_path / "ws")
        return
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="evidencias")
        yield S3Store("evidencias", "org")


@pytest.fixture
def workspace(tmp_path):
    """Alcance aprobado, categoría MEDIA aprobada y `controls init` ejecutado."""
    return prepared_workspace(tmp_path / "ws", uniform("MEDIO"))


@pytest.fixture
def ready(workspace):
    """Aplicabilidad decidida: `op.pl.1` compensada, `op.cont.2` adicional."""
    propose_applicability(workspace, LATER)
    decide_all_undetermined(workspace)
    decide(workspace, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    decide(workspace, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")
    return workspace


@pytest.fixture
def declaration(ready):
    return approve_declaration(ready, "Luis Pardo", SIGNED_AT)


@pytest.fixture
def signed(declaration, ready):
    """El almacén con la Declaración de Aplicabilidad firmada y vigente."""
    return ready
