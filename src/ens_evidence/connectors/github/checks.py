"""Checks de GitHub sobre la evidencia recogida.

`PASS` solo con evidencia positiva demostrada; `FAIL` solo con evidencia negativa positiva
(los estados explícitos que produce la recogida); todo lo demás es `UNKNOWN` con motivo y
la traducción del ADR 0002 cuando la sección o el ítem no se recogió con éxito. Con la
recogida global sin éxito ningún check da `PASS`. `CheckResult.subject` es el repositorio
o la organización evaluada.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ens_evidence.connectors.sections import worst
from ens_evidence.models.contracts import CheckResult, CollectionResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus

ORG_2FA = "github.org_2fa_required"
BRANCH_PROTECTED = "github.default_branch_protected"
SECRET_SCANNING = "github.secret_scanning_enabled"
DEPENDABOT = "github.dependabot_alerts_enabled"

_RULE_FOR = {
    "pull request": "pull_request",
    "bloqueo de force-push": "non_fast_forward",
    "bloqueo de borrado": "deletion",
}


@dataclass(frozen=True)
class Outcome:
    status: CheckStatus
    reason: str
    collection: CollectionStatus = CollectionStatus.SUCCESS
    code: str | None = None


Builder = Callable[[str | None, Outcome], CheckResult]


def _builder(
    check_id: str, result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> Builder:
    def build(subject: str | None, outcome: Outcome) -> CheckResult:
        return CheckResult(
            check_id=check_id,
            asset_id=result.asset_id,
            status=outcome.status,
            evaluated_at=evaluated_at,
            evidence_ids=[evidence_id],
            reason=outcome.reason,
            subject=subject,
            collection_status=outcome.collection,
            reason_code=outcome.code,
        )

    return build


def _unrecovered(collection: CollectionStatus, reason: str) -> Outcome:
    translation = translate_collection(collection)
    return Outcome(translation.status, reason, collection, translation.reason_code)


def _status(raw: Any) -> CollectionStatus:
    try:
        return CollectionStatus(raw)
    except ValueError:
        return CollectionStatus.ERROR


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _why(raw: dict[str, Any]) -> str:
    return str(raw.get("reason") or _status(raw.get("status")).value)


def _section(result: CollectionResult, name: str) -> dict[str, Any] | None:
    raw = _dict(result.data.get("sections")).get(name)
    return raw if isinstance(raw, dict) else None


def _global_failure(result: CollectionResult) -> Outcome | None:
    if result.collection_status is CollectionStatus.SUCCESS:
        return None
    detail = result.data.get("error") or result.collection_status.value
    return _unrecovered(result.collection_status, f"recogida de GitHub fallida: {detail}")


def _absent(name: str) -> Outcome:
    return _unrecovered(CollectionStatus.ERROR, f"sección {name} ausente en la evidencia")


def org_2fa_required(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    build = _builder(ORG_2FA, result, evidence_id, evaluated_at)
    subject = str(result.data.get("organization") or "") or None
    if failure := _global_failure(result):
        return [build(subject, failure)]
    section = _section(result, "organization")
    if section is None:
        return [build(subject, _absent("organization"))]
    requirement = _dict(_dict(section.get("summary")).get("two_factor_requirement"))
    status = _status(requirement.get("status"))
    if status is not CollectionStatus.SUCCESS:
        return [
            build(
                subject, _unrecovered(status, f"requisito de 2FA no recogido: {_why(requirement)}")
            )
        ]
    match requirement.get("enabled"):
        case True:
            outcome = Outcome(CheckStatus.PASS, "la organización exige 2FA a sus miembros")
        case False:
            outcome = Outcome(CheckStatus.FAIL, "la organización no exige 2FA a sus miembros")
        case _:
            outcome = Outcome(CheckStatus.UNKNOWN, "el requisito de 2FA no figura en la evidencia")
    return [build(subject, outcome)]


def _repositories(result: CollectionResult) -> tuple[list[dict[str, Any]], Outcome | None]:
    section = _section(result, "repositories")
    if section is None:
        return [], _absent("repositories")
    status = _status(section.get("status"))
    if status is not CollectionStatus.SUCCESS:
        return [], _unrecovered(status, f"repositorios no recogidos: {_why(section)}")
    return [r for r in _list(section.get("items")) if isinstance(r, dict)], None


def _repo_item(section: dict[str, Any], name: str) -> dict[str, Any] | Outcome:
    """El ítem del repositorio, o el resultado que explica por qué no hay dato."""
    section_status = _status(section.get("status"))
    for item in _list(section.get("items")):
        if isinstance(item, dict) and item.get("repository") == name:
            return item
    if section_status is not CollectionStatus.SUCCESS:
        return _unrecovered(section_status, f"sección no recogida: {_why(section)}")
    return _unrecovered(CollectionStatus.ERROR, "sin dato para el repositorio en la evidencia")


def _per_repository(
    check_id: str,
    section_name: str,
    decide: Callable[[dict[str, Any]], Outcome],
    result: CollectionResult,
    evidence_id: str,
    evaluated_at: datetime,
    *,
    skip_archived: bool = False,
) -> list[CheckResult]:
    build = _builder(check_id, result, evidence_id, evaluated_at)
    if failure := _global_failure(result):
        return [build(None, failure)]
    repos, failure = _repositories(result)
    if failure:
        return [build(None, failure)]
    section = _section(result, section_name)
    checks = []
    for repo in repos:
        name = str(repo.get("name"))
        if skip_archived and repo.get("archived") is True:
            continue
        if section is None:
            outcome = _absent(section_name)
        else:
            item = _repo_item(section, name)
            outcome = item if isinstance(item, Outcome) else decide(item)
        checks.append(build(name, outcome))
    checks.extend(build(name, _invisible_to_token()) for name in _missing_from_token(result))
    return checks


def _missing_from_token(result: CollectionResult) -> list[str]:
    summary = _dict(_dict(_section(result, "repositories")).get("summary"))
    return [str(name) for name in _list(summary.get("missing_from_token"))]


def _invisible_to_token() -> Outcome:
    return _unrecovered(
        CollectionStatus.PERMISSION_DENIED, "repositorio del inventario no visible para el token"
    )


def _feature(
    enabled_reason: str, disabled_reason: str, unknown_reason: str
) -> Callable[[dict[str, Any]], Outcome]:
    def decide(item: dict[str, Any]) -> Outcome:
        status = _status(item.get("status"))
        if status is not CollectionStatus.SUCCESS:
            return _unrecovered(status, f"lectura no recogida: {_why(item)}")
        match item.get("state"):
            case "enabled":
                return Outcome(CheckStatus.PASS, enabled_reason)
            case "disabled":
                return Outcome(CheckStatus.FAIL, disabled_reason)
            case _:
                return Outcome(CheckStatus.UNKNOWN, unknown_reason)

    return decide


_decide_secret_scanning = _feature(
    "secret scanning activado",
    "secret scanning desactivado",
    "el estado de secret scanning no figura en la evidencia",
)
_decide_dependabot = _feature(
    "alertas de Dependabot activadas",
    "alertas de Dependabot desactivadas",
    "el estado de las alertas de Dependabot no figura en la evidencia",
)


def secret_scanning_enabled(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _per_repository(
        SECRET_SCANNING,
        "secret_scanning",
        _decide_secret_scanning,
        result,
        evidence_id,
        evaluated_at,
        skip_archived=True,
    )


def dependabot_alerts_enabled(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _per_repository(
        DEPENDABOT,
        "dependabot",
        _decide_dependabot,
        result,
        evidence_id,
        evaluated_at,
        skip_archived=True,
    )


@dataclass
class _Requirement:
    """Estado de una regla (PR, force-push, borrado) combinando todas sus fuentes."""

    proven: bool = False
    classic_negative: bool = False
    provider_seen: bool = False
    notes: list[str] = field(default_factory=list)
    failed: list[CollectionStatus] = field(default_factory=list)


def _ruleset_bypass(req: _Requirement, rulesets: dict[str, Any], ruleset_id: Any) -> bool:
    """Si el bypass queda descartado; si no, deja la nota y la fuente caída en `req`."""
    inventory = _status(rulesets.get("status"))
    if inventory is not CollectionStatus.SUCCESS:
        req.failed.append(inventory)
        req.notes.append("bypass no visible (inventario de rulesets no recogido)")
        return False
    entry = next(
        (
            r
            for r in _list(rulesets.get("items"))
            if isinstance(r, dict) and r.get("id") == ruleset_id
        ),
        None,
    )
    if entry is None:
        req.notes.append("bypass no visible (ruleset sin detalle en la evidencia)")
        return False
    detail = _status(entry.get("status"))
    if detail is not CollectionStatus.SUCCESS:
        req.failed.append(detail)
        req.notes.append("bypass no visible (ruleset sin detalle en la evidencia)")
        return False
    actors = entry.get("bypass_actors")
    if actors is None:
        req.notes.append("bypass no visible (el token no puede leer bypass_actors)")
        return False
    if actors:
        req.notes.append("bypass configurado en un ruleset: requiere revisión humana")
        return False
    return True


def _from_rulesets(req: _Requirement, rule_type: str, repo: dict[str, Any]) -> None:
    effective = _dict(repo.get("effective_rules"))
    status = _status(effective.get("status"))
    if status is not CollectionStatus.SUCCESS:
        req.failed.append(status)
        req.notes.append(f"reglas efectivas no recogidas: {_why(effective)}")
        return
    rulesets = _dict(repo.get("rulesets"))
    for rule in _list(effective.get("rules")):
        if not isinstance(rule, dict) or rule.get("type") != rule_type:
            continue
        req.provider_seen = True
        if _ruleset_bypass(req, rulesets, rule.get("ruleset_id")):
            req.proven = True


def _classic_enforced(classic: dict[str, Any]) -> tuple[bool, str | None]:
    """Los administradores no están exentos de la protección clásica."""
    match classic.get("enforce_admins"):
        case True:
            return True, None
        case False:
            return False, "bypass configurado: la protección clásica no se aplica a administradores"
        case _:
            return False, "enforce_admins no visible en la protección clásica"


def _from_classic(req: _Requirement, rule_type: str, repo: dict[str, Any]) -> None:
    classic = _dict(repo.get("classic"))
    status = _status(classic.get("status"))
    if status is not CollectionStatus.SUCCESS:
        req.failed.append(status)
        req.notes.append(f"protección clásica no recogida: {_why(classic)}")
        return
    match classic.get("state"):
        case "unprotected":
            req.classic_negative = True
            return
        case "protected":
            pass
        case _:
            req.notes.append("protección clásica sin estado demostrable")
            return
    flags = {
        "pull_request": ("requires_pull_request", True),
        "non_fast_forward": ("allow_force_pushes", False),
        "deletion": ("allow_deletions", False),
    }
    key, required = flags[rule_type]
    value = classic.get(key)
    if value is not required:
        if value is None:
            req.notes.append(f"{key} no visible en la protección clásica")
        else:
            req.classic_negative = True
        return
    if rule_type == "pull_request" and classic.get("bypass_pull_request_allowances") is not False:
        req.notes.append("bypass configurado: la protección clásica admite saltarse el PR")
        return
    enforced, note = _classic_enforced(classic)
    if enforced:
        req.proven = True
    elif note:
        req.notes.append(note)


def _requirement(rule_type: str, repo: dict[str, Any]) -> _Requirement:
    req = _Requirement()
    _from_rulesets(req, rule_type, repo)
    _from_classic(req, rule_type, repo)
    return req


def _branch_outcome(repo: dict[str, Any]) -> Outcome:
    requirements = {label: _requirement(rule, repo) for label, rule in _RULE_FOR.items()}
    missing = [
        label
        for label, req in requirements.items()
        if not req.proven and req.classic_negative and not req.provider_seen and not req.failed
    ]
    if missing:
        return Outcome(
            CheckStatus.FAIL,
            "la rama por defecto no exige: " + ", ".join(missing),
        )
    open_items = {label: req for label, req in requirements.items() if not req.proven}
    if not open_items:
        return Outcome(
            CheckStatus.PASS,
            "la rama por defecto exige PR y bloquea force-push y borrado, "
            "sin bypass demostrable en las fuentes que lo aportan",
        )
    failed = [status for req in open_items.values() for status in req.failed]
    reason = "; ".join(
        f"{label}: {', '.join(dict.fromkeys(req.notes)) or 'no demostrable'}"
        for label, req in open_items.items()
    )
    if failed:
        return _unrecovered(worst(failed), f"protección de la rama no demostrable: {reason}")
    return Outcome(CheckStatus.UNKNOWN, f"protección de la rama no demostrable: {reason}")


def default_branch_protected(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _per_repository(
        BRANCH_PROTECTED,
        "branch_rules",
        _branch_outcome,
        result,
        evidence_id,
        evaluated_at,
        skip_archived=True,
    )
