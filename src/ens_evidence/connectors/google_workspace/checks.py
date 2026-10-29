"""Checks de Google Workspace sobre la evidencia recogida.

`PASS` solo con evidencia positiva demostrada; `FAIL` solo con evidencia negativa explícita
(flags booleanos de Google); todo lo demás es `UNKNOWN` con motivo y la traducción del
ADR 0002 cuando la sección o el ítem no se recogió con éxito. Con la recogida global sin
éxito ningún check da `PASS`. `CheckResult.subject` es el correo del usuario.

Los checks de 2SV afirman solo el flag de Google (`isEnrolledIn2Sv`, `isEnforcedIn2Sv`):
no prueban MFA resistente a phishing ni cubren un IdP externo.

Estado del usuario: solo se evalúa 2SV de usuarios con `suspended` y `archived` explícitamente
`false`; los archivados y suspendidos confirmados se excluyen sin resultado, y un estado
ausente o mal formado da `UNKNOWN`. Los administradores se evalúan en cualquier estado
(suspender no retira privilegios); el estado de suspensión de un admin lo cubre
`workspace.suspended_without_admin_role`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ens_evidence.models.contracts import CheckResult, CollectionResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus

USER_2SV = "workspace.user_2sv_enrolled"
ADMIN_2SV = "workspace.admin_2sv_enforced"
SUSPENDED_NO_ADMIN = "workspace.suspended_without_admin_role"

_FLAG_ONLY = "solo afirma el flag de Google, no MFA resistente a phishing ni un IdP externo"


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


def _unknown(reason: str) -> Outcome:
    return Outcome(CheckStatus.UNKNOWN, reason)


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


def _items(section: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [i for i in _list(_dict(section).get("items")) if isinstance(i, dict)]


def _global_failure(result: CollectionResult) -> Outcome | None:
    if result.collection_status is CollectionStatus.SUCCESS:
        return None
    detail = result.data.get("error") or result.collection_status.value
    return _unrecovered(result.collection_status, f"recogida de Workspace fallida: {detail}")


def _absent(name: str) -> Outcome:
    return _unrecovered(CollectionStatus.ERROR, f"sección {name} ausente en la evidencia")


def _users(result: CollectionResult) -> tuple[list[dict[str, Any]], Outcome | None]:
    section = _section(result, "users")
    if section is None:
        return [], _absent("users")
    status = _status(section.get("status"))
    if status is not CollectionStatus.SUCCESS:
        return [], _unrecovered(status, f"usuarios no recogidos: {_why(section)}")
    return _items(section), None


def _subject(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("id") or "usuario ilegible")


def _roles_by_user(result: CollectionResult) -> dict[str, dict[str, Any]]:
    return {str(item.get("user_id")): item for item in _items(_section(result, "admins"))}


def _roles(item: dict[str, Any] | None) -> list[Any] | None:
    """Los roles solo cuentan si su consulta fue exitosa: `None` es desconocido."""
    if item is None or _status(item.get("status")) is not CollectionStatus.SUCCESS:
        return None
    roles = item.get("roles")
    return roles if isinstance(roles, list) else None


def _flag_outcome(value: Any, yes: str, no: str, missing: str) -> Outcome:
    match value:
        case True:
            return Outcome(CheckStatus.PASS, yes)
        case False:
            return Outcome(CheckStatus.FAIL, no)
        case _:
            return _unknown(missing)


def user_2sv_enrolled(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    build = _builder(USER_2SV, result, evidence_id, evaluated_at)
    if failure := _global_failure(result):
        return [build(None, failure)]
    users, failure = _users(result)
    if failure:
        return [build(None, failure)]
    checks = []
    for user in users:
        if user.get("archived") is True or user.get("suspended") is True:
            continue
        if user.get("archived") is not False or user.get("suspended") is not False:
            outcome = _unknown("estado activo del usuario desconocido; no se evalúa su 2SV")
        else:
            outcome = _flag_outcome(
                user.get("enrolled_2sv"),
                f"Google indica que el usuario tiene 2SV activado ({_FLAG_ONLY})",
                f"Google indica que el usuario activo no tiene 2SV activado ({_FLAG_ONLY})",
                "el flag isEnrolledIn2Sv no figura o está mal formado en la evidencia",
            )
        checks.append(build(_subject(user), outcome))
    return checks


def _known_admin(user: dict[str, Any], item: dict[str, Any] | None) -> bool:
    return (
        user.get("is_admin") is True or user.get("is_delegated_admin") is True or bool(_roles(item))
    )


def _non_admin_proven(user: dict[str, Any], item: dict[str, Any] | None) -> bool:
    return (
        user.get("is_admin") is False
        and user.get("is_delegated_admin") is False
        and _roles(item) == []
    )


def admin_2sv_enforced(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    build = _builder(ADMIN_2SV, result, evidence_id, evaluated_at)
    if failure := _global_failure(result):
        return [build(None, failure)]
    users, failure = _users(result)
    if failure:
        return [build(None, failure)]
    by_user = _roles_by_user(result)
    checks = []
    undetermined = 0
    for user in users:
        item = by_user.get(str(user.get("id")))
        if _known_admin(user, item):
            outcome = _flag_outcome(
                user.get("enforced_2sv"),
                f"Google indica que se exige 2SV al administrador ({_FLAG_ONLY})",
                f"Google indica que no se exige 2SV al administrador ({_FLAG_ONLY})",
                "el flag isEnforcedIn2Sv no figura o está mal formado; Google lo oculta "
                "según el rol del administrador suplantado",
            )
            checks.append(build(_subject(user), outcome))
        elif not _non_admin_proven(user, item):
            undetermined += 1
    if undetermined or not _admins_complete(result):
        checks.append(build(None, _incomplete_discovery(result, undetermined)))
    return checks


def _admins_complete(result: CollectionResult) -> bool:
    section = _section(result, "admins")
    return (
        section is not None
        and _status(section.get("status")) is CollectionStatus.SUCCESS
        and _dict(section.get("summary")).get("complete") is True
    )


def _incomplete_discovery(result: CollectionResult, undetermined: int) -> Outcome:
    reason = (
        "descubrimiento de administradores incompleto: no consta si hay otros administradores "
        f"({undetermined} usuarios sin evidencia negativa completa)"
    )
    section = _section(result, "admins")
    if section is None:
        return _unrecovered(CollectionStatus.ERROR, reason + "; sección admins ausente")
    status = _status(section.get("status"))
    if status is not CollectionStatus.SUCCESS:
        return _unrecovered(status, f"{reason}: {_why(section)}")
    return _unknown(reason)


def _suspended_outcome(user: dict[str, Any], item: dict[str, Any] | None) -> Outcome:
    roles = _roles(item)
    if user.get("is_admin") is True or user.get("is_delegated_admin") is True or roles:
        return Outcome(
            CheckStatus.FAIL, "el usuario suspendido conserva privilegios de administrador"
        )
    if _non_admin_proven(user, item):
        return Outcome(
            CheckStatus.PASS,
            "el usuario suspendido no tiene roles de administrador (consulta completa de roles)",
        )
    if item is None:
        return _unknown("sin consulta de roles del usuario suspendido en la evidencia")
    status = _status(item.get("status"))
    if status is not CollectionStatus.SUCCESS:
        return _unrecovered(status, f"roles del usuario suspendido no recogidos: {_why(item)}")
    return _unknown("flags de administrador ausentes o mal formados; sin evidencia negativa")


def suspended_without_admin_role(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    build = _builder(SUSPENDED_NO_ADMIN, result, evidence_id, evaluated_at)
    if failure := _global_failure(result):
        return [build(None, failure)]
    users, failure = _users(result)
    if failure:
        return [build(None, failure)]
    by_user = _roles_by_user(result)
    checks = []
    for user in users:
        suspended = user.get("suspended")
        if suspended is False:
            continue
        if suspended is True:
            outcome = _suspended_outcome(user, by_user.get(str(user.get("id"))))
        else:
            outcome = _unknown("estado de suspensión del usuario desconocido")
        checks.append(build(_subject(user), outcome))
    return checks
