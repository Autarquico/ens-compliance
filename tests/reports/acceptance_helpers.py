"""Mundo de aceptación de los informes: el ciclo completo por la CLI, con conectores falsos.

Sobre el mundo `ready` del motor de checks (workspace real, GitHub recogido y en PASS). Se añaden
las configuraciones de los otros tres conectores, un `schedule.yaml` sin reintentos (el reloj
inyectado no duerme) y los conectores falsos de `acceptance_fakes` (se ejecutan en subprocesos
`spawn`, como en producción). El reloj de la CLI avanza un segundo por lectura.
"""

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import yaml
from checks.acceptance_helpers import World, make_world
from checks.test_engine import write_config
from matrix.acceptance_helpers import settings_file
from matrix.evaluate_helpers import NOW

from ens_evidence.connectors.registry import ConnectorRegistry
from ens_evidence.reports.model import DailyReport
from ens_evidence.reports.store import load_json_strict, pairs
from ens_evidence.scheduling.collect import Plugins
from ens_evidence.scheduling.config import SCHEDULE_KEY

from . import acceptance_fakes as fakes
from .helpers import source_sha256

START = NOW + timedelta(hours=1)
TOKEN = "ghp_" + "T0k3n" * 8
LEAK = "AKIA" + "ABCDEFGHIJKLMNOP"
PERIOD = "2026-10-30"  # fecha local en Madrid de START
EVERYTHING_OK = {"github": "ok", "cloudflare": "ok", "google-workspace": "ok"}
CONFIGS = {
    "cloudflare": {"asset_id": "cf-taller", "account_id": "0123456789abcdef" * 2},
    "google-workspace": {
        "asset_id": "ws-identidad",
        "admin_subject": "admin@taller.example",
        "domains": ["taller.example"],
    },
}
METADATA = {
    "schema_version",
    "generated_at",
    "product_version",
    "notices",
    "kind",
    "period",
    "cycle",
}


class Tick:
    """Reloj de la CLI: cada lectura avanza un segundo (las claves de los informes lo exigen)."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        instant = self.current
        self.current += timedelta(seconds=1)
        return instant


def configure(world: World, connectors) -> None:
    for name in connectors:
        write_config(world.store, name, CONFIGS[name])
    world.store.write_text(SCHEDULE_KEY, yaml.safe_dump({"collect": {"retries": 0}}))


def fake_plugins() -> Plugins:
    registry = ConnectorRegistry()
    for fake in fakes.FAKES:
        registry.register(fake)
    return Plugins(registry)


def cycle_world(
    tmp_path, monkeypatch, modes: dict[str, str], *, extra=("cloudflare", "google-workspace"),
    kind: str = "ready", token: str = TOKEN, leak: str = LEAK,
) -> World:  # fmt: skip
    """El mundo con `modes` por conector listo para `report daily --cycle` a las `START`."""
    world = make_world(tmp_path, monkeypatch, kind)
    configure(world, extra)
    monkeypatch.setenv(fakes.MODES_ENV, json.dumps(modes))
    monkeypatch.setenv(fakes.TOKEN_ENV, token)
    monkeypatch.setenv(fakes.LEAK_ENV, leak)
    monkeypatch.setenv(fakes.COLLECTED_AT_ENV, (START - timedelta(minutes=1)).isoformat())
    monkeypatch.setattr("ens_evidence.cli.load_plugins", fake_plugins)
    world.tick = Tick(START)
    monkeypatch.setattr("ens_evidence.cli.clock", world.tick)
    return world


def daily_keys(store, period: str = PERIOD):
    """El último par publicado del periodo (el canónico)."""
    published = [p for p in pairs(store, "daily", period) if p.published]
    assert published, "no hay informe diario publicado"
    return published[-1]


def daily_json(store, period: str = PERIOD) -> dict:
    return load_json_strict(store.read_bytes(daily_keys(store, period).json_key))


def daily_report(store, period: str = PERIOD) -> DailyReport:
    return DailyReport.model_validate(daily_json(store, period))


def daily_html(store, period: str = PERIOD) -> str:
    return store.read_text(daily_keys(store, period).html_key)


def without_sources(node):
    """El documento sin las listas `sources`: lo que el informe afirma, no lo que cita."""
    if isinstance(node, dict):
        return {k: without_sources(v) for k, v in node.items() if k != "sources"}
    if isinstance(node, list):
        return [without_sources(v) for v in node]
    return node


def connector(document: dict, name: str) -> dict:
    return next(c for c in document["connectors"] if c["connector"] == name)


def counts(section: dict) -> dict[str, int]:
    assert "value" in section, section
    return section["value"]["counts"]


# --- recorrido genérico del JSON ----------------------------------------------------------------

_INSTANT_KEY = re.compile(r"(^at$|_at$|^data_cutoff$)")


def walk(node, path: str = ""):
    """Cada (ruta, valor) del documento, hojas incluidas."""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


def is_unavailable(node) -> bool:
    return isinstance(node, dict) and set(node) == {"reason"}


def is_sourced(node) -> bool:
    return isinstance(node, dict) and set(node) == {"value", "sources"}


def sections_under(node) -> list[tuple[str, dict]]:
    return [(p, n) for p, n in walk(node) if is_unavailable(n) or is_sourced(n)]


def source_refs(document: dict) -> list[tuple[str, dict]]:
    return [
        (path, node)
        for path, node in walk(document)
        if isinstance(node, dict) and set(node) == {"kind", "key", "sha256", "at"}
    ]


def instants(document: dict) -> list[tuple[str, str]]:
    return [
        (path, value)
        for path, value in walk(document)
        if isinstance(value, str) and _INSTANT_KEY.search(path.rsplit(".", 1)[-1].split("[")[0])
    ]


def is_aware(text: str) -> bool:
    return datetime.fromisoformat(text).tzinfo is not None


def source_matches_store(store, ref: dict) -> bool:
    key = ref["key"]
    if key.endswith("/"):
        return bool(store.list(key.rstrip("/"))) and source_sha256(store, key) == ref["sha256"]
    return store.exists(key) and source_sha256(store, key) == ref["sha256"]


# --- HTML ---------------------------------------------------------------------------------------


class Rows(HTMLParser):
    """Las filas `<tr>` del cuerpo de las tablas, con el título `<h2>` de su sección."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.titled: list[tuple[str, list[str]]] = []
        self.text: list[str] = []
        self._title = ""
        self._in_title = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._body = False

    def handle_starttag(self, tag, attrs):
        self._body = {"tbody": True, "thead": False}.get(tag, self._body)
        if tag == "h2":
            self._in_title, self._title = True, ""
        if tag == "tr" and self._body:
            self._row = []
        if tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_title = False
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self.titled.append((self._title, self._row))
            self._row = None

    def handle_data(self, data):
        self.text.append(data)
        if self._in_title:
            self._title += data
        if self._cell is not None:
            self._cell.append(data)


