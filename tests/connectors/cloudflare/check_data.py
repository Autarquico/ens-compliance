"""`CollectionResult` mínimos para probar `tunnel_requires_access` sin pasar por la API."""

from typing import Any

from ens_evidence.connectors.cloudflare.checks import tunnel_requires_access
from ens_evidence.connectors.cloudflare.identity_checks import access_idp_restricted
from ens_evidence.models.contracts import CheckResult, CollectionResult

from .fake_api import NOW

TUNNEL = "t-1"
EVIDENCE_ID = "ev-1"
ALLOW_IDENTITY = {"decision": "allow", "include": ["email"], "require": []}


def policy(decision="allow", include=("email",), require=(), include_groups=(), require_groups=()):
    return {
        "decision": decision,
        "include": list(include),
        "require": list(require),
        "include_groups": list(include_groups),
        "require_groups": list(require_groups),
    }


def group(group_id, include=("email",), require=(), include_groups=(), require_groups=()):
    return {
        "id": group_id,
        "include": list(include),
        "require": list(require),
        "include_groups": list(include_groups),
        "require_groups": list(require_groups),
    }


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
    groups_section=None,
    dns=None,
    zones=None,
    tunnels_section=None,
    collection_status="SUCCESS",
    extra_sections=None,
) -> CollectionResult:
    sections = {
        "zones": zones or section(),
        "dns": dns or section(),
        "access_applications": applications or section(a["app"] for a in apps),
        "access_policies": policies_section
        or section(a["policies"] for a in apps if a["policies"]),
        "access_groups": groups_section or section(),
        "tunnels": tunnels_section or section(tunnels),
        **(extra_sections or {}),
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


HOST = "app.z.example"


def idp(idp_id, idp_type, name=None):
    return {"id": idp_id, "name": name or idp_id, "type": idp_type}


def sel(kind, idp=None, group=None):
    return {"kind": kind, "idp": idp, "group": group}


def _kinds(selectors):
    return [s["kind"] for s in selectors]


def ipolicy(decision="allow", include=(), require=(), exclude=()):
    """Política ya normalizada por la recogida: tipos y selectores saneados."""
    return {
        "decision": decision,
        "include": _kinds(include),
        "require": _kinds(require),
        "exclude": _kinds(exclude),
        "include_selectors": list(include),
        "require_selectors": list(require),
        "exclude_selectors": list(exclude),
    }


def igroup(group_id, include=(), require=(), exclude=()):
    return {
        "id": group_id,
        **{k: v for k, v in ipolicy("allow", include, require, exclude).items() if k != "decision"},
    }


def iapp(app_id, *destinations, allowed="absent", **kwargs):
    """`allowed`: "absent", None (null), lista de ids o "malformed"."""
    entry = app(app_id, *(destinations or (HOST,)), **kwargs)
    if allowed == "absent":
        extra = {"allowed_idps_form": "absent", "allowed_idps": None}
    elif allowed is None:
        extra = {"allowed_idps_form": "null", "allowed_idps": None}
    elif allowed == "malformed":
        extra = {"allowed_idps_form": "malformed", "allowed_idps": None}
    else:
        extra = {"allowed_idps_form": "list", "allowed_idps": list(allowed)}
    entry["app"].update(extra)
    return entry


def build_identity(apps, idps, *, groups=(), identity_section=None, tunnels=None, **kwargs):
    """Cuenta con túnel en `HOST`, proveedores `idps` y las aplicaciones `apps`."""
    extra = {"identity_providers": identity_section or section(idps)}
    return build(
        tunnels if tunnels is not None else [remote(rule(HOST))],
        apps,
        groups_section=section(groups),
        extra_sections=extra,
        **kwargs,
    )


def run_identity(result: CollectionResult) -> list[CheckResult]:
    results = access_idp_restricted(result, EVIDENCE_ID, NOW)
    for check in results:
        CheckResult.model_validate(check.model_dump())
    return results
