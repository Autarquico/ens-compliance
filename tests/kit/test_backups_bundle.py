import gzip
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from ens_evidence.kit.backups import (
    BackupInputError,
    BackupInputs,
    BundleSecrets,
    generate_repo_password,
    normalize_host_path,
    render_bundle,
    render_host_bundle,
    validation_messages,
)

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
SECRETS = BundleSecrets(
    repo_password=SecretStr("repo-pass-0123456789"),
    write_access_key_id=SecretStr("WRITEKEYID0001"),
    write_secret_access_key=SecretStr("write-secret-0001"),
    read_access_key_id=SecretStr("READKEYID0002"),
    read_secret_access_key=SecretStr("read-secret-0002"),
)


def inputs(**over):
    base = {
        "id": "caja",
        "asset_id": "vps-caja",
        "destination": {
            "endpoint": "https://s3.example.com",
            "bucket": "copias-pyme",
            "prefix": "caja",
            "lock_days": 30,
        },
        "schedule": {
            "cron": "0 2 * * *",
            "retention_days": 90,
            "max_backup_age_hours": 26,
            "max_check_age_days": 35,
            "restore_test_validity_days": 365,
        },
        "volumes": ["data"],
        "host_paths": ["/srv/datos"],
    }
    return BackupInputs.model_validate({**base, **over})


def unpack(blob):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: (m, tar.extractfile(m).read()) for m in tar.getmembers()}


def test_el_paquete_es_determinista():
    assert render_bundle(inputs(), SECRETS, NOW) == render_bundle(inputs(), SECRETS, NOW)


def test_el_paquete_tiene_exactamente_los_ficheros_del_contrato_en_orden():
    members = unpack(render_bundle(inputs(), SECRETS, NOW))
    assert list(members) == [
        "copias/config.json",
        "copias/compose.yaml",
        "copias/app.env",
        "copias/backups.yaml",
        "copias/MANIFEST",
    ]
    for member, _ in members.values():
        assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
        assert member.mtime == int(NOW.timestamp())


def test_la_cabecera_gzip_no_lleva_nombre_ni_hora_de_reloj():
    blob = render_bundle(inputs(), SECRETS, NOW)
    assert gzip.decompress(blob)
    assert int.from_bytes(blob[4:8], "little") == int(NOW.timestamp())
    assert blob[3] == 0  # sin FNAME


def test_el_manifest_coincide_con_los_ficheros():
    members = unpack(render_bundle(inputs(), SECRETS, NOW))
    lines = members["copias/MANIFEST"][1].decode().splitlines()
    assert lines[0] == "version: 1"
    listed = {n: h for h, n in (line.split("  ") for line in lines[1:])}
    others = {n: b for n, (_, b) in members.items() if n != "copias/MANIFEST"}
    assert listed == {n: hashlib.sha256(b).hexdigest() for n, b in others.items()}


def test_config_json_tiene_el_formato_de_backrest_sin_usuarios():
    config = json.loads(unpack(render_bundle(inputs(), SECRETS, NOW))["copias/config.json"][1])
    assert "auth" not in config
    repo = config["repos"][0]
    assert repo["uri"] == "s3:https://s3.example.com/copias-pyme/caja"
    assert repo["password"] == "repo-pass-0123456789"
    assert repo["env"] == [
        "AWS_ACCESS_KEY_ID=WRITEKEYID0001",
        "AWS_SECRET_ACCESS_KEY=write-secret-0001",
    ]
    assert {"prunePolicy", "checkPolicy"} <= set(repo)
    plan = config["plans"][0]
    assert plan["paths"] == ["/source/caja", "/source/host/srv/datos"]
    assert plan["schedule"] == {"cron": "0 2 * * *"}
    assert plan["retention"] == {"policyTimeBucketed": {"daily": 90}}


def test_el_override_sin_rutas_del_host_no_monta_nada_nuevo():
    override = yaml.safe_load(
        unpack(render_bundle(inputs(host_paths=[]), SECRETS, NOW))["copias/compose.yaml"][1]
    )
    assert override == {"services": {"backrest": {"volumes": []}}}


def test_el_override_monta_en_solo_lectura_y_solo_en_backrest():
    override = yaml.safe_load(
        unpack(render_bundle(inputs(), SECRETS, NOW))["copias/compose.yaml"][1]
    )
    assert list(override) == ["services"]
    assert list(override["services"]) == ["backrest"]
    assert override["services"]["backrest"]["volumes"] == [
        "/srv/datos:/source/host/srv/datos:ro",
    ]


def test_app_env_solo_lleva_las_credenciales_de_lectura():
    env = unpack(render_bundle(inputs(), SECRETS, NOW))["copias/app.env"][1].decode()
    assert env.splitlines() == [
        "ENS_BACKUPS_CAJA_RESTIC_PASSWORD=repo-pass-0123456789",
        "ENS_BACKUPS_CAJA_AWS_ACCESS_KEY_ID=READKEYID0002",
        "ENS_BACKUPS_CAJA_AWS_SECRET_ACCESS_KEY=read-secret-0002",
    ]
    assert "WRITE" not in env.upper().replace("WRITE_", "")


def test_el_fragmento_de_backups_no_lleva_secretos_y_tiene_la_forma_esperada():
    members = unpack(render_bundle(inputs(), SECRETS, NOW))
    text = members["copias/backups.yaml"][1].decode()
    for value in (
        "repo-pass-0123456789",
        "WRITEKEYID0001",
        "write-secret-0001",
        "READKEYID0002",
        "read-secret-0002",
    ):
        assert value not in text
    assert yaml.safe_load(text) == {
        "repositories": [
            {
                "id": "caja",
                "asset_id": "vps-caja",
                "repository": "s3:https://s3.example.com/copias-pyme/caja",
                "max_backup_age_hours": 26,
                "max_check_age_days": 35,
                "restore_test_validity_days": 365,
                "min_check_read_data_percent": 5,
            }
        ]
    }


