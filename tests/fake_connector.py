"""Conector ficticio usado por los tests de descubrimiento."""

from datetime import UTC, datetime

from pydantic import BaseModel

from ens_evidence.connectors import Connector
from ens_evidence.models import CollectionResult, CollectionStatus


class FakeConfig(BaseModel):
    asset_id: str = "asset-1"


class FakeConnector(Connector):
    name = "fake"
    config_model = FakeConfig

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, FakeConfig)
        return CollectionResult(
            source="fake-source",
            collector=self.name,
            collected_at=datetime(2026, 1, 1, tzinfo=UTC),
            asset_id=config.asset_id,
            data={"mfa_enabled": True},
            collection_status=CollectionStatus.SUCCESS,
        )


class MisnamedConnector(FakeConnector):
    name = "other-name"
