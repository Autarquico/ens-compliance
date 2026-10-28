"""`CollectionResult` mínimos para probar los checks de GitHub sin pasar por la API."""

from collections.abc import Callable
from typing import Any

from ens_evidence.models.contracts import CheckResult, CollectionResult

from .fake_api import NOW, ORG

EVIDENCE_ID = "ev-gh-1"
ALL_RULES = ("pull_request", "non_fast_forward", "deletion")

Check = Callable[[CollectionResult, str, Any], list[CheckResult]]


def section(items=(), status="SUCCESS", reason=None, **summary):
    return {"status": status, "reason": reason, "items": list(items), "summary": summary}


def repo(name="facturas", *, archived=False):
    return {"name": name, "archived": archived, "default_branch": "main", "admin": True}


def result(*, repos=(), collection_status="SUCCESS", error=None, **sections) -> CollectionResult:
    """Recogida con `repositories` y las demás secciones dadas por nombre."""
    names = {"repositories": section(repos), **sections}
    return CollectionResult(
        source="github",
        collector="github",
        collected_at=NOW,
        asset_id="gh-taller",
        collection_status=collection_status,
        data={"organization": ORG, "error": error, "sections": names},
    )


def run(check: Check, evidence: CollectionResult) -> list[CheckResult]:
    results = check(evidence, EVIDENCE_ID, NOW)
    for item in results:
        CheckResult.model_validate(item.model_dump())
    return results


def only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, [(r.subject, r.status, r.reason) for r in results]
    return results[0]


def by_subject(results: list[CheckResult]) -> dict[str | None, CheckResult]:
    assert len({r.subject for r in results}) == len(results), "subjects duplicados"
    return {r.subject: r for r in results}


def two_factor(enabled=True, status="SUCCESS", reason=None):
    return section(
        status=status,
        two_factor_requirement={"status": status, "reason": reason, "enabled": enabled},
    )


def ruleset(ruleset_id=11, bypass_actors=(), *, status="SUCCESS", enforcement="active"):
    """Ítem del inventario de rulesets; `bypass_actors=None`: el token no lo ve."""
    actors = None if bypass_actors is None else list(bypass_actors)
    return {
        "id": ruleset_id,
        "status": status,
        "enforcement": enforcement,
        "bypass_actors": actors,
    }


def actor(mode="always", actor_type="OrganizationAdmin"):
    return {"actor_type": actor_type, "bypass_mode": mode}


def effective(*types, ruleset_id=11, status="SUCCESS", reason=None):
    return {
        "status": status,
        "reason": reason,
        "rules": [{"type": t, "ruleset_id": ruleset_id} for t in types],
    }


def classic(
    *,
    state="protected",
    pull_request=True,
    force_push=False,
    deletion=False,
    enforce_admins=True,
    bypass_allowances=False,
    status="SUCCESS",
    reason=None,
):
    """Protección clásica; `None` en una bandera la omite (no visible)."""
    if state != "protected":
        return {"status": status, "reason": reason, "state": state}
    return {
        "status": status,
        "reason": reason,
        "state": "protected",
        "requires_pull_request": pull_request,
        "bypass_pull_request_allowances": bypass_allowances,
        "enforce_admins": enforce_admins,
        "allow_force_pushes": force_push,
        "allow_deletions": deletion,
    }


NO_CLASSIC = classic(state="unprotected")
UNKNOWN_CLASSIC = classic(state="unknown", status="PERMISSION_DENIED", reason="sin permiso")


def branch_item(name="facturas", *, rulesets=(), rules=None, classic_part=NO_CLASSIC, status=None):
    """Ítem de `branch_rules`; `rulesets` y `rules` por defecto: ruleset 11 que aporta las tres."""
    inventory = [ruleset()] if rulesets == () else list(rulesets)
    rules = effective(*ALL_RULES) if rules is None else rules
    parts = {
        "rulesets": {"status": "SUCCESS", "items": inventory},
        "effective_rules": rules,
        "classic": classic_part,
    }
    return {"repository": name, "status": status or "SUCCESS", **parts}


def branch_result(*items, repos=None) -> CollectionResult:
    names = repos if repos is not None else [repo(i["repository"]) for i in items]
    return result(repos=names, branch_rules=section(items))
