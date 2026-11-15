"""Check `cloudflare.access_idp_restricted`: las aplicaciones de Access que publican hosts de
túneles solo admiten un proveedor de identidad de directorio de organización.

Es una restricción técnica de métodos de acceso. `PASS` no demuestra que el tenant sea el de la
pyme ni que el proveedor exija doble factor (eso lo evidencian `workspace` y `m365`), y la
vinculación a una medida la decide una persona. Lo claramente abierto (código por correo,
proveedor de consumo, `bypass`) es `FAIL`; todo lo que no se puede resolver es `UNKNOWN`.

Evaluación conservadora por rama: `include` es un OR de ramas; una rama admite todo el conjunto
base salvo que esté ligada a un proveedor (`login_method` o selector con proveedor) o sea un grupo
resoluble. `require` es un AND que interseca; `exclude` solo resta un `login_method`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ens_evidence.connectors.cloudflare.checks import (
    _GROUP,
    _IDENTITY_SELECTORS,
    _OTHER_KNOWN_SELECTORS,
    ACCESS_IDP_RESTRICTED,
    Outcome,
    ScopedApp,
    SectionData,
    _read,
    _unrecovered,
    _why,
    published_scope,
)
from ens_evidence.connectors.cloudflare.sections import _IDP_BOUND_KINDS
from ens_evidence.models.contracts import CheckResult, CollectionResult
from ens_evidence.models.enums import CheckStatus, CollectionStatus

PERMISSION_DENIED = "permission_denied"
NO_IDENTITY_PROVIDERS = "no_identity_providers"
OTP_OR_CONSUMER_IDP = "otp_or_consumer_idp"
IDP_REVIEW = "idp_review"
NON_IDENTITY = "non_identity"
BYPASS = "bypass"
SCOPE_INCOMPLETE = "scope_incomplete"
STALE_EVIDENCE = "stale_evidence"

TOKEN_PERMISSION = "Access: Organizations, Identity Providers, and Groups Read"
_CONSUMER_TYPES = frozenset({"onetimepin", "google", "facebook", "linkedin"})
_DIRECTORY_TYPES = frozenset({"google-apps", "azureAD"})
_LOGIN_METHOD = "login_method"
_KNOWN_KINDS = _IDENTITY_SELECTORS | _OTHER_KNOWN_SELECTORS | {_LOGIN_METHOD}
_DECISIONS = frozenset({"allow", "deny", "non_identity", "bypass"})
_STALE = "la evidencia es anterior a esta comprobación: recoge de nuevo con ens-evidence collect"

Selector = dict[str, Any]
Findings = list[tuple[str, str]]  # (reason_code, texto)


class _Unresolved(Exception):
    def __init__(self, detail: str, code: str = IDP_REVIEW):
        super().__init__(detail)
        self.detail = detail
        self.code = code


@dataclass(frozen=True)
class _Account:
    types: dict[str, str]  # id -> tipo
    names: dict[str, str]
    groups: dict[str, dict[str, Any]]

    def label(self, idp_id: str) -> str:
        name = self.names.get(idp_id) or idp_id
        return f"{name} ({self.types[idp_id]})" if self.types[idp_id] else name


def access_idp_restricted(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    def build(subject: str | None, outcome: Outcome) -> CheckResult:
        return CheckResult(
            check_id=ACCESS_IDP_RESTRICTED,
            asset_id=result.asset_id,
            status=outcome.status,
            evaluated_at=evaluated_at,
            evidence_ids=[evidence_id],
            reason=outcome.reason,
            subject=subject,
            collection_status=outcome.collection,
            reason_code=outcome.code,
        )

    if result.collection_status is not CollectionStatus.SUCCESS:
        detail = result.data.get("error") or result.collection_status.value
        return [build(None, _unrecovered(result.collection_status, f"recogida fallida: {detail}"))]
    sections = result.data.get("sections", {})
    applications = _read(sections, "access_applications")
    if applications.status is not CollectionStatus.SUCCESS:
        reason = f"aplicaciones Access no recogidas: {_why(applications)}"
        return [build(None, _unrecovered(applications.status, reason))]
    scope = published_scope(result)
    results = []
    if scope.incomplete:
        results.append(build(None, _unknown(SCOPE_INCOMPLETE, _scope_reason(scope.incomplete))))
    account = _account(sections)
    if isinstance(account, Outcome):
        return [*results, build(None, account)]
    if not scope.apps:
        return results
    policies = _read(sections, "access_policies")
    items = {item["app_id"]: item for item in policies.items}
    for scoped in scope.apps:
        results.append(build(scoped.app["id"], _evaluate(scoped, items, policies, account)))
    return results


def _scope_reason(reasons: list[str]) -> str:
    return (
        "no se puede fijar qué aplicaciones publican hosts de túneles ("
        + "; ".join(reasons)
        + "); solo se evalúan las aplicaciones que seguro los publican"
    )


def _unknown(code: str, reason: str) -> Outcome:
    return Outcome(CheckStatus.UNKNOWN, reason, code=code)


def _account(sections: dict[str, Any]) -> _Account | Outcome:
    if not isinstance(sections.get("identity_providers"), dict):
        return _unknown(STALE_EVIDENCE, f"sin sección identity_providers: {_STALE}")
    providers = _read(sections, "identity_providers")
    if providers.status is not CollectionStatus.SUCCESS:
        reason = f"proveedores de identidad no recogidos: {_why(providers)}"
        if providers.status is CollectionStatus.PERMISSION_DENIED:
            reason += f"; el token necesita el permiso «{TOKEN_PERMISSION}»"
        return _unrecovered(providers.status, reason)
    if not providers.items:
        return _unknown(NO_IDENTITY_PROVIDERS, "la cuenta no tiene proveedores de identidad")
    groups = _read(sections, "access_groups")
    return _Account(
        {p["id"]: p.get("type") or "" for p in providers.items},
        {p["id"]: p.get("name") or "" for p in providers.items},
        {g["id"]: g for g in groups.items},
    )


def _evaluate(
    scoped: ScopedApp,
    items: dict[str, dict[str, Any]],
    policies: SectionData,
    account: _Account,
) -> Outcome:
    app = scoped.app
    name = app.get("name") or app["id"]
    if "allowed_idps_form" not in app:
        return _unknown(STALE_EVIDENCE, f"aplicación «{name}»: {_STALE}")
    try:
        base = _base(app, account)
        item = _policy_item(items, policies, app["id"])
        return _outcome(name, scoped.uncertain, base, item["policies"], account)
    except _Unresolved as exc:
        return _unknown(exc.code, f"aplicación «{name}»: {exc.detail}")
    except _Uncollected as exc:
        return _unrecovered(exc.status, f"aplicación «{name}»: {exc.detail}")


class _Uncollected(Exception):
    def __init__(self, status: CollectionStatus, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _policy_item(
    items: dict[str, dict[str, Any]], section: SectionData, app_id: str
) -> dict[str, Any]:
    item = items.get(app_id)
    if item is None:
        if section.status is not CollectionStatus.SUCCESS:
            raise _Uncollected(section.status, f"sus políticas no se recogieron: {_why(section)}")
        raise _Unresolved("no consta ninguna política recogida")
    status = CollectionStatus(item["status"])
    if status is not CollectionStatus.SUCCESS:
        raise _Uncollected(
            status, f"sus políticas no se recogieron: {item.get('reason') or status.value}"
        )
    return item


def _base(app: dict[str, Any], account: _Account) -> frozenset[str]:
    form = app["allowed_idps_form"]
    if form == "malformed":
        raise _Unresolved("allowed_idps con una forma que no se sabe interpretar")
    if form != "list":
        raise _Unresolved(
            "la respuesta de Cloudflare no indica qué proveedores admite la aplicación "
            "(allowed_idps); revísalo en la consola"
        )
    if not app["allowed_idps"]:
        return frozenset(account.types)
    unknown = [i for i in app["allowed_idps"] if i not in account.types]
    if unknown:
        raise _Unresolved("allowed_idps referencia un proveedor que no consta en la cuenta")
    return frozenset(app["allowed_idps"])


def _selectors(rule: dict[str, Any], key: str) -> list[Selector]:
    selectors = rule.get(f"{key}_selectors")
    if not isinstance(selectors, list):
        raise _Unresolved(_STALE, STALE_EVIDENCE)
    return selectors


def _bound_idp(selector: Selector, base: frozenset[str], account: _Account) -> frozenset[str]:
    idp = selector.get("idp")
    if idp is None:
        raise _Unresolved(f"selector {selector['kind']} sin proveedor identificable")
    if idp not in account.types:
        raise _Unresolved("un selector referencia un proveedor que no consta en la cuenta")
    return base & {idp}


def _is_bound(selector: Selector) -> bool:
    return selector["kind"] == _LOGIN_METHOD or selector["kind"] in _IDP_BOUND_KINDS


def _check_kind(selector: Selector) -> None:
    if selector.get("kind") not in _KNOWN_KINDS:
        raise _Unresolved("tiene selectores de política desconocidos")


def _admitted(
    rule: dict[str, Any], base: frozenset[str], account: _Account, trail: tuple[str, ...] = ()
) -> frozenset[str]:
    """Proveedores que admite una política o un grupo: OR de include, AND de require, - exclude."""
    admitted: frozenset[str] = frozenset()
    for selector in _selectors(rule, "include"):
        admitted |= _branch(selector, base, account, trail)
    for selector in _selectors(rule, "require"):
        admitted &= _branch(selector, base, account, trail)
    for selector in _selectors(rule, "exclude"):
        _check_kind(selector)
        if selector["kind"] == _LOGIN_METHOD:
            admitted -= _bound_idp(selector, base, account)
    return admitted


def _branch(
    selector: Selector, base: frozenset[str], account: _Account, trail: tuple[str, ...]
) -> frozenset[str]:
    _check_kind(selector)
    if selector["kind"] == _GROUP:
        return _group(selector.get("group"), base, account, trail)
    if _is_bound(selector):
        return _bound_idp(selector, base, account)
    return base


def _group(
    group_id: str | None, base: frozenset[str], account: _Account, trail: tuple[str, ...]
) -> frozenset[str]:
    if group_id in trail:
        raise _Unresolved("los grupos de Access se referencian entre sí en ciclo")
    if group_id is None or group_id not in account.groups:
        raise _Unresolved("referencia un grupo de Access que no consta en la evidencia")
    return _admitted(account.groups[group_id], base, account, (*trail, group_id))


def _denied(policy: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        s["idp"]
        for s in _selectors(policy, "include")
        if s["kind"] == _LOGIN_METHOD and s.get("idp")
    )


def _outcome(
    name: str,
    uncertain: bool,
    base: frozenset[str],
    policies: list[dict[str, Any]],
    account: _Account,
) -> Outcome:
    fails: Findings = []
    unknowns: Findings = []
    admitted: frozenset[str] = frozenset()
    denied: frozenset[str] = frozenset()
    allows = 0
    for policy in policies:
        decision = policy.get("decision")
        if decision == "bypass":
            fails.append((BYPASS, "tiene una política bypass: deja entrar sin identidad"))
        elif decision == "non_identity":
            unknowns.append(
                (
                    NON_IDENTITY,
                    "hay acceso por token de servicio; una persona confirma que es solo para "
                    "máquinas",
                )
            )
        elif decision not in _DECISIONS:
            unknowns.append((IDP_REVIEW, "tiene una política con decisión desconocida"))
        elif decision == "deny":
            try:
                denied |= _denied(policy)
            except _Unresolved as exc:
                unknowns.append((exc.code, exc.detail))
        else:
            allows += 1
            try:
                admitted |= _admitted(policy, base, account)
            except _Unresolved as exc:
                unknowns.append((exc.code, exc.detail))
    consumer = sorted(i for i in admitted if account.types[i] in _CONSUMER_TYPES)
    if consumer:
        methods = ", ".join(account.label(i) for i in consumer)
        if set(consumer) <= denied:
            unknowns.append((IDP_REVIEW, f"admite {methods} pero una política deny lo menciona"))
        else:
            fails.append(
                (OTP_OR_CONSUMER_IDP, f"admite métodos que no son de directorio: {methods}")
            )
    if fails:
        return _report(CheckStatus.FAIL, name, fails)
    if unknowns:
        return _report(CheckStatus.UNKNOWN, name, unknowns)
    if uncertain:
        text = "algún destino de la aplicación no se sabe interpretar"
        return _report(CheckStatus.UNKNOWN, name, [(SCOPE_INCOMPLETE, text)])
    return _classify(name, allows, admitted, account)


def _classify(name: str, allows: int, admitted: frozenset[str], account: _Account) -> Outcome:
    if not allows:
        return _report(CheckStatus.UNKNOWN, name, [(IDP_REVIEW, "no tiene ninguna política allow")])
    if not admitted:
        return _report(
            CheckStatus.UNKNOWN,
            name,
            [(IDP_REVIEW, "sus políticas allow no admiten ningún proveedor de identidad")],
        )
    others = sorted(i for i in admitted if account.types[i] not in _DIRECTORY_TYPES)
    if others or len(admitted) > 1:
        listed = ", ".join(account.label(i) for i in sorted(admitted))
        text = f"admite {listed}: una persona revisa si son los proveedores de la pyme"
        return _report(CheckStatus.UNKNOWN, name, [(IDP_REVIEW, text)])
    (only,) = admitted
    return Outcome(
        CheckStatus.PASS,
        f"la aplicación «{name}» solo admite el proveedor de directorio {account.label(only)}; "
        "no prueba que el tenant sea el de la pyme ni que exija doble factor "
        "(ver workspace y m365)",
    )


def _report(status: CheckStatus, name: str, findings: Findings) -> Outcome:
    text = "; ".join(dict.fromkeys(text for _, text in findings))
    return Outcome(status, f"la aplicación «{name}» {text}", code=findings[0][0])


__all__ = ["access_idp_restricted"]
