"""Checks PWD-001..003 sobre la evidencia del conector `passwords`.

Miden el segundo factor y la inactividad de las cuentas del propio gestor, y la frescura de la
instantánea para la copia. No miden la rotación ni la complejidad de las credenciales de los
sistemas: son evidencia de apoyo para op.acc.6, no conformidad. `PASS` solo con datos que lo
demuestran; sin población evaluable, fechas futuras o umbral ausente, `UNKNOWN`. Los textos citan
solo ids internos y cifras, nunca datos personales.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ens_evidence.connectors.passwords import reader
from ens_evidence.models.contracts import CheckResult, CollectionResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus

SECOND_FACTOR = "passwords.second_factor"
NO_INACTIVE = "passwords.no_inactive_accounts"
SNAPSHOT_RECENT = "passwords.snapshot_recent"

SHOWN = 5
_NOT_COLLECTED = {
    reader.MISSING: "no hay export de usuarios",
    reader.UNREADABLE: "el export de usuarios no se puede leer",
    reader.NOT_REGULAR: "el export de usuarios no es un fichero regular",
    reader.TOO_LARGE: "el export de usuarios supera el tamaño máximo",
    reader.INVALID: "el export de usuarios no es válido",
    reader.STALE: "el export de usuarios es demasiado antiguo",
    reader.FUTURE: "el export de usuarios tiene una fecha futura",
}


@dataclass(frozen=True)
class Outcome:
    status: CheckStatus
    reason: str
    collection: CollectionStatus = CollectionStatus.SUCCESS
    code: str | None = None


def _pass(reason: str) -> Outcome:
    return Outcome(CheckStatus.PASS, reason)


def _fail(reason: str) -> Outcome:
    return Outcome(CheckStatus.FAIL, reason)


def _unknown(reason: str) -> Outcome:
    return Outcome(CheckStatus.UNKNOWN, reason)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _moment(raw: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return moment if moment and moment.tzinfo else None


def _threshold(result: CollectionResult, name: str) -> int | None:
    value = _dict(result.data.get("thresholds")).get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _not_collected(result: CollectionResult) -> Outcome | None:
    """El resultado traducido si no hay export recogido; None si hay datos que evaluar."""
    status = result.collection_status
    if status is CollectionStatus.SUCCESS and isinstance(result.data.get("export"), dict):
        return None
    code = result.data.get("error")
    detail = _NOT_COLLECTED.get(code, "no se recogió el export de usuarios")
    translation = translate_collection(
        status if status is not CollectionStatus.SUCCESS else CollectionStatus.ERROR
    )
    return Outcome(translation.status, detail, CollectionStatus.ERROR, translation.reason_code)


def _population(result: CollectionResult) -> list[dict[str, Any]] | None:
    """Usuarios activos y no invitados: los únicos que se pueden evaluar."""
    users = result.data.get("users")
    if not isinstance(users, list):
        return None
    people = [_dict(u) for u in users]
    return [u for u in people if u.get("enabled") is True and u.get("invited") is False]


def _ids(users: list[dict[str, Any]]) -> str:
    names = sorted(str(u.get("id")) for u in users)
    shown = ", ".join(names[:SHOWN])
    return shown + (f" y {len(names) - SHOWN} más" if len(names) > SHOWN else "")


def evaluate(
    check_id: str,
    decide: Callable[[CollectionResult, datetime], Outcome],
    result: CollectionResult,
    evidence_id: str,
    evaluated_at: datetime,
) -> list[CheckResult]:
    outcome = _not_collected(result) or decide(result, evaluated_at.astimezone(UTC))
    return [
        CheckResult(
            check_id=check_id,
            asset_id=result.asset_id,
            status=outcome.status,
            evaluated_at=evaluated_at,
            evidence_ids=[evidence_id],
            reason=outcome.reason,
            collection_status=outcome.collection,
            reason_code=outcome.code,
        )
    ]


def _second_factor(result: CollectionResult, _: datetime) -> Outcome:
    population = _population(result)
    if population is None:
        return _unknown("la evidencia no lleva la lista de usuarios")
    if not population:
        return _unknown("no hay usuarios activos no invitados que evaluar: el gestor está vacío")
    without = [u for u in population if u.get("second_factor") is not True]
    if without:
        return _fail(
            f"{len(without)} de {len(population)} usuarios activos no tienen un segundo factor "
            f"activado: {_ids(without)}"
        )
    return _pass(
        f"los {len(population)} usuarios activos no invitados tienen un segundo factor activado"
    )


def _no_inactive_accounts(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    limit = _threshold(result, "inactive_days")
    if limit is None:
        return _unknown("umbral inactive_days ausente")
    population = _population(result)
    if population is None:
        return _unknown("la evidencia no lleva la lista de usuarios")
    if not population:
        return _unknown("no hay usuarios activos no invitados que evaluar: el gestor está vacío")
    measured = [(u, *_last_seen(u)) for u in population]
    if any(moment is None or moment > evaluated_at for _, moment, _ in measured):
        return _unknown("hay fechas de actividad ilegibles o futuras; no se puede medir")
    maximum = timedelta(days=limit)
    inactive = [u for u, moment, _ in measured if evaluated_at - moment > maximum]
    from_creation = [u for u, _, source in measured if source == "created_at"]
    basis = (
        f"; {len(from_creation)} medidas desde created_at por no tener actividad registrada"
        if from_creation
        else ""
    )
    if inactive:
        return _fail(
            f"{len(inactive)} de {len(population)} usuarios activos llevan más de {limit} días "
            f"sin actividad: {_ids(inactive)}{basis}. Detectar la inactividad no la corrige: "
            "la cuenta sigue habilitada hasta que alguien la suspenda"
        )
    return _pass(
        f"ninguno de los {len(population)} usuarios activos supera {limit} días sin actividad"
        f"{basis}. Detectar la inactividad no suspende cuentas"
    )


def _last_seen(user: dict[str, Any]) -> tuple[datetime | None, str]:
    """La última actividad o, sin ella, la fecha de alta; con el nombre del campo medido."""
    if user.get("last_active") is not None:
        return _moment(user["last_active"]), "last_active"
    return _moment(user.get("created_at")), "created_at"


def _snapshot_recent(result: CollectionResult, _: datetime) -> Outcome:
    limit = _threshold(result, "max_export_age_minutes")
    if limit is None:
        return _unknown("umbral max_export_age_minutes ausente")
    export = _dict(result.data.get("export"))
    exported_at = _moment(export.get("exported_at"))
    last_good = _moment(export.get("last_good_snapshot_at"))
    if exported_at is None:
        return _unknown("la fecha del export es ilegible")
    if export.get("snapshot_ok") is not True:
        since = f"; la última buena es de {last_good.isoformat()}" if last_good else ""
        return _fail(f"la última instantánea para la copia falló{since}")
    if last_good is None or last_good > exported_at:
        return _unknown("la fecha de la última instantánea buena es ilegible o futura")
    gap = exported_at - last_good
    if gap > timedelta(minutes=limit):
        return _fail(
            f"la instantánea buena va {gap // timedelta(minutes=1)} min por detrás del export "
            f"(máximo {limit} min)"
        )
    return _pass(
        f"la instantánea para la copia es del mismo ciclo del export "
        f"({gap // timedelta(minutes=1)} min de diferencia, máximo {limit})"
    )


def _check(check_id: str, decide: Callable[[CollectionResult, datetime], Outcome]):
    def run(result: CollectionResult, evidence_id: str, evaluated_at: datetime):
        return evaluate(check_id, decide, result, evidence_id, evaluated_at)

    return run


second_factor = _check(SECOND_FACTOR, _second_factor)
no_inactive_accounts = _check(NO_INACTIVE, _no_inactive_accounts)
snapshot_recent = _check(SNAPSHOT_RECENT, _snapshot_recent)
