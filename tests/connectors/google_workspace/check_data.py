"""`CollectionResult` mínimos para probar los checks de Workspace sin pasar por la API."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ens_evidence.models.contracts import CheckResult, CollectionResult

EVIDENCE_ID = "ev-gw-1"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

Check = Callable[[CollectionResult, str, Any], list[CheckResult]]

OMITTED: Any = object()
_UNSET: Any = object()


def section(items=(), status="SUCCESS", reason=None, **summary):
    return {"status": status, "reason": reason, "items": list(items), "summary": summary}


def user(
    email="ana@acme.es",
    *,
    suspended=False,
    archived=False,
    is_admin=False,
    is_delegated_admin=False,
    enrolled=True,
    enforced=True,
):
    """Usuario normalizado; un flag en `OMITTED` no figura (Google no lo devolvió)."""
    flags = {
        "suspended": suspended,
        "archived": archived,
        "is_admin": is_admin,
        "is_delegated_admin": is_delegated_admin,
        "enrolled_2sv": enrolled,
        "enforced_2sv": enforced,
    }
    present = {key: value for key, value in flags.items() if value is not OMITTED}
    return {"id": f"id-{email}", "email": email, **present}


def roles_item(who, roles=(), *, status="SUCCESS", reason=None):
    """Ítem de `admins`; `roles=None`: la consulta no se completó."""
    return {
        "user_id": who["id"],
        "is_admin": who.get("is_admin"),
        "is_delegated_admin": who.get("is_delegated_admin"),
        "status": status,
        "reason": reason,
        "roles": None if roles is None else list(roles),
    }


def admins_section(*items, status="SUCCESS", reason=None, complete=None):
    done = status == "SUCCESS" if complete is None else complete
    return section(items, status, reason, complete=done, users=len(items))


def result(
    users=(), *, admins=_UNSET, users_section=None, collection_status="SUCCESS", error=None
) -> CollectionResult:
    """Recogida; por defecto cada usuario tiene su consulta de roles completa y sin roles.

    `admins=None` omite la sección.
    """
    users = list(users)
    if admins is _UNSET:
        admins = admins_section(*(roles_item(u) for u in users))
    names = {"users": users_section or section(users)}
    if admins is not None:
        names["admins"] = admins
    return CollectionResult(
        source="google-workspace",
        collector="google-workspace",
        collected_at=NOW,
        asset_id="gw-acme",
        collection_status=collection_status,
        data={"customer": "my_customer", "error": error, "sections": names},
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
