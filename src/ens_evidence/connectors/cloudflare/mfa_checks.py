"""Check `cloudflare.ui_access_requires_mfa`: toda política que deja entrar a la aplicación de
Access que protege la UI exige un segundo factor.

NO VERIFICADO contra una cuenta real de Cloudflare: la forma con que la API expresa la MFA
(selector `require` `auth_method` con valor `mfa`, y `mfa_config.mfa_disabled == false` en la
política o la aplicación) se ha tomado de la documentación y se prueba solo con fixtures.

`PASS` solo si hay al menos una aplicación que cubre el hostname de la UI entero y cada política
`allow` que la alcanza exige MFA. `bypass` o una `allow` sin MFA (incluido `everyone` sin
`require`) es `FAIL` y nombra la política; un selector `auth_method` ilegible o distinto de `mfa`
cuenta como que no exige MFA (`FAIL`). Lo que no se puede resolver es `UNKNOWN`; la ausencia de
evidencia nunca es `PASS`. El token de aplicación de Access no lleva un claim de MFA, así que
esta política es la única comprobación posible. No demuestra que los usuarios hayan enrolado el
factor (eso lo evidencian `workspace` y `m365`).
"""

from datetime import datetime
from typing import Any

from ens_evidence.connectors.cloudflare.checks import (
    _GROUP,
    _IGNORED_APP_TYPES,
    UI_ACCESS_REQUIRES_MFA,
    AccessGroups,
    Host,
    Outcome,
    PathPattern,
    SectionData,
    Unresolvable,
    _is_other_host,
    _read,
    _unrecovered,
    _why,
    host_covers,
    parse_destination,
    parse_host,
    path_covers,
)
from ens_evidence.models.contracts import CheckResult, CollectionResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

HOSTNAME_NOT_CONFIGURED = "ui_hostname_not_configured"
APP_NOT_FOUND = "ui_app_not_found"
APP_PARTIAL = "ui_app_partial"
APP_UNRESOLVED = "ui_app_unresolved"
POLICY_UNRESOLVED = "policy_unresolved"
NO_ALLOW_POLICY = "no_allow_policy"
NON_IDENTITY = "non_identity"
BYPASS = "bypass"
MFA_MISSING = "mfa_missing"
STALE_EVIDENCE = "stale_evidence"

MFA_METHOD = "mfa"
_STALE = "la evidencia es anterior a esta comprobación: recoge de nuevo con ens-evidence collect"
_ROOT = PathPattern("/", True)

Findings = list[tuple[str, str]]  # (reason_code, texto)


class _Stale(Exception):
    pass


