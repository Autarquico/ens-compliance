"""`ens-evidence agent hosts` (listado) y `agent hosts show <activo>`: solo leen."""
# ruff: noqa: F811

from .protocol_support import LAPTOP, OS_VERSION_ROW, SERVER, snapshot_event
from .test_cli_agent import ens, held_by_another, settings  # noqa: F401 -- fixture compartida

ASSETS = """\
assets:
  - id: lap-ana
    type: laptop
    deployment: deployed
    hostname: lap-ana.taller-norte.example
    os: macOS 15
    owner: Ana Ruiz
    encrypted: true
    usage: Desarrollo
"""


def deliver_os_version(world, node_key: str) -> None:
    event = snapshot_event("os_version", [OS_VERSION_ROW], unix=world.clock())
    body = {"node_key": node_key, "log_type": "result", "data": [event]}
    assert world.post("log", body).status_code == 200


def test_bare_hosts_still_lists_every_enrolled_host(settings, world):
    result = ens(settings, "hosts")
    assert result.exit_code == 0, result.output
    assert f"{LAPTOP}\túltimo contacto: nunca\tsin alta" in result.stdout
    assert f"{SERVER}\t" in result.stdout


def test_show_without_data_says_sin_datos_and_lists_host_results(settings, world):
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    result = ens(settings, "hosts", "show", LAPTOP)
    assert result.exit_code == 0, result.output
    assert "sin alta" in result.stdout
    assert "Último contacto (evidencia): sin datos" in result.stdout
    assert "(estado del servicio, no es evidencia): nunca" in result.stdout
    assert "os_version\tsin datos" in result.stdout
    assert "HOST-002\tUNKNOWN" in result.stdout


def test_show_reports_admission_contact_snapshot_and_checks_with_evidence_ids(settings, world):
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    node_key = world.enroll()
    world.clock.advance(minutes=5)
    deliver_os_version(world, node_key)

    result = ens(settings, "hosts", "show", LAPTOP)

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "admitido (generación 1)" in out
    assert f"Último contacto (evidencia): {world.clock().isoformat()}" in out
    assert "evidencia: " in out and "sin datos" not in out.split("Consultas")[0]
    assert f"os_version\trecogida {world.clock().isoformat()}\tfilas: 1\tevidencia: " in out
    assert "estado del servicio, no es evidencia): " in out
    assert "nunca" not in out.split("(estado del servicio")[1].splitlines()[0]
    assert "HOST-002\tPASS" in out


def test_show_ignores_contact_older_than_the_silence_threshold(settings, world):
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    world.enroll()
    world.clock.advance(hours=25)
    result = ens(settings, "hosts", "show", LAPTOP)
    assert "HOST-002\tFAIL" in result.stdout


def test_show_unknown_or_not_enrolled_asset_exits_2_and_writes_nothing(settings, world):
    before = world.files()
    for asset in ("fantasma", "lap-sin-inscribir"):
        result = ens(settings, "hosts", "show", asset)
        assert result.exit_code == 2
        assert "no está inscrito" in result.output
    assert world.files() == before


def test_show_is_read_only(settings, world):
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    world.enroll()
    before = world.files()
    assert ens(settings, "hosts", "show", LAPTOP).exit_code == 0
    assert world.files() == before


def test_show_does_not_wait_for_the_cycle_lock(settings, world):
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    with held_by_another(settings):
        assert ens(settings, "hosts", "show", LAPTOP).exit_code == 0