def html_rows(document: str) -> Rows:
    parser = Rows()
    parser.feed(document)
    parser.close()
    return parser


def instant_text(text: str) -> str:
    return datetime.fromisoformat(text).astimezone(UTC).isoformat()


def stdout_json(world: World, *args: str) -> dict:
    result = world.run("report", "daily", "--stdout", *args)
    assert result.exit_code in (0, 3), result.output
    return json.loads(result.output)


# --- instrumentos de las invariantes ------------------------------------------------------------

LATE = START + timedelta(days=1)  # tras todo lo escrito por un ciclo a las START


def read_only(monkeypatch) -> None:
    """Para leer un mundo ya construido: el reloj de la CLI queda tras todo lo escrito."""
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: LATE)


def copy_of(world: World, destination) -> World:
    """Una copia del workspace con su propio settings (mismo reloj de la CLI)."""
    original = world.settings.parent
    shutil.copytree(original, destination)
    settings = settings_file(
        destination, {"backend": "local", "local": {"path": str(destination / "ws")}}
    )
    return World(destination / "ws", settings, world.clock)


class AppendOnlyStore:
    """Envoltorio que falla ante una reescritura de una clave existente bajo `evidence/`.

    Anota cada violación además de lanzar: un `except Exception` del código bajo prueba no puede
    taparla.
    """

    PROTECTED = "evidence/"

    def __init__(self, inner) -> None:
        self.inner = inner
        self.violations: list[str] = []
        self.new_keys: list[str] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _write(self, method: str, key: str, data) -> None:
        if key.startswith(self.PROTECTED):
            if self.inner.exists(key):
                self.violations.append(f"{method} {key}")
                raise AssertionError(f"reescritura de {key}")
            self.new_keys.append(key)
        getattr(self.inner, method)(key, data)

    def write_text(self, key: str, text: str) -> None:
        self._write("write_text", key, text)

    def write_bytes(self, key: str, data: bytes) -> None:
        self._write("write_bytes", key, data)

    def create_bytes(self, key: str, data: bytes) -> None:
        self.inner.create_bytes(key, data)
        if key.startswith(self.PROTECTED):
            self.new_keys.append(key)

    def read_text(self, key: str) -> str:
        return self.inner.read_text(key)

    def read_bytes(self, key: str) -> bytes:
        return self.inner.read_bytes(key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def list(self, prefix: str = "") -> list[str]:
        return self.inner.list(prefix)


def everything_stored(store) -> str:
    """El contenido de todo el workspace como texto (para buscar lo que no debe estar)."""
    return "\n".join(
        store.read_bytes(key).decode("utf-8", "replace") for key in store.list("") if key
    )