def ui_access_requires_mfa(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    outcome = _outcome(result)
    return [
        CheckResult(
            check_id=UI_ACCESS_REQUIRES_MFA,
            asset_id=result.asset_id,
            status=outcome.status,
            evaluated_at=evaluated_at,
            evidence_ids=[evidence_id],
            reason=outcome.reason,
            collection_status=outcome.collection,
            reason_code=outcome.code,
        )
    ]


def _unknown(code: str, reason: str) -> Outcome:
    return Outcome(CheckStatus.UNKNOWN, reason, code=code)


def _outcome(result: CollectionResult) -> Outcome:
    if result.collection_status is not CollectionStatus.SUCCESS:
        detail = result.data.get("error") or result.collection_status.value
        return _unrecovered(result.collection_status, f"recogida fallida: {detail}")
    hostname = result.data.get("ui_hostname")
    if not isinstance(hostname, str) or not hostname:
        return _unknown(
            HOSTNAME_NOT_CONFIGURED,
            "el conector Cloudflare no tiene ui_hostname: no se sabe qué aplicación de Access "
            "protege la UI",
        )
    sections = result.data.get("sections", {})
    applications = _read(sections, "access_applications")
    if applications.status is not CollectionStatus.SUCCESS:
        reason = f"aplicaciones Access no recogidas: {_why(applications)}"
        return _unrecovered(applications.status, reason)
    target = parse_host(hostname)
    if target is None:
        return _unknown(APP_UNRESOLVED, f"ui_hostname «{hostname}» no es un hostname válido")
    covering, partial, uncertain = _find_apps(applications.items, target)
    if uncertain:
        return _unknown(
            APP_UNRESOLVED, "algún destino de Access no se sabe interpretar: " + uncertain
        )
    if not covering:
        if partial:
            names = ", ".join(partial)
            return _unknown(
                APP_PARTIAL, f"solo cubren parte de {hostname} (por ruta) las aplicaciones: {names}"
            )
        return _unknown(
            APP_NOT_FOUND, f"ninguna aplicación de Access cubre {hostname}; revísalo en la consola"
        )
    policies = _read(sections, "access_policies")
    groups = _groups(_read(sections, "access_groups"))
    fails: Findings = []
    unknowns: Findings = []
    for app in covering:
        try:
            found = _evaluate_app(app, policies, groups)
        except Unresolvable as exc:
            return _unrecovered(exc.collection, f"aplicación «{_label(app)}»: {exc.detail}")
        except _Stale:
            return _unknown(STALE_EVIDENCE, f"aplicación «{_label(app)}»: {_STALE}")
        fails += [(code, f"aplicación «{_label(app)}»: {text}") for code, text in found[0]]
        unknowns += [(code, f"aplicación «{_label(app)}»: {text}") for code, text in found[1]]
    if fails:
        return _report(CheckStatus.FAIL, fails)
    if unknowns:
        return _report(CheckStatus.UNKNOWN, unknowns)
    return Outcome(
        CheckStatus.PASS,
        f"toda política allow que alcanza {hostname} exige MFA (política de Access; no prueba "
        "que los usuarios hayan enrolado el factor)",
    )


def _label(app: dict[str, Any]) -> str:
    return app.get("name") or app["id"]


def _report(status: CheckStatus, findings: Findings) -> Outcome:
    text = "; ".join(dict.fromkeys(text for _, text in findings))
    return Outcome(status, text, code=findings[0][0])


def _find_apps(
    applications: list[dict[str, Any]], target: Host
) -> tuple[list[dict[str, Any]], list[str], str]:
    """Aplicaciones que cubren el hostname entero, las que cubren parte y los destinos ilegibles."""
    covering: list[dict[str, Any]] = []
    partial: list[str] = []
    unreadable: list[str] = []
    for app in applications:
        if app.get("type") in _IGNORED_APP_TYPES:
            continue
        for uri in app.get("public_destinations", []):
            dest = parse_destination(uri)
            if dest.host is None:
                if not _is_other_host(target.name, dest.raw_host):
                    unreadable.append(_label(app))
            elif host_covers(dest.host, target):
                if dest.path is not None and path_covers(dest.path, _ROOT):
                    covering.append(app)
                    break
                partial.append(_label(app))
    return covering, partial, ", ".join(dict.fromkeys(unreadable))


def _groups(section: SectionData) -> AccessGroups:
    if section.status is not CollectionStatus.SUCCESS:
        detail = f"grupos de Access no recogidos: {_why(section)}"
        return AccessGroups({}, Unresolvable(detail, section.status))
    return AccessGroups({g["id"]: g for g in section.items})


def _evaluate_app(
    app: dict[str, Any], policies: SectionData, groups: AccessGroups
) -> tuple[Findings, Findings]:
    if "mfa_required" not in app:
        raise _Stale
    item = next((i for i in policies.items if i["app_id"] == app["id"]), None)
    if item is None:
        if policies.status is not CollectionStatus.SUCCESS:
            raise Unresolvable(f"sus políticas no se recogieron: {_why(policies)}", policies.status)
        return [], [(POLICY_UNRESOLVED, "no consta ninguna política recogida")]
    status = CollectionStatus(item["status"])
    if status is not CollectionStatus.SUCCESS:
        raise Unresolvable(
            f"sus políticas no se recogieron: {item.get('reason') or status.value}", status
        )
    fails: Findings = []
    unknowns: Findings = []
    allows = 0
    for policy in item["policies"]:
        if "mfa_required" not in policy or "require_auth_methods" not in policy:
            raise _Stale
        name = policy.get("name") or policy.get("id") or "sin nombre"
        decision = policy.get("decision")
        if decision == "bypass":
            fails.append((BYPASS, f"la política «{name}» es bypass: deja entrar sin MFA"))
        elif decision == "non_identity":
            unknowns.append(
                (
                    NON_IDENTITY,
                    f"la política «{name}» da acceso por token de servicio; una persona confirma "
                    "que es solo para máquinas",
                )
            )
        elif decision == "allow":
            allows += 1
            _evaluate_allow(policy, name, bool(app["mfa_required"]), groups, fails, unknowns)
        elif decision != "deny":
            unknowns.append((POLICY_UNRESOLVED, f"la política «{name}» tiene decisión desconocida"))
    if not allows and not fails and not unknowns:
        unknowns.append((NO_ALLOW_POLICY, "no tiene ninguna política allow que se pueda evaluar"))
    return fails, unknowns


def _evaluate_allow(
    policy: dict[str, Any],
    name: str,
    app_mfa: bool,
    groups: AccessGroups,
    fails: Findings,
    unknowns: Findings,
) -> None:
    if app_mfa or policy["mfa_required"]:
        return
    try:
        if _requires_mfa(policy, groups, ()):
            return
    except Unresolvable as exc:
        unknowns.append((POLICY_UNRESOLVED, f"la política «{name}»: {exc.detail}"))
        return
    fails.append((MFA_MISSING, f"la política allow «{name}» no exige MFA"))


def _requires_mfa(rule: dict[str, Any], groups: AccessGroups, trail: tuple[str, ...]) -> bool:
    """`require` es un AND: basta un `auth_method` mfa, o un grupo `require` que lo exija."""
    if MFA_METHOD in rule["require_auth_methods"]:
        return True
    if _GROUP not in rule.get("require", []):
        return False
    for group_id in rule.get("require_groups") or [None]:
        group = groups.get(group_id, trail)
        if "require_auth_methods" not in group:
            raise Unresolvable(_STALE)
        if _requires_mfa(group, groups, (*trail, group_id or "")):
            return True
    return False


__all__ = ["ui_access_requires_mfa"]
