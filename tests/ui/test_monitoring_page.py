"""`/monitorizacion`: grupos, estados, desde, runbook, histórico, sin datos y obsoleto."""

import re
from datetime import timedelta
from types import SimpleNamespace

import jinja2
import pytest

from ens_evidence.monitoring.model import Item, MailState, Point, Snapshot, Status
from ens_evidence.monitoring.snapshot import write_heartbeat, write_snapshot
from ens_evidence.ui.app import TEMPLATES_DIR
from ens_evidence.ui.monitoring_banner import banner_for
from ens_evidence.ui.pages import code_spans
from ens_evidence.workspace.cycle_lock import STATE_DIR_ENV

from .conftest import NOW, Session

HOSTILE = "<script>alert(1)</script>"
FORBIDDEN = re.compile(r"certific|garantiza el cumplimiento|cumple el ENS", re.IGNORECASE)
INTERVAL = 900


def item(
    id, group, title, status, *, detail="detalle", runbook="monitorizacion", since=None, series=()
):
    return Item(id, group, title, status, detail, runbook, since, NOW, None, tuple(series))


def series(*statuses):
    return [Point(NOW - timedelta(hours=6 * i), s, None) for i, s in enumerate(statuses)]


FAILING_SINCE = NOW - timedelta(hours=5)
ITEMS = (
    item(
        "tool:copias:up",
        "herramientas",
        "Copias responde",
        Status.FAIL,
        detail="no responde",
        runbook="kit-copias",
        since=FAILING_SINCE,
        series=series(Status.OK, Status.FAIL),
    ),
    item("vps:disk:/", "vps", "Disco", Status.OK, series=series(Status.OK, Status.OK)),
    item("vps:updates", "vps", "Actualizaciones", Status.UNKNOWN, series=series(Status.UNKNOWN)),
    item("caja:scheduler", "caja", "Programador", Status.WARN),
)


def publish(tmp_path, monkeypatch, *, items=ITEMS, mail=None, age=timedelta(0), beat_age=None):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    generated = NOW - age
    snap = Snapshot(generated, INTERVAL, tuple(items), mail or MailState(configured=False))
    write_snapshot(tmp_path, snap)
    write_heartbeat(tmp_path, NOW - (age if beat_age is None else beat_age), INTERVAL)


def page(session: Session) -> str:
    response = session.get("/monitorizacion")
    assert response.status_code == 200
    return response.text


def card(html: str, title: str) -> str:
    start = html.index(f"<h3>{title}</h3>")
    return html[start : html.index("</li>", start)]


def test_failing_item_shows_since_runbook_and_groups(session, tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch)

    html = page(session)

    for heading in ("Herramientas", "Servidor (VPS)", "La caja"):
        assert f">{heading}</h2>" in html
    copias = card(html, "Copias responde")
    assert "tone-bad" in copias and "Fallo" in copias
    assert "2026-11-08 07:00 UTC" in copias
    assert 'href="/ayuda/kit-copias"' in copias
    assert "<svg" in copias and "spark-bad" in copias
    assert not FORBIDDEN.search(html)
    assert "no cambia ninguna medida de la matriz" in html


def test_unknown_is_grey_never_green(session, tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch)

    unknown = card(page(session), "Actualizaciones")

    assert "tone-neutral" in unknown and "Desconocido" in unknown
    assert "tone-ok" not in unknown and "spark-ok" not in unknown
    assert "Desde" not in unknown


def test_stale_snapshot_treats_everything_as_unknown(session, tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch, age=timedelta(minutes=45))

    html = page(session)

    assert "Datos desactualizados" in html
    for title in ("Copias responde", "Disco", "Programador"):
        block = card(html, title)
        assert "tone-neutral" in block
        assert "tone-ok" not in block and "tone-bad" not in block and "Desde" not in block
        assert "spark-ok" not in block and "spark-bad" not in block


def test_stale_when_heartbeat_is_old_even_with_fresh_snapshot(session, tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch, beat_age=timedelta(minutes=45))

    assert "Datos desactualizados" in page(session)


def test_missing_snapshot_explains_how_to_start(session, tmp_path, monkeypatch):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "vacio"))

    html = page(session)

    assert "El monitor aún no ha corrido" in html
    assert 'href="/ayuda/monitorizacion"' in html
    assert "tone-ok" not in html


@pytest.mark.parametrize("configured", [True, False])
def test_mail_state(session, tmp_path, monkeypatch, configured):
    publish(tmp_path, monkeypatch, mail=MailState(configured=configured))

    html = page(session)

    assert ("Correo configurado" in html) is configured
    assert ("Correo sin configurar" in html) is not configured


def test_values_are_escaped(session, tmp_path, monkeypatch):
    hostile = item(
        "tool:copias:up",
        "herramientas",
        HOSTILE,
        Status.FAIL,
        detail=HOSTILE,
        runbook="x",
        since=NOW,
    )
    publish(tmp_path, monkeypatch, items=(hostile,), mail=MailState(True, NOW, False, HOSTILE))

    html = page(session)

    assert HOSTILE not in html
    assert "&lt;script&gt;" in html


def test_nav_links_to_the_page(session):
    assert 'href="/monitorizacion"' in session.get("/").text


def request_at(now):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(clock=lambda: now)))


def test_banner_lists_failing_items(tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch)

    banner = banner_for(request_at(NOW))

    assert banner == {"kind": "failing", "titles": ["Copias responde"]}


def test_banner_for_stale_and_none_when_healthy_or_missing(tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch, age=timedelta(hours=2))
    assert banner_for(request_at(NOW)) == {"kind": "stale", "titles": []}

    publish(tmp_path, monkeypatch, items=ITEMS[1:2])
    assert banner_for(request_at(NOW)) is None

    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "vacio"))
    assert banner_for(request_at(NOW)) is None


CHROME = {"product_name": "P", "identity_email": "a@b.c", "nav_active": "estado", "page_title": "T"}


def base_html(banner, **chrome):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.filters["code_spans"] = code_spans
    return env.get_template("error.html").render(
        **{**CHROME, **chrome}, status=500, message=None, monitoring_banner=banner
    )


def test_base_renders_the_banner_escaped():
    failing = base_html({"kind": "failing", "titles": [HOSTILE, "Disco"]})
    stale = base_html({"kind": "stale", "titles": []})

    assert "Falla 2 elementos" in failing and HOSTILE not in failing
    assert 'href="/monitorizacion"' in failing
    assert "desactualizados" in stale
    assert "monitoring-banner" not in base_html(None)


def test_banner_is_not_repeated_on_the_monitoring_page():
    banner = {"kind": "stale", "titles": []}

    assert "monitoring-banner" not in base_html(banner, nav_active="monitorizacion")