def test_el_porcentaje_minimo_de_lectura_es_explicito_y_acotado():
    schedule = inputs().schedule.model_dump()
    chosen = inputs(schedule={**schedule, "min_check_read_data_percent": 0}).schedule
    assert chosen.min_check_read_data_percent == 0
    for bad in (-1, 101, 2.5):
        with pytest.raises(ValidationError):
            inputs(schedule={**schedule, "min_check_read_data_percent": bad})


@pytest.mark.parametrize("retention", [30, 10, 90])
def test_la_retencion_nunca_se_rechaza_por_el_bloqueo(retention):
    schedule = {**inputs().schedule.model_dump(), "retention_days": retention}

    assert inputs(schedule=schedule).schedule.retention_days == retention


@pytest.mark.parametrize("identifier", ["1-x", "Caja", "a", "x" * 32, "con-guion"])
def test_un_identificador_de_repositorio_no_valido_se_rechaza(identifier):
    with pytest.raises(ValidationError):
        inputs(id=identifier)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/proc",
        "/proc/1",
        "/sys/kernel",
        "/dev",
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/run",
        "/var/run",
        "/srv/../etc",
        "relativa/ruta",
        "/srv/a:b",
        "/srv/a\nb",
    ],
)
def test_las_rutas_del_host_prohibidas_se_rechazan(path):
    with pytest.raises(ValueError):
        normalize_host_path(path)
    with pytest.raises(ValidationError):
        inputs(host_paths=[path])


def test_un_volumen_desconocido_y_una_seleccion_vacia_se_rechazan():
    with pytest.raises(ValidationError):
        inputs(volumes=["backrest-config"])
    with pytest.raises(ValidationError):
        inputs(volumes=[], host_paths=[])


def test_la_instantanea_de_contrasenas_entra_en_el_plan_sin_montaje_nuevo():
    blob = render_bundle(inputs(volumes=["data", "contrasenas"], host_paths=[]), SECRETS, NOW)
    plan = json.loads(unpack(blob)["copias/config.json"][1])["plans"][0]
    assert plan["paths"] == ["/source/caja", "/source/contrasenas"]


def test_las_rutas_del_host_se_normalizan_y_se_deduplican():
    assert inputs(host_paths=["/srv/datos/", "/srv//datos"]).host_paths == ("/srv/datos",)


@pytest.mark.parametrize(
    "destination",
    [
        {"endpoint": "http://s3.example.com"},
        {"endpoint": "https://user:pw@s3.example.com"},
        {"endpoint": "https://s3.example.com/bucket"},
        {"bucket": "A"},
        {"prefix": "../otro"},
    ],
)
def test_un_destino_no_valido_se_rechaza(destination):
    base = inputs().destination.model_dump()
    with pytest.raises(ValidationError):
        inputs(destination={**base, **destination})


def test_las_credenciales_de_escritura_y_lectura_deben_ser_distintas():
    data = SECRETS.model_dump()
    data["read_access_key_id"] = SECRETS.write_access_key_id
    data["read_secret_access_key"] = SECRETS.write_secret_access_key
    with pytest.raises(ValidationError) as exc:
        BundleSecrets.model_validate(data)
    assert "distintas" in validation_messages(exc.value)[0]


def test_los_secretos_con_caracteres_inseguros_se_rechazan_sin_repetirse():
    data = {**SECRETS.model_dump(), "repo_password": SecretStr("mal valor\nX=1")}
    with pytest.raises(ValidationError) as exc:
        BundleSecrets.model_validate(data)
    assert "mal valor" not in str(exc.value)
    assert "repo-pass" not in repr(SECRETS)


def test_la_contrasena_generada_es_valida_y_aleatoria():
    first, second = generate_repo_password(), generate_repo_password()
    assert first != second
    BundleSecrets.model_validate({**SECRETS.model_dump(), "repo_password": first})


def test_una_fecha_sin_zona_horaria_se_rechaza():
    with pytest.raises(BackupInputError):
        render_bundle(inputs(), SECRETS, datetime(2026, 11, 9))


def test_el_paquete_de_otro_servidor_tiene_repositorio_propio_y_no_lleva_compose():
    host = inputs(
        id="servidor2",
        asset_id="srv-02",
        volumes=[],
        destination={**inputs().destination.model_dump(), "prefix": "servidor2"},
    )
    blob = render_host_bundle(host, SECRETS, NOW)
    assert blob == render_host_bundle(host, SECRETS, NOW)
    members = unpack(blob)
    assert list(members) == [
        "copias/config.json",
        "copias/app.env",
        "copias/backups.yaml",
        "copias/INSTRUCCIONES.md",
        "copias/MANIFEST",
    ]
    config = json.loads(members["copias/config.json"][1])
    assert config["plans"][0]["paths"] == ["/srv/datos"]
    assert config["repos"][0]["uri"].endswith("/copias-pyme/servidor2")
    assert "ENS_BACKUPS_SERVIDOR2_RESTIC_PASSWORD" in members["copias/app.env"][1].decode()
    instructions = members["copias/INSTRUCCIONES.md"][1].decode()
    for value in ("repo-pass-0123456789", "write-secret-0001", "WRITEKEYID0001"):
        assert value not in instructions


def test_el_paquete_de_otro_servidor_rechaza_los_volumenes_de_la_caja():
    with pytest.raises(BackupInputError):
        render_host_bundle(inputs(), SECRETS, NOW)
