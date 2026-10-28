"""Check `cloudflare.tunnel_requires_access`: toda ruta publicada por Tunnel exige Access.

Sin túneles (sección recogida con éxito) no hay rutas que evaluar: no se emite ningún
`CheckResult` y tampoco se afirma un `PASS` sin evidencia.

`PASS` solo si se demuestra la protección de la ruta completa; lo claramente
expuesto es `FAIL`; lo no demostrable es `UNKNOWN` con motivo. `CheckResult`
lleva en `subject` la identidad estable de la ruta: `<tunnel_id> <hostname><path>`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from fnmatch import fnmatchcase
from functools import partial
from typing import Any

from ens_evidence.connectors.cloudflare.sections import worst
from ens_evidence.models.contracts import CheckResult, CollectionResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus

CHECK_ID = "cloudflare.tunnel_requires_access"
DECLARED_PUBLIC = "declared_public"
_IGNORED_APP_TYPES = frozenset({"bookmark"})
_PROVABLE_APP_TYPES = frozenset({"self_hosted", "ssh", "vnc", "rdp"})
_ANCHORED_LITERAL = re.compile(r"^\^(/[A-Za-z0-9_\-/~%]*?)(?:\.\*)?$")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")
_PORT_SUFFIX = re.compile(r":\d*$")
_HOST_GLOB = re.compile(r"^[a-z0-9*.-]+$")
_DECISIONS = frozenset({"allow", "deny", "non_identity", "bypass"})
_ADMITTING_DECISIONS = frozenset({"allow", "non_identity"})
_CREDENTIAL_SELECTORS = frozenset(
    {"service_token", "any_valid_service_token", "certificate", "common_name"}
)
_IDENTITY_SELECTORS = frozenset(
    {
        "email",
        "email_domain",
        "email_list",
        "group",
        "github-organization",
        "azureAD",
        "gsuite",
        "okta",
        "saml",
        "oidc",
    }
)
_OTHER_KNOWN_SELECTORS = frozenset(
    {
        "everyone",
        "login_method",
        "auth_method",
        "external_evaluation",
        "ip",
        "ip_list",
        "geo",
        "service_token",
        "any_valid_service_token",
        "certificate",
        "common_name",
        "device_posture",
        "warp",
        "gateway",
    }
)


@dataclass(frozen=True)
class Host:
    name: str
    wildcard: bool  # `*.zona`: un solo nivel bajo `zona`, sin el apex


@dataclass(frozen=True)
class PathPattern:
    value: str
    prefix: bool  # cubre todo lo que empiece por `value`; si no, `value` y sus subpaths


@dataclass(frozen=True)
class Destination:
    uri: str
    raw_host: str
    host: Host | None
    path: PathPattern | None  # `None`: patrón que no se sabe interpretar


@dataclass(frozen=True)
class Route:
    tunnel_id: str
    hostname: str
    display_path: str
    host: Host | None
    path: PathPattern
    local: bool

    @property
    def subject(self) -> str:
        return f"{self.tunnel_id} {self.hostname}{self.display_path}"


@dataclass(frozen=True)
class DeclaredRoute:
    hostname: str
    display_path: str
    host: Host
    path: PathPattern
    reason: str


@dataclass(frozen=True)
class Candidate:
    app: dict[str, Any]
    destination: Destination
    note: str = ""

    @property
    def label(self) -> str:
        name = self.app.get("name") or self.app["id"]
        suffix = f" [{self.note}]" if self.note else ""
        return f"«{name}» ({self.destination.uri}){suffix}"

    @property
    def host_rank(self) -> bool:
        assert self.destination.host is not None
        return not self.destination.host.wildcard

    @property
    def path_rank(self) -> int:
        assert self.destination.path is not None
        return len(self.destination.path.value)

    def dominates(self, other: "Candidate") -> bool:
        """Más específica en host y en path a la vez; sin orden total no hay precedencia."""
        mine, theirs = (self.host_rank, self.path_rank), (other.host_rank, other.path_rank)
        return mine != theirs and mine[0] >= theirs[0] and mine[1] >= theirs[1]


class Protection(StrEnum):
    PROTECTIVE = "protective"
    EXPOSED = "exposed"
    UNPROVABLE = "unprovable"


@dataclass(frozen=True)
class Verdict:
    protection: Protection
    detail: str
    collection: CollectionStatus = CollectionStatus.SUCCESS


@dataclass(frozen=True)
class Outcome:
    status: CheckStatus
    reason: str
    collection: CollectionStatus = CollectionStatus.SUCCESS
    code: str | None = None


@dataclass(frozen=True)
class SectionData:
    status: CollectionStatus
    reason: str | None
    items: list[dict[str, Any]]
    summary: dict[str, Any]


def parse_host(raw: str) -> Host | None:
    name = raw.strip().lower().rstrip(".")
    if not name or ":" in name:
        return None
    if name.startswith("*."):
        zone = name[2:]
        return None if "*" in zone else Host(zone, True)
    return None if "*" in name else Host(name, False)


def parse_path(raw: str | None) -> PathPattern | None:
    """Path de Access o de una ruta pública declarada (glob); la `/` final no cuenta."""
    text = (raw or "").strip()
    if not text or text == "*":
        return PathPattern("/", True)
    if not text.startswith("/"):
        text = "/" + text
    if "*" in text[:-1]:
        return None
    if text.endswith("*"):
        return PathPattern(text[:-1], True)
    text = text.rstrip("/")
    return PathPattern(text, False) if text else PathPattern("/", True)


def ingress_path(raw: str | None) -> PathPattern:
    """Prefijo solo para `^/literal` con `.*` final opcional; el resto de regex es todo el host."""
    anchored = _ANCHORED_LITERAL.match((raw or "").strip())
    return PathPattern(anchored.group(1), True) if anchored else PathPattern("/", True)


def _is_other_host(hostname: str, raw_host: str) -> bool:
    """Un destino no interpretable solo se descarta si su host es, sin duda, otro."""
    name = _PORT_SUFFIX.sub("", raw_host).rstrip(".")
    return bool(_HOST_GLOB.match(name)) and not fnmatchcase(hostname.lower(), name)


def parse_destination(uri: str) -> Destination:
    text = _SCHEME.sub("", uri.strip())
    raw_host, slash, rest = text.partition("/")
    return Destination(uri, raw_host.lower(), parse_host(raw_host), parse_path(slash + rest))


def _one_level_under(name: str, zone: str) -> bool:
    return name.endswith("." + zone) and "." not in name[: -len(zone) - 1]


def host_covers(covering: Host, target: Host) -> bool:
    if covering.wildcard:
        if target.wildcard:
            return covering.name == target.name
        return _one_level_under(target.name, covering.name)
    return not target.wildcard and covering.name == target.name


def host_overlaps(candidate: Host, target: Host) -> bool:
    if host_covers(candidate, target):
        return True
    return (
        target.wildcard and not candidate.wildcard and _one_level_under(candidate.name, target.name)
    )


def path_covers(covering: PathPattern, target: PathPattern) -> bool:
    if covering.prefix:
        return target.value.startswith(covering.value)
    inherited = target.value.startswith(covering.value + "/")
    return inherited or (not target.prefix and covering.value == target.value)


def path_overlaps(candidate: PathPattern, target: PathPattern) -> bool:
    if path_covers(candidate, target):
        return True
    return target.prefix and candidate.value.startswith(target.value)


def classify_policies(policies: list[dict[str, Any]]) -> Verdict:
    decisions = [policy.get("decision") for policy in policies]
    if "bypass" in decisions:
        return Verdict(Protection.EXPOSED, "tiene una política bypass")
    admitting = [p for p in policies if p.get("decision") in _ADMITTING_DECISIONS]
    for policy in admitting:
        if "everyone" in policy.get("include", []) and not policy.get("require"):
            return Verdict(
                Protection.EXPOSED,
                f"tiene una política {policy['decision']} para everyone sin require",
            )
    if any(decision not in _DECISIONS for decision in decisions):
        return Verdict(Protection.UNPROVABLE, "tiene una política con decisión desconocida")
    known = _IDENTITY_SELECTORS | _OTHER_KNOWN_SELECTORS
    selectors = {
        kind
        for policy in admitting
        for key in ("include", "require")
        for kind in policy.get(key, [])
    }
    if not selectors <= known:
        return Verdict(Protection.UNPROVABLE, "tiene selectores de política desconocidos")
    if any(not _requires_credential(policy) for policy in admitting):
        return Verdict(
            Protection.UNPROVABLE,
            "tiene políticas que no exigen identidad ni credencial (IP, geo, everyone...)",
        )
    allows = [policy for policy in admitting if policy["decision"] == "allow"]
    if any(_authenticates_users(policy) for policy in allows):
        return Verdict(Protection.PROTECTIVE, "política allow con selector de identidad")
    if not allows:
        return Verdict(Protection.UNPROVABLE, "no tiene ninguna política allow")
    return Verdict(
        Protection.UNPROVABLE,
        "sus políticas allow no exigen identidad (solo IP, geo, certificado o service token)",
    )


def _requires_credential(policy: dict[str, Any]) -> bool:
    include = policy.get("include", [])
    return _authenticates_users(policy) or (bool(include) and set(include) <= _CREDENTIAL_SELECTORS)


def _authenticates_users(policy: dict[str, Any]) -> bool:
    include, require = policy.get("include", []), policy.get("require", [])
    return (bool(include) and set(include) <= _IDENTITY_SELECTORS) or any(
        kind in _IDENTITY_SELECTORS for kind in require
    )


def _read(sections: dict[str, Any], name: str) -> SectionData:
    raw = sections.get(name)
    if not isinstance(raw, dict):
        return SectionData(CollectionStatus.ERROR, "sección ausente en la evidencia", [], {})
    return SectionData(
        CollectionStatus(raw["status"]),
        raw.get("reason"),
        raw.get("items", []),
        raw.get("summary", {}),
    )


def _unrecovered(collection: CollectionStatus, reason: str) -> Outcome:
    translation = translate_collection(collection)
    return Outcome(translation.status, reason, collection, translation.reason_code)


def _why(section: SectionData) -> str:
    return section.reason or section.status.value


class _Evaluator:
    def __init__(self, result: CollectionResult) -> None:
        sections = result.data.get("sections", {})
        self.zones = _read(sections, "zones")
        self.dns = _read(sections, "dns")
        self.applications = _read(sections, "access_applications")
        self.policies = _read(sections, "access_policies")
        self.policy_items = {item["app_id"]: item for item in self.policies.items}
        self.declared = self._declared_routes(result.data.get("public_routes", []))

    @staticmethod
    def _declared_routes(raw_routes: list[dict[str, Any]]) -> list[DeclaredRoute]:
        declared = []
        for raw in raw_routes:
            host, path = parse_host(raw["hostname"]), parse_path(raw.get("path", "/*"))
            if host is not None and path is not None:
                declared.append(
                    DeclaredRoute(raw["hostname"], raw.get("path", "/*"), host, path, raw["reason"])
                )
        return declared

    def evaluate(self, route: Route) -> tuple[Outcome, list[DeclaredRoute]]:
        """Resultado de la ruta y subconjuntos declarados públicos excluidos de la evaluación.

        Una ruta pública declarada sin `*` (`/agent`) cubre también sus subpaths (`/agent/x`).
        """
        if route.host is None:
            return Outcome(
                CheckStatus.UNKNOWN, "hostname con patrón que no se sabe interpretar"
            ), []
        for entry in self.declared:
            if host_covers(entry.host, route.host) and path_covers(entry.path, route.path):
                return (
                    Outcome(
                        CheckStatus.UNKNOWN,
                        f"ruta declarada pública ({entry.reason}); requiere revisión humana",
                        code=DECLARED_PUBLIC,
                    ),
                    [],
                )
        if self.applications.status is not CollectionStatus.SUCCESS:
            return (
                _unrecovered(
                    self.applications.status,
                    f"aplicaciones Access no recogidas: {_why(self.applications)}",
                ),
                [],
            )
        covering, partial, uncertain = self._overlapping(route)
        specific, excluded = self._without_declared(partial)
        return self._outcome(covering, specific, uncertain, excluded), excluded

    def _overlapping(
        self, route: Route
    ) -> tuple[list[Candidate], list[Candidate], list[Candidate]]:
        assert route.host is not None
        covering: list[Candidate] = []
        partial: list[Candidate] = []
        uncertain: list[Candidate] = []
        for app in self.applications.items:
            app_type = app.get("type")
            if app_type in _IGNORED_APP_TYPES:
                continue
            provable = app_type in _PROVABLE_APP_TYPES
            for uri in app.get("public_destinations", []):
                dest = parse_destination(uri)
                candidate = Candidate(app, dest)
                if dest.host is None:
                    if not _is_other_host(route.hostname, dest.raw_host):
                        uncertain.append(candidate)
                elif not host_overlaps(dest.host, route.host):
                    continue
                elif dest.path is None:
                    uncertain.append(candidate)
                elif not provable and path_overlaps(dest.path, route.path):
                    uncertain.append(
                        Candidate(app, dest, f"tipo de aplicación {app_type or 'desconocido'}")
                    )
                elif host_covers(dest.host, route.host) and path_covers(dest.path, route.path):
                    covering.append(candidate)
                elif path_overlaps(dest.path, route.path):
                    partial.append(candidate)
        return covering, partial, uncertain

    def _without_declared(
        self, candidates: list[Candidate]
    ) -> tuple[list[Candidate], list[DeclaredRoute]]:
        kept, excluded = [], []
        for candidate in candidates:
            dest = candidate.destination
            assert dest.host is not None and dest.path is not None
            entry = next(
                (
                    e
                    for e in self.declared
                    if host_covers(e.host, dest.host) and path_covers(e.path, dest.path)
                ),
                None,
            )
            if entry is None:
                kept.append(candidate)
            elif entry not in excluded:
                excluded.append(entry)
        return kept, excluded

    def _verdict(self, app: dict[str, Any]) -> Verdict:
        item = self.policy_items.get(app["id"])
        if item is None:
            if self.policies.status is not CollectionStatus.SUCCESS:
                return Verdict(
                    Protection.UNPROVABLE,
                    f"sus políticas no se recogieron: {_why(self.policies)}",
                    self.policies.status,
                )
            return Verdict(Protection.UNPROVABLE, "no consta ninguna política recogida")
        status = CollectionStatus(item["status"])
        if status is not CollectionStatus.SUCCESS:
            reason = item.get("reason") or status.value
            return Verdict(
                Protection.UNPROVABLE, f"sus políticas no se recogieron: {reason}", status
            )
        return classify_policies(item.get("policies", []))

    def _outcome(
        self,
        covering: list[Candidate],
        specific: list[Candidate],
        uncertain: list[Candidate],
        excluded: list[DeclaredRoute],
    ) -> Outcome:
        verdicts = [(c, self._verdict(c.app)) for c in covering + specific]
        partial = {id(c) for c in specific}
        protective = [
            c for c, v in verdicts[: len(covering)] if v.protection is Protection.PROTECTIVE
        ]
        exposed = [(c, v) for c, v in verdicts if v.protection is Protection.EXPOSED]
        if exposed:
            return self._exposed(exposed, protective, partial)
        if unrecovered := [
            v.collection for _, v in verdicts if v.collection is not CollectionStatus.SUCCESS
        ]:
            detail = "; ".join(
                f"{c.label} {v.detail}"
                for c, v in verdicts
                if v.collection is not CollectionStatus.SUCCESS
            )
            return _unrecovered(worst(unrecovered), detail)
        if uncertain:
            labels = ", ".join(c.label for c in uncertain)
            return Outcome(
                CheckStatus.UNKNOWN,
                f"destinos no interpretables o de tipo de aplicación no verificable: {labels}",
            )
        for candidate, verdict in verdicts:
            if verdict.protection is Protection.UNPROVABLE:
                return Outcome(
                    CheckStatus.UNKNOWN,
                    f"no se demuestra la protección de la aplicación {candidate.label}: "
                    f"{verdict.detail}",
                )
        if not protective:
            if not specific:
                return Outcome(CheckStatus.FAIL, "ninguna aplicación Access cubre la ruta")
            labels = ", ".join(c.label for c in specific)
            return Outcome(
                CheckStatus.UNKNOWN,
                f"cobertura parcial: solo protegen una parte de la ruta {labels}",
            )
        labels = ", ".join(c.label for c in covering)
        note = ""
        if excluded:
            note = "; excluido el subconjunto declarado público " + ", ".join(
                f"{e.hostname}{e.display_path}" for e in excluded
            )
        return Outcome(
            CheckStatus.PASS,
            f"la ruta completa está cubierta por {labels} con política allow de identidad{note}",
        )

    @staticmethod
    def _exposed(
        exposed: list[tuple[Candidate, Verdict]],
        protective: list[Candidate],
        partial: set[int],
    ) -> Outcome:
        """La precedencia solo puede rebajar un FAIL a UNKNOWN, nunca producir un PASS."""
        for candidate, verdict in exposed:
            if not any(p.dominates(candidate) for p in protective):
                role = " más específica que solapa la ruta" if id(candidate) in partial else ""
                return Outcome(
                    CheckStatus.FAIL,
                    f"la aplicación{role} {candidate.label} no protege: {verdict.detail}",
                )
        labels = ", ".join(c.label for c, _ in exposed)
        return Outcome(
            CheckStatus.UNKNOWN,
            f"una app no protectora menos específica queda tapada; no demostrable: {labels}",
        )


Builder = Callable[[str | None, Outcome], CheckResult]


def _check_result(
    asset_id: str, evidence_id: str, evaluated_at: datetime, subject: str | None, outcome: Outcome
) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID,
        asset_id=asset_id,
        status=outcome.status,
        evaluated_at=evaluated_at,
        evidence_ids=[evidence_id],
        reason=outcome.reason,
        subject=subject,
        collection_status=outcome.collection,
        reason_code=outcome.code,
    )


def _remote_routes(tunnel: dict[str, Any]) -> list[Route]:
    routes: list[Route] = []
    for rule in tunnel.get("ingress", []):
        hostname = rule.get("hostname")
        if not hostname or str(rule.get("service", "")).startswith("http_status"):
            continue  # catch-all o respuesta fija sin origen: no es una ruta
        routes.append(
            Route(
                tunnel["id"],
                hostname,
                rule.get("path") or "/*",
                parse_host(hostname),
                ingress_path(rule.get("path")),
                False,
            )
        )
    return routes


def _unscoped_rules(tunnel: dict[str, Any]) -> list[str]:
    """Paths de reglas sin hostname con origen real: aplican a cualquier hostname."""
    return [
        rule.get("path") or "/*"
        for rule in tunnel.get("ingress", [])
        if not rule.get("hostname") and not str(rule.get("service", "")).startswith("http_status")
    ]


def _local_routes(tunnel: dict[str, Any]) -> list[Route]:
    return [
        Route(
            tunnel["id"],
            rule["hostname"],
            "/*",
            parse_host(rule["hostname"]),
            PathPattern("/", True),
            True,
        )
        for rule in tunnel.get("ingress", [])
        if rule.get("hostname")
    ]


def tunnel_requires_access(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    builder = partial(_check_result, result.asset_id, evidence_id, evaluated_at)
    if result.collection_status is not CollectionStatus.SUCCESS:
        detail = result.data.get("error") or result.collection_status.value
        outcome = _unrecovered(
            result.collection_status, f"recogida de Cloudflare fallida: {detail}"
        )
        return [builder(None, outcome)]
    tunnels = _read(result.data.get("sections", {}), "tunnels")
    if tunnels.status is not CollectionStatus.SUCCESS and not tunnels.items:
        outcome = _unrecovered(tunnels.status, f"túneles no recogidos: {_why(tunnels)}")
        return [builder(None, outcome)]
    evaluator = _Evaluator(result)
    checks: list[CheckResult] = []
    for tunnel in tunnels.items:
        checks += _tunnel_checks(tunnel, evaluator, builder)
    return checks


def _tunnel_checks(
    tunnel: dict[str, Any], evaluator: _Evaluator, builder: Builder
) -> list[CheckResult]:
    subject = tunnel["id"]
    status = CollectionStatus(tunnel["ingress_status"])
    local = status is CollectionStatus.NOT_SUPPORTED
    if not local and status is not CollectionStatus.SUCCESS:
        reason = tunnel.get("reason") or status.value
        return [builder(subject, _unrecovered(status, f"ingress no recogido: {reason}"))]
    routes = _local_routes(tunnel) if local else _remote_routes(tunnel)
    checks: list[CheckResult] = []
    seen = set()
    declared_subsets: list[DeclaredRoute] = []
    for route in routes:
        identity = (route.hostname.lower(), route.display_path)
        if identity in seen:
            continue
        seen.add(identity)
        outcome, excluded = evaluator.evaluate(route)
        if route.local and outcome.status is CheckStatus.PASS:
            outcome = Outcome(
                CheckStatus.UNKNOWN,
                "ingress local no visible: hostname inferido por DNS; no se demuestra que "
                f"toda la ruta pase por Access ({outcome.reason})",
            )
        checks.append(builder(route.subject, outcome))
        declared_subsets += [e for e in excluded if e not in declared_subsets]
    for path in dict.fromkeys([] if local else _unscoped_rules(tunnel)):
        checks.append(
            builder(
                f"{subject} (sin hostname){path}",
                Outcome(
                    CheckStatus.UNKNOWN,
                    "regla de ingress sin hostname: aplica a cualquier hostname del túnel y no se "
                    "puede resolver qué rutas publica",
                ),
            )
        )
    for entry in declared_subsets:
        if (entry.hostname.lower(), entry.display_path) in seen:
            continue
        checks.append(
            builder(
                f"{subject} {entry.hostname}{entry.display_path}",
                Outcome(
                    CheckStatus.UNKNOWN,
                    f"ruta declarada pública ({entry.reason}); requiere revisión humana",
                    code=DECLARED_PUBLIC,
                ),
            )
        )
    if local and (unresolved := _unresolved_coverage(tunnel, evaluator)):
        checks.append(builder(subject, unresolved))
    return checks


def _unresolved_coverage(tunnel: dict[str, Any], evaluator: _Evaluator) -> Outcome | None:
    """Un túnel local solo se conoce por DNS: sin DNS completo hay cobertura sin resolver."""
    if evaluator.dns.status is not CollectionStatus.SUCCESS:
        return _unrecovered(
            evaluator.dns.status,
            "ingress local no visible y DNS no recogido: "
            f"{_why(evaluator.dns)}; cobertura no resuelta",
        )
    missing = evaluator.zones.summary.get("missing_from_token", [])
    if missing:
        return Outcome(
            CheckStatus.UNKNOWN,
            f"ingress local no visible y el token no ve {len(missing)} zona(s) del inventario; "
            "cobertura no resuelta",
        )
    if not tunnel.get("inferred_complete"):
        return Outcome(CheckStatus.UNKNOWN, "ingress local no visible; cobertura no resuelta")
    return None
