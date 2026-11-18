import pytest

from ens_evidence.ui.roles import ROLES_KEY
from ens_evidence.ui.store import WRITABLE_PREFIXES, UiStore, WriteNotAllowed
from ens_evidence.workspace import InvalidKey


@pytest.fixture
def ui_store(store):
    return UiStore(store)


@pytest.mark.parametrize("prefix", WRITABLE_PREFIXES)
def test_allowed_prefixes_are_written(ui_store, store, prefix):
    ui_store.write_text(f"{prefix}/a.yaml", "x: 1\n")
    assert store.read_text(f"{prefix}/a.yaml") == "x: 1\n"


@pytest.mark.parametrize(
    "key",
    [
        "reports/r.md",
        "connectors",
        "connectors-evil/a.yaml",
        "settings.yaml",
        "scope",
        "scopes/a.yaml",
        "scope-evil/a.yaml",
        "evidence/checks/a.json",
        "evidence/preaudits/runs/a.yaml",
        "evidence/backups/a.json",
        "evidence/key-custodians-evil/a.yaml",
        "evidence/key-custodians",
        "evidence/rawx/a",
        "evidence/collect-requests",
        "evidence/collect-requests-evil/a.yaml",
        "evidence/collect-requests/../x",
        "scope/../connectors/a.yaml",
        "/scope/a.yaml",
        "",
    ],
)
def test_other_keys_are_rejected_for_every_write(ui_store, store, key):
    for write in (ui_store.write_text, ui_store.write_bytes, ui_store.create_bytes):
        with pytest.raises((WriteNotAllowed, InvalidKey)):
            write(key, "x" if write is ui_store.write_text else b"x")
    assert store.list("") == [ROLES_KEY]


def test_reads_cover_the_whole_workspace(ui_store, store):
    store.write_text("connectors/github.yaml", "a: 1\n")
    assert ui_store.read_text("connectors/github.yaml") == "a: 1\n"
    assert ui_store.exists("connectors/github.yaml")
    assert ui_store.list("connectors") == ["connectors/github.yaml"]


def test_a_normalized_connectors_key_is_the_allowed_key(ui_store, store):
    ui_store.write_text("./connectors/github.yaml", "a: 1\n")
    assert store.read_text("connectors/github.yaml") == "a: 1\n"
    assert ui_store.written == ["connectors/github.yaml"]


def test_collect_requests_prefix_is_writable():
    assert "evidence/collect-requests" in WRITABLE_PREFIXES


def test_el_lock_exclusivo_respeta_la_lista_blanca(ui_store, store):
    with ui_store.exclusive_lock("evidence/collect-requests/github.lock"):
        assert store.exists("evidence/collect-requests/github.lock")
    with pytest.raises(WriteNotAllowed), ui_store.exclusive_lock("evidence/checks/x.lock"):
        pytest.fail("no debe entrar")
    assert not store.exists("evidence/checks/x.lock")
