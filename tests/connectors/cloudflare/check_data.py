"""`CollectionResult` mínimos para probar `tunnel_requires_access` sin pasar por la API."""

from typing import Any

from ens_evidence.connectors.cloudflare.checks import tunnel_requires_access
from ens_evidence.models.contracts import CheckResult, CollectionResult

from .fake_api import NOW

TUNNEL = "t-1"
EVIDENCE_ID = "ev-1"
ALLOW_IDENTITY = {"decision": "allow", "include": ["email"], "require": []}


def policy(decision="allow", include=("email",), require=()):
    return {"decision": decision, "include": list(include), "require": list(require)}


def section(items=(), status="SUCCESS", reason=None, **summary):
    return {"status": status, "reason": reason, "items": list(items), "summary": summary}


def app(
    app_id,
    *destinations,
    type="self_hosted",
    policies=(ALLOW_IDENTITY,),
    policy_status="SUCCESS",
    collected_policies=True,
):
    """Aplicación Access con sus políticas ya recogidas (`collected_policies=False`: sin ellas)."""
    item = {"app_id": app_id, "status": policy_status, "policies": list(policies)}
    return {
        "app": {"id": app_id, "name": app_id, "type": type, "public_destinations": destinations},
        "policies": item if collected_policies else None,
    }


def rule(hostname, path=None, service="http://origin"):
    entry = {"hostname": hostname, "service": service, "inferred": False}
    return {**entry, "path": path}


def remote(*rules, tunnel_id=TUNNEL, ingress_status="SUCCESS", reason=None):
    return {
        "id": tunnel_id,
        "ingress": list(rules),
        "ingress_status": ingress_status,
        "reason": reason,
        "inferred_complete": False,
    }


def local(*hostnames, tunnel_id=TUNNEL, complete=True):
    return {
        "id": tunnel_id,
        "ingress": [
            {"hostname": host, "path": None, "service": None, "inferred": True}
            for host in hostnames
        ],
        "ingress_status": "NOT_SUPPORTED",
        "reason": "ingress local no visible por la API",
        "inferred_complete": complete,
    }


def build(
    tunnels,
    apps=(),
    *,
    public_routes=(),
    applications=None,
    policies_section=None,
    dns=None,
    zones=None,
    tunnels_section=None,
    collection_status="SUCCESS",
) -> CollectionResult:
    sections = {
        "zones": zones or section(),
        "dns": dns or section(),
        "access_applications": applications or section(a["app"] for a in apps),
        "access_policies": policies_section
        or section(a["policies"] for a in apps if a["policies"]),
        "tunnels": tunnels_section or section(tunnels),
    }
    return CollectionResult(
        source="cloudflare",
        collector="cloudflare",
        collected_at=NOW,
        asset_id="cf-taller",
        collection_status=collection_status,
        data={"sections": sections, "public_routes": list(public_routes), "error": None},
    )


def run(result: CollectionResult) -> list[CheckResult]:
    results = tunnel_requires_access(result, EVIDENCE_ID, NOW)
    for check in results:
        CheckResult.model_validate(check.model_dump())
    return results


def only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, [(r.subject, r.status, r.reason) for r in results]
    return results[0]


def by_subject(results: list[CheckResult]) -> dict[str | None, CheckResult]:
    assert len({r.subject for r in results}) == len(results), "subjects duplicados"
    return {r.subject: r for r in results}


def subject(hostname: str, path: str = "/*", tunnel_id: str = TUNNEL) -> str:
    return f"{tunnel_id} {hostname}{path}"


def declared(hostname: str, path: str = "/*", reason: str = "agente público") -> dict[str, Any]:
    return {"hostname": hostname, "path": path, "reason": reason}
