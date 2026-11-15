"""El conector: qué lee, qué descarta y cómo falla."""

import json
import os
from datetime import timedelta

import pytest

from ens_evidence.connectors.passwords import PasswordsConfig, PasswordsConnector, reader
from ens_evidence.models.enums import CollectionStatus

from .conftest import NOW, export_doc, iso, user


def test_recoge_usuarios_y_resumen(collect):
    u = user()
    result = collect(export_doc([u]))

    assert result.collection_status is CollectionStatus.SUCCESS
    assert result.asset_id == "vaultwarden" and result.source == "passwords"
    assert result.data["thresholds"] == {"inactive_days": 90, "max_export_age_minutes": 60}
    assert result.data["users"] == [{k: u[k] for k in u}]
    assert result.data["export"]["age_minutes"] == 5
    assert "vaultwarden_migration" not in result.model_dump_json()


def test_la_configuracion_exige_umbrales_positivos_y_no_admite_extras():
    base = {"asset_id": "vw", "inactive_days": 90, "max_export_age_minutes": 60}
    assert PasswordsConfig(**base).export_path == "/run/kit/contrasenas/usuarios.json"
    for bad in (
        {"inactive_days": 0},
        {"max_export_age_minutes": 0},
        {"inactive_days": -1},
        {"token": "x"},
    ):
        with pytest.raises(ValueError):
            PasswordsConfig(**{**base, **bad})


def test_export_ausente(collect, tmp_path):
    result = collect(export_path=str(tmp_path / "no-existe.json"))

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"] == reader.MISSING and "users" not in result.data


@pytest.mark.parametrize(
    ("age_minutes", "status"),
    [(59, CollectionStatus.SUCCESS), (60, CollectionStatus.SUCCESS), (61, CollectionStatus.ERROR)],
)
def test_export_viejo(collect, age_minutes, status):
    doc = export_doc(
        exported_at=iso(NOW - timedelta(minutes=age_minutes)),
        last_good_snapshot_at=iso(NOW - timedelta(minutes=age_minutes)),
    )
    result = collect(doc)

    assert result.collection_status is status
    if status is CollectionStatus.ERROR:
        assert result.data["error"] == reader.STALE


def test_export_con_fecha_futura(collect):
    future = iso(NOW + timedelta(minutes=1))
    result = collect(export_doc(exported_at=future, last_good_snapshot_at=future))

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"] == reader.FUTURE


def test_usuario_con_fecha_posterior_al_export_se_rechaza(collect):
    result = collect(export_doc([user(created_at=iso(NOW))]))

    assert result.data["error"] == reader.INVALID


@pytest.mark.parametrize(
    "raw",
    [b"", b"no es json", b"[]", b'{"format": 2}', b"\xff\xfe", b"[" * 100000],
)
def test_ilegible_o_invalido(collect, raw):
    result = collect(raw=raw)

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"] == reader.INVALID


def test_formato_desconocido_y_booleanos_estrictos(collect):
    assert collect(export_doc(format=2)).data["error"] == reader.INVALID
    assert collect(export_doc([user(second_factor="true")])).data["error"] == reader.INVALID
    dup = user()
    assert collect(export_doc([dup, dup])).data["error"] == reader.INVALID


def test_symlink_se_rechaza(collect, export_file, tmp_path):
    target = tmp_path / "real.json"
    target.write_text(json.dumps(export_doc()))
    link = tmp_path / "enlace.json"
    os.symlink(target, link)
    result = collect(export_path=str(link))

    assert result.collection_status is CollectionStatus.ERROR
    assert result.data["error"] == reader.UNREADABLE


def test_no_regular_se_rechaza(collect, tmp_path):
    result = collect(export_path=str(tmp_path))

    assert result.data["error"] in {reader.NOT_REGULAR, reader.UNREADABLE}
    assert result.collection_status is CollectionStatus.ERROR


def test_fifo_no_bloquea_y_se_rechaza(collect, tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    assert collect(export_path=str(fifo)).data["error"] == reader.NOT_REGULAR


def test_demasiado_grande(collect, monkeypatch):
    monkeypatch.setattr(reader, "MAX_EXPORT_BYTES", 100)
    result = collect(export_doc())

    assert result.data["error"] == reader.TOO_LARGE


def test_prepare_exige_un_activo_application(monkeypatch):
    from types import SimpleNamespace

    from ens_evidence.connectors.passwords import connector as module
    from ens_evidence.scope.models import AssetType
    from ens_evidence.workspace import WorkspaceError

    assets = SimpleNamespace(
        assets=[
            SimpleNamespace(id="vaultwarden", type=AssetType.APPLICATION),
            SimpleNamespace(id="disco", type=AssetType.BACKUP),
        ]
    )
    monkeypatch.setattr(module, "load_assets", lambda store: (assets, []))
    connector = PasswordsConnector(clock=lambda: NOW)
    base = {"inactive_days": 90, "max_export_age_minutes": 60}

    config = PasswordsConfig(asset_id="vaultwarden", **base)
    assert connector.prepare(config, object()) is config
    with pytest.raises(WorkspaceError, match="application"):
        connector.prepare(PasswordsConfig(asset_id="disco", **base), object())
