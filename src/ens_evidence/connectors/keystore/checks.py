"""Checks KEY-001..006 sobre la evidencia del conector `keystore`.

`PASS` solo con evidencia que lo demuestra; `FAIL` con dato presente que no cumple; lo no
demostrable (403, listado truncado, motor sin cobertura, fechas ilegibles) es `UNKNOWN`. Un
OpenBao sellado es `FAIL` solo en KEY-001; en los demás, la sección sin leer da `ERROR`.
Los umbrales viajan en la evidencia (`data.thresholds`).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any

from ens_evidence.connectors.keystore.sections import APP_POLICY, parse_time
from ens_evidence.models.contracts import CheckResult, CollectionResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus

UNSEALED = "keystore.unsealed"
AUDIT_ENABLED = "keystore.audit_enabled"
KEYS_ROTATED = "keystore.keys_rotated"
NO_ROOT_TOKEN = "keystore.no_root_token"
APP_TOKEN_SCOPED = "keystore.app_token_scoped"
CUSTODIANS_REGISTERED = "keystore.custodians_registered"

SHOWN = 5


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


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _section(result: CollectionResult, name: str) -> tuple[dict[str, Any] | None, Outcome | None]:
    """La sección con éxito, o el resultado traducido si falta o no se recogió."""
    raw = _dict(_dict(result.data.get("sections")).get(name))
    if not raw:
        return None, _failed(CollectionStatus.ERROR, f"sección {name} ausente en la evidencia")
    try:
        status = CollectionStatus(raw.get("status"))
    except ValueError:
        status = CollectionStatus.ERROR
    if status is not CollectionStatus.SUCCESS:
        return None, _failed(status, f"sección {name} no recogida: {raw.get('reason') or status}")
    return raw, None


def _failed(collection: CollectionStatus, reason: str) -> Outcome:
    translation = translate_collection(collection)
    return Outcome(translation.status, reason, collection, translation.reason_code)


def _threshold(result: CollectionResult, name: str) -> int | Outcome:
    value = _int(_dict(result.data.get("thresholds")).get(name))
    return value if value is not None and value > 0 else _unknown(f"umbral {name} ausente")


def _names(names: list[str]) -> str:
    shown = ", ".join(sorted(names)[:SHOWN])
    return shown + (f" y {len(names) - SHOWN} más" if len(names) > SHOWN else "")


def evaluate(
    check_id: str,
    decide: Callable[[CollectionResult, datetime], Outcome],
    result: CollectionResult,
    evidence_id: str,
    evaluated_at: datetime,
) -> list[CheckResult]:
    outcome = decide(result, evaluated_at.astimezone(UTC))
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


def _unsealed(result: CollectionResult, _: datetime) -> Outcome:
    seal, problem = _section(result, "seal")
    if problem:
        return problem
    assert seal is not None
    summary = _dict(seal.get("summary"))
    if summary.get("initialized") is False:
        return _fail("OpenBao no está inicializado: no hay claves que custodiar")
    if summary.get("sealed") is True:
        threshold, shares = _int(summary.get("t")), _int(summary.get("n"))
        quorum = f"{threshold} de {shares}" if threshold and shares else "M de N"
        return _fail(f"OpenBao está sellado: hacen falta {quorum} custodios para desbloquearlo")
    if summary.get("initialized") is True and summary.get("sealed") is False:
        return _pass("OpenBao está inicializado y desellado")
    return _unknown("seal-status no permite saber si está desellado")


def _audit_enabled(result: CollectionResult, _: datetime) -> Outcome:
    audit, problem = _section(result, "audit")
    if problem:
        return problem
    assert audit is not None
    files = [d for d in _list(audit.get("items")) if _dict(d).get("type") == "file"]
    if files:
        return _pass(f"{len(files)} dispositivo(s) de auditoría `file` activo(s)")
    return _fail("no hay ningún dispositivo de auditoría `file` activo")


def _keys_rotated(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    limit = _threshold(result, "max_key_age_days")
    if isinstance(limit, Outcome):
        return limit
    kv, problem = _section(result, "kv")
    if problem:
        return problem
    transit, problem = _section(result, "transit")
    if problem:
        return problem
    mounts, problem = _section(result, "mounts")
    if problem:
        return problem
    assert kv is not None and transit is not None and mounts is not None
    entries = [
        (f"kv:{_dict(i).get('path')}", _dict(i).get("last_version_at"))
        for i in _list(kv.get("items"))
    ] + [
        (f"transit:{_dict(i).get('name')}", _dict(i).get("last_version_at"))
        for i in _list(transit.get("items"))
    ]
    maximum = timedelta(days=limit)
    ages = [(name, _age(raw, evaluated_at)) for name, raw in entries]
    exceeded = [name for name, age in ages if age is not None and age > maximum]
    if exceeded:
        return _fail(
            f"{len(exceeded)} de {len(entries)} claves exceden {limit} días sin rotar: "
            f"{_names(exceeded)}"
        )
    uncovered = _list(_dict(mounts.get("summary")).get("uncovered"))
    if uncovered:
        return _unknown(f"motor {_names(uncovered)} sin cobertura; revísalo a mano")
    if not entries:
        return _fail("no hay claves gestionadas: instalar OpenBao no es cumplir")
    unreadable = [name for name, age in ages if age is None]
    if unreadable:
        return _unknown(f"fecha de la última versión ilegible o futura: {_names(unreadable)}")
    return _pass(f"{len(entries)} claves con la última versión de menos de {limit} días")


def _age(raw: Any, evaluated_at: datetime) -> timedelta | None:
    moment = parse_time(raw)
    if moment is None:
        return None
    age = evaluated_at - moment
    return age if age >= timedelta(0) else None


def _no_root_token(result: CollectionResult, _: datetime) -> Outcome:
    accessors, problem = _section(result, "accessors")
    if problem:
        return problem
    assert accessors is not None
    items = [_dict(i) for i in _list(accessors.get("items"))]
    summary = _dict(accessors.get("summary"))
    roots = [i for i in items if "root" in _list(i.get("policies"))]
    if roots:
        return _fail(f"hay {len(roots)} token(s) con la política root vivos")
    if summary.get("truncated") is not False or summary.get("unreadable") != 0:
        return _unknown("listado de tokens incompleto: no se puede descartar un root")
    if not items:
        return _unknown("el listado de tokens está vacío: no es coherente con el token de la app")
    return _pass(f"ninguno de los {len(items)} tokens vivos tiene la política root")


def app_policy_sha256() -> str:
    source = resources.files("ens_evidence.kit") / "openbao" / "ens-app-metadata.hcl"
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _app_token_scoped(result: CollectionResult, _: datetime) -> Outcome:
    limit = _threshold(result, "max_token_period_hours")
    if isinstance(limit, Outcome):
        return limit
    token, problem = _section(result, "token")
    if problem:
        return problem
    policies, problem = _section(result, "policies")
    if problem:
        return problem
    assert token is not None and policies is not None
    summary = _dict(token.get("summary"))
    failures = _token_failures(summary, limit) + _policy_failures(policies)
    if failures:
        return _fail("; ".join(failures))
    return _pass(
        f"el token solo tiene {APP_POLICY}, periodo y TTL válidos, valores denegados "
        "y la política es la de la release"
    )


def _token_failures(summary: dict[str, Any], limit_hours: int) -> list[str]:
    failures = []
    if summary.get("policies") != [APP_POLICY]:
        failures.append(f"las políticas del token no son exactamente [{APP_POLICY}]")
    period, ttl = _int(summary.get("period_seconds")), _int(summary.get("ttl_seconds"))
    if not period:
        failures.append("el token no tiene periodo")
    elif period > limit_hours * 3600:
        failures.append(f"el periodo del token supera {limit_hours} h")
    if not ttl or ttl <= 0:
        failures.append("el TTL del token no es mayor que 0")
    capabilities = _dict(summary.get("capabilities"))
    allowed = sorted(p for p, caps in capabilities.items() if caps != ["deny"])
    if not capabilities:
        failures.append("sin capacidades comprobadas sobre rutas de valores")
    elif allowed:
        failures.append(f"el token no tiene deny en: {_names(allowed)}")
    return failures


def _policy_failures(policies: dict[str, Any]) -> list[str]:
    item = _dict(next(iter(_list(policies.get("items"))), None))
    if item.get("exists") is not True:
        return [f"la política {APP_POLICY} no existe"]
    if item.get("sha256") != app_policy_sha256():
        return [f"la política {APP_POLICY} no coincide con la de la release"]
    return []


def _custodians_registered(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    seal, problem = _section(result, "seal")
    if problem:
        return problem
    custodians, problem = _section(result, "custodians")
    if problem:
        return problem
    assert seal is not None and custodians is not None
    declared = _dict(custodians.get("summary"))
    unreadable = _list(declared.get("unreadable"))
    if not declared.get("registered"):
        if unreadable:
            return _unknown(f"{len(unreadable)} registro(s) de custodios ilegibles")
        return _fail("no consta quién custodia las partes: no hay registro vigente")
    valid_until = parse_time(declared.get("valid_until"))
    if valid_until is None or valid_until <= evaluated_at:
        return _fail("no consta quién custodia: el registro de custodios ha caducado")
    actual = _dict(seal.get("summary"))
    shares, threshold = _int(actual.get("n")), _int(actual.get("t"))
    if shares is None or threshold is None or shares < 1:
        return _unknown("seal-status no informa de N y M")
    if (declared.get("count"), declared.get("key_threshold")) != (shares, threshold):
        return _fail(
            f"no consta quién custodia: el registro ({declared.get('count')} custodios, "
            f"umbral {declared.get('key_threshold')}) no casa con OpenBao ({shares}, {threshold})"
        )
    return _pass(f"hay un registro vigente de {shares} custodios con umbral {threshold}")


def _check(check_id: str, decide: Callable[[CollectionResult, datetime], Outcome]):
    def run(result: CollectionResult, evidence_id: str, evaluated_at: datetime):
        return evaluate(check_id, decide, result, evidence_id, evaluated_at)

    return run


unsealed = _check(UNSEALED, _unsealed)
audit_enabled = _check(AUDIT_ENABLED, _audit_enabled)
keys_rotated = _check(KEYS_ROTATED, _keys_rotated)
no_root_token = _check(NO_ROOT_TOKEN, _no_root_token)
app_token_scoped = _check(APP_TOKEN_SCOPED, _app_token_scoped)
custodians_registered = _check(CUSTODIANS_REGISTERED, _custodians_registered)
