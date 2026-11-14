"""Avisos de seguridad y releases públicos de GitHub: consulta anónima diaria con caché de 48 h.

Sin tokens. Solo `api.github.com`, sin redirecciones, con tiempos límite y paginación acotada.
Sin red o con respuesta inválida se conserva la caché hasta que caduca; después, «desconocido».
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ens_evidence.monitoring.files import write_json
from ens_evidence.monitoring.model import instant, parse_instant

API = "https://api.github.com"
REFRESH_AFTER = timedelta(hours=24)
CACHE_TTL = timedelta(hours=48)
MAX_PAGES = 5
TIMEOUT = httpx.Timeout(10.0)
CACHE_FILE = "github.json"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
_LOGGER = logging.getLogger("ens_evidence.monitoring")
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ens-evidence-monitoring",
}


@dataclass(frozen=True)
class Advisory:
    ghsa_id: str
    severity: str
    ranges: tuple[str, ...]


@dataclass(frozen=True)
class RepoFeed:
    fetched_at: datetime
    advisories: tuple[Advisory, ...]
    latest_release: str | None


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=API, headers=HEADERS, timeout=TIMEOUT, follow_redirects=False, transport=transport
    )


def _pages(client: httpx.Client, path: str) -> list[Any]:
    items: list[Any] = []
    url: str | None = path
    for _ in range(MAX_PAGES):
        response = client.get(url, params={"per_page": 100} if url == path else None)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list):
            raise ValueError("respuesta no es una lista")
        items.extend(body)
        match = _NEXT.search(response.headers.get("link", ""))
        url = match.group(1) if match and match.group(1).startswith(f"{API}/") else None
        if url is None:
            return items
    raise ValueError("demasiadas páginas")


def _advisory(raw: Any) -> Advisory | None:
    if (
        not isinstance(raw, dict)
        or raw.get("withdrawn_at")
        or raw.get("state") not in (None, "published")
    ):
        return None
    ghsa = raw.get("ghsa_id")
    ranges = tuple(
        str(v.get("vulnerable_version_range") or "")
        for v in raw.get("vulnerabilities") or []
        if isinstance(v, dict)
    )
    if not isinstance(ghsa, str):
        return None
    return Advisory(ghsa[:40], str(raw.get("severity") or "unknown")[:20], ranges or ("",))


def fetch_repo(client: httpx.Client, repo: str, now: datetime) -> RepoFeed:
    if not _REPO.fullmatch(repo):
        raise ValueError("repositorio no válido")
    advisories = tuple(
        a for a in map(_advisory, _pages(client, f"/repos/{repo}/security-advisories")) if a
    )
    release = client.get(f"/repos/{repo}/releases/latest")
    latest = None
    if release.status_code == 200 and isinstance(release.json(), dict):
        tag = release.json().get("tag_name")
        latest = tag[:40] if isinstance(tag, str) else None
    elif release.status_code != 404:
        release.raise_for_status()
    return RepoFeed(now, advisories, latest)


class AdvisoryFeed:
    """Caché en `<estado>/monitoring/github.json`; escribe solo el bucle del scheduler."""

    def __init__(self, state_dir: Path, client: httpx.Client | None = None) -> None:
        self._path = state_dir / "monitoring" / CACHE_FILE
        self._client = client

    def get(self, repo: str, now: datetime) -> RepoFeed | None:
        """Avisos de menos de 24 h, refrescando si toca; `None` si la consulta diaria falla."""
        cache = self._read()
        cached = cache.get(repo)
        if cached is None or now - cached.fetched_at >= REFRESH_AFTER:
            try:
                cached = fetch_repo(self._client or make_client(), repo, now)
            except (httpx.HTTPError, ValueError, KeyError):
                _LOGGER.warning("avisos de GitHub no disponibles para %s", repo)
                return None
            cache[repo] = cached
            self._write(cache)
        return cached if cached.fetched_at <= now + timedelta(minutes=5) else None

    def reference(self, repo: str, now: datetime) -> RepoFeed | None:
        """Último resultado guardado (hasta 48 h), solo como dato fechado; nunca como veredicto."""
        cached = self._read().get(repo)
        if cached is None or not cached.fetched_at <= now + timedelta(minutes=5):
            return None
        return cached if now - cached.fetched_at <= CACHE_TTL else None

    def _read(self) -> dict[str, RepoFeed]:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            return {repo: _feed(entry) for repo, entry in raw.items() if _REPO.fullmatch(repo)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def _write(self, cache: dict[str, RepoFeed]) -> None:
        document = {
            repo: {
                "fetched_at": instant(feed.fetched_at),
                "latest_release": feed.latest_release,
                "advisories": [
                    {"ghsa_id": a.ghsa_id, "severity": a.severity, "ranges": list(a.ranges)}
                    for a in feed.advisories
                ],
            }
            for repo, feed in cache.items()
        }
        write_json(self._path, document)


def _feed(entry: dict[str, Any]) -> RepoFeed:
    fetched = parse_instant(entry["fetched_at"])
    if fetched is None:
        raise ValueError("fecha")
    return RepoFeed(
        fetched,
        tuple(
            Advisory(str(a["ghsa_id"]), str(a["severity"]), tuple(str(r) for r in a["ranges"]))
            for a in entry["advisories"]
        ),
        entry.get("latest_release"),
    )


# --- ¿afecta el aviso a la versión instalada? ---------------------------------------------------

_VERSION = re.compile(r"v?(\d+(?:\.\d+){0,3})")
_TERM = re.compile(r"(<=|>=|<|>|=)\s*(\S+)")


def parse_version(raw: str) -> tuple[int, ...] | None:
    """Solo versiones numéricas puras; una preliminar (`-rc1`) no se decide."""
    match = _VERSION.fullmatch(raw.strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else None


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)), b + (0,) * (size - len(b))


def affects(version: tuple[int, ...], vulnerable_range: str) -> bool | None:
    """`True`/`False`, o `None` si el rango no se puede interpretar."""
    terms = [t.strip() for t in vulnerable_range.split(",") if t.strip()]
    if not terms:
        return None
    verdict = True
    for term in terms:
        match = _TERM.fullmatch(term)
        bound = parse_version(match.group(2)) if match else None
        if not match or bound is None:
            return None
        left, right = _pad(version, bound)
        verdict &= {
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
            "=": left == right,
        }[match.group(1)]
    return verdict


def judge(version: str | None, feed: RepoFeed) -> tuple[str, str]:
    """`("fail"|"review"|"clear", detalle)` para la versión instalada."""
    parsed = parse_version(version) if version else None
    if parsed is None:
        return "review", "Versión instalada no interpretable; revisa los avisos del proyecto."
    hits, undecided = [], []
    for advisory in feed.advisories:
        verdicts = [affects(parsed, r) for r in advisory.ranges]
        if any(v is True for v in verdicts):
            hits.append(advisory)
        elif any(v is None for v in verdicts):
            undecided.append(advisory)
    if hits:
        listed = ", ".join(f"{a.ghsa_id} ({a.severity})" for a in hits[:3])
        return "fail", f"La versión {version} figura en avisos de seguridad: {listed}."
    if undecided:
        listed = ", ".join(a.ghsa_id for a in undecided[:3])
        return "review", f"Hay avisos cuyo rango no se puede decidir: {listed}; revisar."
    return "clear", f"Sin avisos publicados que afecten a {version}."
