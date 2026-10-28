"""Secciones normalizadas de la recogida de GitHub.

Cada sección tiene su `CollectionStatus`; las secciones por repositorio tienen además
estado propio por repositorio. Solo se persisten campos de lista blanca, con tipos
estrictos: un campo omitido o `null` nunca equivale a `false`, y un 404 genérico es
"no demostrable" (`PERMISSION_DENIED`). Ni logins, ni títulos de deploy keys ni textos
libres de la API salen del parseo.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
)

from ens_evidence.connectors.github.client import (
    CredentialRejected,
    Finding,
    GitHubClient,
    GitHubError,
)
from ens_evidence.connectors.github.config import GitHubConfig, GitHubRun
from ens_evidence.connectors.sections import Section, worst
from ens_evidence.models.enums import CollectionStatus

PERMISSION_LEVELS = ("admin", "maintain", "push", "triage", "pull")
_REPO_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
_APP_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,59}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
NAME_LIMIT = 100


def _repo_name(value: str) -> str:
    if value in {".", ".."} or not _REPO_NAME.fullmatch(value):
        raise ValueError("nombre de repositorio no válido")
    return value


RepoName = Annotated[str, AfterValidator(_repo_name)]


class _Raw(BaseModel):
    """Forma mínima esperada de la API; lo que no se declara aquí se descarta."""

    model_config = ConfigDict(extra="ignore")


class _Permissions(_Raw):
    admin: StrictBool | None = None
    maintain: StrictBool | None = None
    push: StrictBool | None = None
    triage: StrictBool | None = None
    pull: StrictBool | None = None


class _Org(_Raw):
    login: str
    two_factor_requirement_enabled: StrictBool | None = None


class _Installation(_Raw):
    app_slug: str | None = None
    repository_selection: str | None = None
    permissions: dict[str, str] | None = None
    suspended_at: str | None = None


class _Feature(_Raw):
    status: str | None = None


class _SecurityAndAnalysis(_Raw):
    secret_scanning: _Feature | None = None


class _Repo(_Raw):
    name: RepoName
    visibility: str | None = None
    archived: StrictBool | None = None
    default_branch: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    permissions: _Permissions | None = None
    security_and_analysis: _SecurityAndAnalysis | None = None


class _RulesetRef(_Raw):
    id: StrictInt
    source_type: str | None = None
    enforcement: str | None = None
    target: str | None = None


class _BypassActor(_Raw):
    actor_type: str | None = None
    bypass_mode: str | None = None


class _RulesetDetail(_Raw):
    bypass_actors: list[_BypassActor] | None = None


class _EffectiveRule(_Raw):
    type: str
    ruleset_id: StrictInt | None = None
    ruleset_source_type: str | None = None


class _Enabled(_Raw):
    enabled: StrictBool | None = None


class _Allowances(_Raw):
    users: list[Any] | None = None
    teams: list[Any] | None = None
    apps: list[Any] | None = None


class _PullRequestReviews(_Raw):
    bypass_pull_request_allowances: _Allowances | None = None


class _ClassicProtection(_Raw):
    required_pull_request_reviews: _PullRequestReviews | None = None
    enforce_admins: _Enabled | None = None
    allow_force_pushes: _Enabled | None = None
    allow_deletions: _Enabled | None = None


class _Collaborator(_Raw):
    permissions: _Permissions | None = None


class _EnvironmentRule(_Raw):
    type: str | None = None
    reviewers: list[Any] | None = None


class _Environment(_Raw):
    name: str
    protection_rules: list[_EnvironmentRule] | None = None
    deployment_branch_policy: dict[str, Any] | None = None


class _ActionsPermissions(_Raw):
    enabled: StrictBool | None = None
    allowed_actions: str | None = None


class _WorkflowPermissions(_Raw):
    default_workflow_permissions: str | None = None
    can_approve_pull_request_reviews: StrictBool | None = None


class _DeployKey(_Raw):
    read_only: StrictBool
    created_at: datetime | None = None


def _attempt[T](fetch: Callable[[], T]) -> tuple[CollectionStatus, str | None, T | None]:
    """Ejecuta una lectura; un fallo saneado se convierte en estado y motivo."""
    try:
        return CollectionStatus.SUCCESS, None, fetch()
    except CredentialRejected:
        raise
    except GitHubError as exc:
        return exc.status, exc.message, None
    except (ValidationError, KeyError, TypeError, ValueError, AttributeError):
        return CollectionStatus.ERROR, "respuesta con forma inesperada", None


def _failed(status: CollectionStatus, reason: str | None) -> Section:
    return Section(status=status, reason=reason)


def _dependent_on(parent: Section, label: str) -> Section | None:
    if parent.status is CollectionStatus.SUCCESS:
        return None
    return _failed(parent.status, f"depende de {label}: {parent.reason or parent.status.value}")


def _sub(fetch: Callable[[], dict[str, Any]], fallback: dict[str, Any] | None = None) -> dict:
    """Lectura con estado propio; el `status` del payload (si lo hay) manda sobre SUCCESS."""
    status, reason, payload = _attempt(fetch)
    if payload is None:
        return {"status": status, **({"reason": reason} if reason else {}), **(fallback or {})}
    return {"status": CollectionStatus.SUCCESS, **payload}


def _per_repo(
    repos: Section,
    fetch: Callable[[dict[str, Any]], dict[str, Any]],
    fallback: dict[str, Any],
) -> Section:
    """Una lectura por repositorio, con estado propio; el estado de la sección es el peor."""
    items = []
    for repo in repos.items:
        status, reason, payload = _attempt(lambda repo=repo: fetch(repo))
        entry: dict[str, Any] = {"repository": repo["name"], "status": status}
        if reason:
            entry["reason"] = reason
        items.append({**entry, **(fallback if payload is None else payload)})
    return Section(status=worst(item["status"] for item in items), items=items)


def _composite(repos: Section, **subs: Callable[[dict[str, Any]], dict[str, Any]]) -> Section:
    """Varias lecturas por repositorio, cada una con estado propio."""
    items = []
    for repo in repos.items:
        parts = {name: sub(repo) for name, sub in subs.items()}
        status = worst(part["status"] for part in parts.values())
        items.append({"repository": repo["name"], "status": status, **parts})
    return Section(status=worst(item["status"] for item in items), items=items)


def _choice(value: str | None, allowed: tuple[str, ...]) -> str | None:
    """Valor de un conjunto conocido; cualquier otro texto se reduce a `other`."""
    if value is None:
        return None
    return value if value in allowed else "other"


def _identifier(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _IDENTIFIER.fullmatch(value) else "other"


def _instant(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _highest_permission(permissions: _Permissions | None) -> str:
    if permissions is None:
        return "unknown"
    return next((level for level in PERMISSION_LEVELS if getattr(permissions, level)), "unknown")


def collect_organization(client: GitHubClient, config: GitHubConfig) -> Section:
    def requirement() -> dict[str, Any]:
        org = _Org.model_validate(client.get("orgs", config.organization).body)
        if org.login.lower() != config.organization.lower():
            raise GitHubError(CollectionStatus.ERROR, "organización distinta de la pedida")
        if org.two_factor_requirement_enabled is None:
            return {
                "status": CollectionStatus.PERMISSION_DENIED,
                "reason": "campo visible solo para propietarios de la organización",
                "enabled": None,
            }
        return {"enabled": org.two_factor_requirement_enabled}

    def without_two_factor() -> dict[str, Any]:
        members = client.pages(
            "orgs", config.organization, "members", params={"filter": "2fa_disabled"}
        )
        if not all(isinstance(member, dict) for member in members):
            raise GitHubError(CollectionStatus.ERROR, "respuesta con forma inesperada")
        return {"count": len(members)}

    summary = {
        "two_factor_requirement": _sub(requirement, {"enabled": None}),
        "members_without_two_factor": _sub(without_two_factor, {"count": None}),
    }
    return Section(status=worst(part["status"] for part in summary.values()), summary=summary)


def collect_app_installations(client: GitHubClient, config: GitHubConfig) -> Section:
    status, reason, installations = _attempt(
        lambda: [
            _Installation.model_validate(raw)
            for raw in client.pages(
                "orgs", config.organization, "installations", wrapper="installations"
            )
        ]
    )
    if installations is None:
        return _failed(status, reason)
    return Section(
        status=CollectionStatus.SUCCESS,
        items=[
            {
                "app_slug": i.app_slug if _APP_SLUG.fullmatch(i.app_slug or "") else "other",
                "repository_selection": _choice(i.repository_selection, ("all", "selected")),
                "permissions": {
                    name: _choice(level, ("read", "write", "admin"))
                    for name, level in sorted((i.permissions or {}).items())
                    if _IDENTIFIER.fullmatch(name)
                },
                "suspended": i.suspended_at is not None,
            }
            for i in installations
        ],
    )


def _secret_scanning_status(repo: _Repo) -> str | None:
    feature = repo.security_and_analysis.secret_scanning if repo.security_and_analysis else None
    return _choice(feature.status if feature else None, ("enabled", "disabled"))


def collect_repositories(client: GitHubClient, config: GitHubRun) -> Section:
    status, reason, repos = _attempt(
        lambda: [
            _Repo.model_validate(raw)
            for raw in client.pages("orgs", config.organization, "repos", params={"type": "all"})
        ]
    )
    if repos is None:
        return _failed(status, reason)
    seen = {repo.name.lower() for repo in repos}
    return Section(
        status=CollectionStatus.SUCCESS,
        items=[
            {
                "name": repo.name,
                "visibility": _choice(repo.visibility, ("public", "private", "internal")),
                "archived": repo.archived,
                "default_branch": repo.default_branch,
                "admin": repo.permissions.admin if repo.permissions else None,
                "security_and_analysis_visible": repo.security_and_analysis is not None,
                "secret_scanning": _secret_scanning_status(repo),
            }
            for repo in repos
        ],
        summary={
            "missing_from_token": sorted(
                name for name in config.inventory_repos if name.lower() not in seen
            ),
            "not_in_inventory": sorted(seen - {name.lower() for name in config.inventory_repos}),
        },
    )


def collect_secret_scanning(repos: Section) -> Section:
    """Del campo `security_and_analysis` de cada repositorio; no hay llamadas propias."""

    def state(repo: dict[str, Any]) -> dict[str, Any]:
        if not repo["security_and_analysis_visible"]:
            return {
                "status": CollectionStatus.PERMISSION_DENIED,
                "reason": "security_and_analysis solo es visible con administración del repo",
                "state": "unknown",
            }
        match repo["secret_scanning"]:
            case "enabled" | "disabled" as value:
                return {"status": CollectionStatus.SUCCESS, "state": value}
            case None:
                return {
                    "status": CollectionStatus.NOT_SUPPORTED,
                    "reason": "secret scanning no figura en security_and_analysis",
                    "state": "unknown",
                }
            case _:
                return {
                    "status": CollectionStatus.ERROR,
                    "reason": "valor inesperado de secret scanning",
                    "state": "unknown",
                }

    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    items = [{"repository": repo["name"], **state(repo)} for repo in repos.items]
    return Section(status=worst(item["status"] for item in items), items=items)


def _ruleset_item(
    ref: _RulesetRef, status: CollectionStatus, reason: str | None, detail: _RulesetDetail | None
) -> dict[str, Any]:
    actors = detail.bypass_actors if detail else None
    item: dict[str, Any] = {
        "id": ref.id,
        "source_type": _choice(ref.source_type, ("Repository", "Organization", "Enterprise")),
        "enforcement": _choice(ref.enforcement, ("active", "evaluate", "disabled")),
        "target": _choice(ref.target, ("branch", "tag", "push")),
        "status": status,
        "bypass_actors": None
        if actors is None
        else [
            {
                "actor_type": _choice(
                    actor.actor_type,
                    ("RepositoryRole", "OrganizationAdmin", "Team", "Integration", "DeployKey"),
                ),
                "bypass_mode": _choice(actor.bypass_mode, ("always", "pull_request", "exempt")),
            }
            for actor in actors
        ],
    }
    if reason:
        item["reason"] = reason
    return item


def _rulesets(client: GitHubClient, org: str, repo: dict[str, Any]) -> dict[str, Any]:
    """Inventario con rulesets heredados (incluye `evaluate` y `disabled`) y su detalle."""
    parents = {"includes_parents": "true"}
    refs = [
        _RulesetRef.model_validate(raw)
        for raw in client.pages("repos", org, repo["name"], "rulesets", params=parents)
    ]
    items = []
    for ref in refs:
        status, reason, detail = _attempt(
            lambda ref=ref: _RulesetDetail.model_validate(
                client.get("repos", org, repo["name"], "rulesets", ref.id, params=parents).body
            )
        )
        items.append(_ruleset_item(ref, status, reason, detail))
    return {"items": items, "status": worst(item["status"] for item in items)}


def _effective_rules(client: GitHubClient, org: str, repo: dict[str, Any]) -> dict[str, Any]:
    """Reglas efectivas de la rama por defecto (GitHub solo devuelve rulesets `active`)."""
    branch = repo["default_branch"]
    if branch is None:
        raise GitHubError(CollectionStatus.ERROR, "repositorio sin rama por defecto")
    rules = [
        _EffectiveRule.model_validate(raw)
        for raw in client.pages("repos", org, repo["name"], "rules", "branches", branch)
    ]
    return {
        "branch": branch,
        "rules": [
            {
                "type": _identifier(rule.type),
                "ruleset_id": rule.ruleset_id,
                "ruleset_source_type": _choice(
                    rule.ruleset_source_type, ("Repository", "Organization", "Enterprise")
                ),
            }
            for rule in rules
        ],
    }


def _has_allowances(allowances: _Allowances | None) -> bool:
    return allowances is not None and bool(allowances.users or allowances.teams or allowances.apps)


def _classic_protection(client: GitHubClient, org: str, repo: dict[str, Any]) -> dict[str, Any]:
    branch = repo["default_branch"]
    if branch is None:
        raise GitHubError(CollectionStatus.ERROR, "repositorio sin rama por defecto")
    try:
        reply = client.get("repos", org, repo["name"], "branches", branch, "protection")
    except GitHubError as exc:
        if exc.finding is Finding.BRANCH_NOT_PROTECTED:
            return {"state": "unprotected"}
        raise
    protection = _ClassicProtection.model_validate(reply.body)
    reviews = protection.required_pull_request_reviews

    def flag(setting: _Enabled | None) -> bool | None:
        return setting.enabled if setting else None

    return {
        "state": "protected",
        "requires_pull_request": reviews is not None,
        "bypass_pull_request_allowances": reviews is not None
        and _has_allowances(reviews.bypass_pull_request_allowances),
        "enforce_admins": flag(protection.enforce_admins),
        "allow_force_pushes": flag(protection.allow_force_pushes),
        "allow_deletions": flag(protection.allow_deletions),
    }


def collect_branch_rules(client: GitHubClient, config: GitHubConfig, repos: Section) -> Section:
    org = config.organization
    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    return _composite(
        repos,
        rulesets=lambda repo: _sub(lambda: _rulesets(client, org, repo), {"items": []}),
        effective_rules=lambda repo: _sub(
            lambda: _effective_rules(client, org, repo), {"rules": []}
        ),
        classic=lambda repo: _sub(
            lambda: _classic_protection(client, org, repo), {"state": "unknown"}
        ),
    )


def _dependabot(client: GitHubClient, org: str, repo: dict[str, Any]) -> dict[str, Any]:
    """`enabled` solo con el 204 de `/vulnerability-alerts`; `disabled` solo con el 403 explícito.

    Un 404 genérico en ambos es no demostrable (`PERMISSION_DENIED`).
    """
    try:
        alerts = client.get("repos", org, repo["name"], "vulnerability-alerts")
    except GitHubError as exc:
        if exc.http_status != 404:
            raise
    else:
        if alerts.http_status == 204:
            return {"state": "enabled"}
        raise GitHubError(CollectionStatus.ERROR, "respuesta inesperada")
    try:
        client.get("repos", org, repo["name"], "dependabot", "alerts", params={"per_page": 1})
    except GitHubError as exc:
        if exc.finding is Finding.DEPENDABOT_ALERTS_DISABLED:
            return {"state": "disabled"}
        raise
    raise GitHubError(CollectionStatus.ERROR, "respuestas contradictorias sobre Dependabot")


def collect_dependabot(client: GitHubClient, config: GitHubConfig, repos: Section) -> Section:
    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    return _per_repo(
        repos, lambda repo: _dependabot(client, config.organization, repo), {"state": "unknown"}
    )


def collect_collaborators(client: GitHubClient, config: GitHubConfig, repos: Section) -> Section:
    """Solo recuentos por permiso más alto de los colaboradores externos; sin logins."""

    def fetch(repo: dict[str, Any]) -> dict[str, Any]:
        collaborators = [
            _Collaborator.model_validate(raw)
            for raw in client.pages(
                "repos",
                config.organization,
                repo["name"],
                "collaborators",
                params={"affiliation": "outside"},
            )
        ]
        by_permission = {level: 0 for level in (*PERMISSION_LEVELS, "unknown")}
        for collaborator in collaborators:
            by_permission[_highest_permission(collaborator.permissions)] += 1
        return {"outside_total": len(collaborators), "by_permission": by_permission}

    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    return _per_repo(repos, fetch, {"outside_total": None, "by_permission": None})


def _environment_item(environment: _Environment) -> dict[str, Any]:
    rules = environment.protection_rules or []
    return {
        "name": _CONTROL.sub(" ", environment.name)[:NAME_LIMIT],
        "protection_rule_types": sorted({_identifier(rule.type) or "other" for rule in rules}),
        "required_reviewers": sum(
            len(rule.reviewers or []) for rule in rules if rule.type == "required_reviewers"
        ),
        "restricts_branches": environment.deployment_branch_policy is not None,
    }


def collect_environments(client: GitHubClient, config: GitHubConfig, repos: Section) -> Section:
    def fetch(repo: dict[str, Any]) -> dict[str, Any]:
        environments = [
            _Environment.model_validate(raw)
            for raw in client.pages(
                "repos", config.organization, repo["name"], "environments", wrapper="environments"
            )
        ]
        return {"environments": [_environment_item(environment) for environment in environments]}

    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    return _per_repo(repos, fetch, {"environments": None})


def collect_actions(client: GitHubClient, config: GitHubConfig, repos: Section) -> Section:
    org = config.organization

    def permissions(repo: dict[str, Any]) -> dict[str, Any]:
        body = client.get("repos", org, repo["name"], "actions", "permissions").body
        actions = _ActionsPermissions.model_validate(body)
        return {
            "enabled": actions.enabled,
            "allowed_actions": _choice(actions.allowed_actions, ("all", "local_only", "selected")),
        }

    def workflow(repo: dict[str, Any]) -> dict[str, Any]:
        body = client.get("repos", org, repo["name"], "actions", "permissions", "workflow").body
        defaults = _WorkflowPermissions.model_validate(body)
        return {
            "default_workflow_permissions": _choice(
                defaults.default_workflow_permissions, ("read", "write")
            ),
            "can_approve_pull_request_reviews": defaults.can_approve_pull_request_reviews,
        }

    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    return _composite(
        repos,
        permissions=lambda repo: _sub(
            lambda: permissions(repo), {"enabled": None, "allowed_actions": None}
        ),
        workflow=lambda repo: _sub(
            lambda: workflow(repo),
            {"default_workflow_permissions": None, "can_approve_pull_request_reviews": None},
        ),
    )


def collect_deploy_keys(client: GitHubClient, config: GitHubConfig, repos: Section) -> Section:
    """Solo `read_only` y fecha de alta de cada clave; ni título ni la clave."""

    def fetch(repo: dict[str, Any]) -> dict[str, Any]:
        keys = [
            _DeployKey.model_validate(raw)
            for raw in client.pages("repos", config.organization, repo["name"], "keys")
        ]
        return {
            "count": len(keys),
            "writable_count": sum(not key.read_only for key in keys),
            "keys": [
                {"read_only": key.read_only, "created_at": _instant(key.created_at)} for key in keys
            ],
        }

    if blocked := _dependent_on(repos, "repositories"):
        return blocked
    return _per_repo(repos, fetch, {"count": None, "writable_count": None, "keys": None})
