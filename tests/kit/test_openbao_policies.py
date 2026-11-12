import re
from importlib import resources
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "src/ens_evidence/kit/openbao"
DEPLOY = ROOT / "deploy/openbao"
COPIES = [
    ("config.hcl", "config.hcl"),
    ("ens-app-metadata.hcl", "policies/ens-app-metadata.hcl"),
    ("claves-admin.hcl", "policies/claves-admin.hcl"),
]
SUDO_ALLOWED = {"sys/audit", "auth/token/accessors"}
APP_DENIES = [
    "kv/data/*", "kv/subkeys/*", "kv/destroy/*", "kv/delete/*", "transit/export/*",
    "transit/backup/*", "transit/decrypt/*", "transit/encrypt/*", "transit/sign/*",
    "sys/audit/*", "auth/token/revoke*", "auth/token/renew-accessor", "auth/token/tidy",
    "auth/token/create*", "sys/generate-root-token/*", "sys/rekey*", "sys/rotate*",
    "sys/storage/raft/*", "sys/raw*",
]  # fmt: skip
_BLOCK = re.compile(r'path\s+"([^"]+)"\s*\{\s*capabilities\s*=\s*\[([^\]]*)\]\s*\}')


def rules(name: str) -> dict[str, set[str]]:
    text = (KIT / name).read_text()
    found = {p: set(re.findall(r'"([^"]+)"', caps)) for p, caps in _BLOCK.findall(text)}
    assert text.count("path ") == len(found), "bloque path que el analizador no reconoce"
    return found


@pytest.mark.parametrize(("kit", "deploy"), COPIES)
def test_deploy_es_copia_identica_de_la_fuente(kit, deploy):
    assert (DEPLOY / deploy).read_bytes() == (KIT / kit).read_bytes()


def test_los_ficheros_estan_dentro_del_paquete_importable():
    base = resources.files("ens_evidence.kit") / "openbao"
    assert all((base / kit).is_file() for kit, _ in COPIES)


@pytest.mark.parametrize("name", ["ens-app-metadata.hcl", "claves-admin.hcl"])
def test_sudo_solo_en_audit_y_accessors_de_la_app(name):
    with_sudo = {p for p, caps in rules(name).items() if "sudo" in caps}
    assert with_sudo <= SUDO_ALLOWED
    assert (with_sudo == SUDO_ALLOWED) == (name == "ens-app-metadata.hcl")


def test_claves_admin_sin_sudo_y_con_deny_en_export_y_backup():
    found = rules("claves-admin.hcl")
    assert not any("sudo" in caps for caps in found.values())
    assert found["transit/export/*"] == found["transit/backup/*"] == {"deny"}


def test_la_politica_de_la_app_tiene_todos_los_deny_y_ningun_comodin_entre_montajes():
    found = rules("ens-app-metadata.hcl")
    for path in APP_DENIES:
        assert found.get(path) == {"deny"}, path
    assert not any("+/" in path or path.startswith(("+", "*")) for path in found)
    assert {p for p, caps in found.items() if "deny" in caps} == set(APP_DENIES)


def test_la_app_no_puede_escribir_en_kv_ni_transit():
    found = rules("ens-app-metadata.hcl")
    writes = {"create", "update", "delete", "patch"}
    for path, caps in found.items():
        if path.startswith(("kv/", "transit/")):
            assert not caps & writes, path


def test_config_declara_auditoria_y_no_trae_opciones_inseguras():
    text = (KIT / "config.hcl").read_text()
    assert 'audit "file"' in text
    for forbidden in ("unsafe_allow_api_audit_creation", "disable_mlock", "disable_unauthed"):
        assert forbidden not in text
