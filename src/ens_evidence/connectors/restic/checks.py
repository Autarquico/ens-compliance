"""Checks BACKUP-001..006 sobre la evidencia recogida de Restic.

`PASS` solo con evidencia positiva; `FAIL` solo con dato presente que no cumple; lo no
demostrable (registros de otro repositorio, ilegibles, con fechas incoherentes, sin atar a
una restauración) es `UNKNOWN`. Con la recogida global sin éxito, o una sección sin éxito,
no hay `PASS` (traducción del ADR 0002). Los motivos dicen "según registro de ...": los
registros del workspace no se autentican solos (ADR 0008).

Umbrales y declaración `offsite` viajan en la evidencia (`data.thresholds`, `data.offsite`);
los checks no leen la configuración del workspace. `CheckResult.subject` es el id del
repositorio (001-004, 006) o el `test_id` evaluado (005). Se evalúa siempre el último
intento (por inicio), nunca el último éxito. Sin mapeo al anexo II.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ens_evidence.models.contracts import CheckResult, CollectionResult, translate_collection
from ens_evidence.models.enums import CheckStatus, CollectionStatus

RECENT = "restic.backup_001_recent"
NO_ERRORS = "restic.backup_002_no_errors"
REPOSITORY_ACCESSIBLE = "restic.backup_003_repository_accessible"
INTEGRITY = "restic.backup_004_integrity"
RESTORE_TEST = "restic.backup_005_restore_test"
OFFSITE = "restic.backup_006_offsite"

FOREIGN_RECORD = "registro de otro repositorio o sin id de repositorio"


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


def _unrecovered(collection: CollectionStatus, reason: str) -> Outcome:
    translation = translate_collection(collection)
    return Outcome(translation.status, reason, collection, translation.reason_code)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _status(raw: Any) -> CollectionStatus:
    try:
        return CollectionStatus(raw)
    except ValueError:
        return CollectionStatus.ERROR


def _instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def _positive_int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else None


def _repository_id(result: CollectionResult) -> str | None:
    value = result.data.get("repository_id")
    return value if isinstance(value, str) and value else None


def _global_failure(result: CollectionResult) -> Outcome | None:
    if result.collection_status is CollectionStatus.SUCCESS:
        return None
    detail = result.data.get("error") or result.collection_status.value
    return _unrecovered(result.collection_status, f"recogida de Restic fallida: {detail}")


def _section(result: CollectionResult, name: str) -> tuple[dict[str, Any] | None, Outcome | None]:
    """La sección con éxito, o el resultado traducido si falta o no se recogió."""
    raw = _dict(_dict(result.data.get("sections")).get(name))
    if not raw:
        return None, _unrecovered(CollectionStatus.ERROR, f"sección {name} ausente en la evidencia")
    status = _status(raw.get("status"))
    if status is not CollectionStatus.SUCCESS:
        reason = raw.get("reason") or status.value
        return None, _unrecovered(status, f"sección {name} no recogida: {reason}")
    unreadable = _list(_dict(raw.get("summary")).get("unreadable"))
    if unreadable:
        return None, _unknown(f"{len(unreadable)} registro(s) de {name} ilegibles: no se omiten")
    return raw, None


def _items(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _list(section.get("items")) if isinstance(item, dict)]


def _latest(items: list[dict[str, Any]], key: str) -> dict[str, Any] | Outcome:
    """El último intento por `key`; un registro sin instante válido impide elegir."""
    dated = [(_instant(item.get(key)), item) for item in items]
    if any(moment is None for moment, _ in dated):
        return _unknown(f"registro con {key} ilegible: no se puede elegir el último intento")
    return max(dated, key=lambda pair: pair[0])[1]  # type: ignore[arg-type,return-value]


def _timeline_problem(item: dict[str, Any], evaluated_at: datetime) -> str | None:
    """`start <= end <= evaluated_at` (el fin puede faltar si el intento quedó abierto)."""
    start = _instant(item.get("started_at"))
    end_raw = item.get("finished_at")
    end = _instant(end_raw)
    if start is None or (end_raw is not None and end is None):
        return "fechas del registro ilegibles"
    if start > evaluated_at or (end is not None and not start <= end <= evaluated_at):
        return "fechas incoherentes o futuras en el registro"
    return None


def _age(moment: datetime, evaluated_at: datetime) -> timedelta | None:
    age = evaluated_at - moment
    return age if age >= timedelta(0) else None


def _hours(age: timedelta) -> str:
    return f"{age.total_seconds() / 3600:.1f} h"


def _threshold(result: CollectionResult, name: str) -> int | Outcome:
    value = _positive_int(_dict(result.data.get("thresholds")).get(name))
    return value if value is not None else _unknown(f"umbral {name} ausente o inválido")


def _evaluate(
    check_id: str,
    result: CollectionResult,
    evidence_id: str,
    evaluated_at: datetime,
    subject: str | None,
    decide: Callable[[CollectionResult, datetime], Outcome],
) -> list[CheckResult]:
    outcome = _global_failure(result) or decide(result, evaluated_at.astimezone(UTC))
    return [
        CheckResult(
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
    ]


def _recent(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    """Último snapshot presente en el repositorio y citado por un run `completed` atado."""
    limit = _threshold(result, "max_backup_age_hours")
    if isinstance(limit, Outcome):
        return limit
    snapshots, problem = _section(result, "snapshots")
    if problem:
        return problem
    runs, problem = _section(result, "runs")
    if problem:
        return problem
    assert snapshots is not None and runs is not None
    if snapshots.get("summary", {}).get("repository_exists") is False:
        return _fail("el repositorio no existe: no hay snapshots")
    trusted = {
        run["run_id"]
        for run in _items(runs)
        if run.get("status") == "completed" and run.get("bound") is True
    }
    if not _items(snapshots):
        return _fail("el repositorio no tiene ningún snapshot")
    counted = [s for s in _items(snapshots) if not s.get("foreign") and s.get("run_id") in trusted]
    if not counted:
        return _unknown(
            "hay snapshots en el repositorio, pero ninguno está citado por un backup completado "
            "de este repositorio (ver foreign_snapshots)"
        )
    latest = _latest(counted, "time")
    if isinstance(latest, Outcome):
        return latest
    age = _age(_instant(latest["time"]), evaluated_at)  # type: ignore[arg-type]
    if age is None:
        return _unknown("el último snapshot tiene fecha futura")
    detail = f"último snapshot de hace {_hours(age)} (máximo {limit} h)"
    return _pass(detail) if age <= timedelta(hours=limit) else _fail(detail)


def _no_errors(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    """La última ejecución registrada (no el último éxito) debe estar `completed` con snapshot."""
    runs, problem = _section(result, "runs")
    if problem:
        return problem
    assert runs is not None
    if not _items(runs):
        return _unknown("sin ejecuciones de backup registradas")
    latest = _latest(_items(runs), "started_at")
    if isinstance(latest, Outcome):
        return latest
    if latest.get("bound") is not True:
        return _unknown(f"la última ejecución es un {FOREIGN_RECORD}")
    if timeline := _timeline_problem(latest, evaluated_at):
        return _unknown(f"última ejecución: {timeline}")
    status = latest.get("status")
    if status == "failed":
        return _fail("la última ejecución de backup falló, según registro de ejecuciones")
    if status == "started":
        return _fail("la última ejecución de backup quedó sin terminar (interrumpida)")
    if status != "completed":
        return _unknown("estado de la última ejecución no interpretable")
    snapshots, problem = _section(result, "snapshots")
    if problem:
        return problem
    assert snapshots is not None
    present = any(
        s.get("run_id") == latest.get("run_id") and not s.get("foreign") for s in _items(snapshots)
    )
    if not present:
        return _unknown(
            "la última ejecución completada cita un snapshot que no está en el repositorio"
        )
    note = " (reconstruida desde el snapshot)" if latest.get("reconstructed_from_snapshot") else ""
    return _pass(f"la última ejecución se completó{note} y su snapshot está en el repositorio")


def _repository_item(result: CollectionResult) -> dict[str, Any] | Outcome:
    section, problem = _section(result, "repository")
    if problem:
        return problem
    assert section is not None
    items = _items(section)
    return items[0] if items else _unknown("sección repository sin datos")


def _accessible(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    repository = _repository_item(result)
    if isinstance(repository, Outcome):
        return repository
    match repository.get("exists"):
        case True:
            return _pass("restic leyó la configuración del repositorio")
        case False:
            return _fail("el repositorio no existe (restic cat config, código 10)")
        case _:
            return _unknown("existencia del repositorio no interpretable")


def _integrity(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    """El último `backup check`: 0 errores, reciente y con cobertura de datos suficiente."""
    max_days = _threshold(result, "max_check_age_days")
    if isinstance(max_days, Outcome):
        return max_days
    minimum = _dict(result.data.get("thresholds")).get("min_check_read_data_percent")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or not 0 <= minimum <= 100:
        return _unknown("umbral min_check_read_data_percent ausente o inválido")
    section, problem = _section(result, "integrity_checks")
    if problem:
        return problem
    assert section is not None
    if not _items(section):
        return _unknown("nunca se ha ejecutado `backup check`")
    latest = _latest(_items(section), "started_at")
    if isinstance(latest, Outcome):
        return latest
    if latest.get("bound") is not True:
        return _unknown(f"el último check es un {FOREIGN_RECORD}")
    if timeline := _timeline_problem(latest, evaluated_at):
        return _unknown(f"último check: {timeline}")
    if latest.get("status") == "started":
        return _fail("el último `backup check` quedó sin terminar (interrumpido)")
    if latest.get("status") == "failed" or latest.get("num_errors") != 0:
        return _fail("el último `backup check` falló o halló errores, según registro de checks")
    finished = _instant(latest.get("finished_at"))
    coverage = latest.get("read_data_percent")
    if finished is None or not isinstance(coverage, int):
        return _unknown("último check completado sin fin o cobertura interpretables")
    age = _age(finished, evaluated_at)
    if age is None:
        return _unknown("el último check tiene fecha futura")
    if age > timedelta(days=max_days):
        return _fail(f"último check de hace {age.days} d (máximo {max_days} d)")
    if coverage < minimum:
        return _fail(f"cobertura de datos {coverage} % inferior al mínimo {minimum} %")
    return _pass(
        f"último check sin errores hace {age.days} d, cobertura de datos {coverage} % "
        f"(mínimo {minimum} %), según registro de checks"
    )


def _declared_failure(test: dict[str, Any]) -> str | None:
    result = _dict(test.get("result"))
    review = _dict(test.get("review"))
    started = result.get("application_started")
    justified = started == "not_applicable" and bool(result.get("application_justification"))
    if result.get("restored") is not True:
        return "la restauración no se completó"
    if result.get("integrity_verified") is not True:
        return "la integridad de lo restaurado no se verificó"
    if started is not True and not justified:
        return "la aplicación no arrancó (y no se justificó como no aplicable)"
    if test.get("isolated_target") is not True:
        return "el destino no se declaró aislado"
    if review.get("approved") is not True:
        return "la revisión no aprobó el restore test"
    return None


def _restore_test(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    """El último restore test debe estar completo, aprobado, atado y vigente."""
    validity = _threshold(result, "restore_test_validity_days")
    if isinstance(validity, Outcome):
        return validity
    section, problem = _section(result, "restore_tests")
    if problem:
        return problem
    assert section is not None
    if not _items(section):
        return _unknown("ningún restore test registrado")
    latest = _latest(_items(section), "performed_at")
    if isinstance(latest, Outcome):
        return latest
    if latest.get("bound") is not True:
        return _unknown(f"el último restore test es un {FOREIGN_RECORD}")
    review = _dict(latest.get("review"))
    performed = _instant(latest.get("performed_at"))
    reviewed = _instant(review.get("reviewed_at"))
    valid_until = _instant(review.get("valid_until"))
    if performed is None or reviewed is None or valid_until is None:
        return _unknown("fechas del restore test ilegibles")
    if not performed <= reviewed <= evaluated_at:
        return _unknown("restore test o revisión con fecha futura o incoherente")
    declared = "isolated_target es una declaración humana, no comprobada por la herramienta"
    if failure := _declared_failure(latest):
        return _fail(f"{failure}, según el restore test; {declared}")
    if not evaluated_at < valid_until:
        return _fail("la revisión del último restore test ha caducado")
    age = (evaluated_at - performed).days
    if age > validity:
        return _fail(f"último restore test de hace {age} d (vigencia {validity} d)")
    if latest.get("restore_linked") is not True:
        problem = latest.get("restore_link_problem")
        return _unknown(
            f"el restore test no está atado a una restauración verificada: {problem}"
            if isinstance(problem, str)
            else "el restore test no está atado a una restauración verificada, única y con "
            "snapshot presente en el repositorio"
        )
    return _pass(
        f"restore test {latest.get('test_id')} hace {age} d, aprobado y atado a una "
        f"restauración verificada; {declared}"
    )


def _offsite(result: CollectionResult, evaluated_at: datetime) -> Outcome:
    repository = _repository_item(result)
    if isinstance(repository, Outcome):
        return repository
    match repository.get("backend"):
        case "local":
            return _fail("el repositorio es local: no hay copia externa")
        case "remote":
            pass
        case _:
            return _unknown("tipo de destino no reconocido: requiere revisión humana del destino")
    if repository.get("exists") is not True:
        return _fail("el repositorio remoto no existe o no es accesible")
    declaration = _dict(result.data.get("offsite"))
    reviewed = _instant(declaration.get("reviewed_at"))
    valid_until = _instant(declaration.get("valid_until"))
    if not declaration:
        return _unknown(
            "destino remoto sin declaración offsite: requiere revisión humana del destino"
        )
    if reviewed is None or valid_until is None or not reviewed <= evaluated_at < valid_until:
        return _unknown("declaración offsite caducada, futura o ilegible: requiere revisión humana")
    return _pass(
        "destino remoto accesible con declaración offsite vigente; la separación "
        "administrativa es un control organizativo declarado, no comprobado"
    )


def backup_001_recent(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _evaluate(RECENT, result, evidence_id, evaluated_at, _repository_id(result), _recent)


def backup_002_no_errors(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _evaluate(
        NO_ERRORS, result, evidence_id, evaluated_at, _repository_id(result), _no_errors
    )


def backup_003_repository_accessible(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _evaluate(
        REPOSITORY_ACCESSIBLE,
        result,
        evidence_id,
        evaluated_at,
        _repository_id(result),
        _accessible,
    )


def backup_004_integrity(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _evaluate(
        INTEGRITY, result, evidence_id, evaluated_at, _repository_id(result), _integrity
    )


def backup_005_restore_test(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    """El sujeto es el `test_id` del último restore test, el mismo que evalúa el check."""
    return _evaluate(
        RESTORE_TEST,
        result,
        evidence_id,
        evaluated_at,
        _restore_test_subject(result),
        _restore_test,
    )


def backup_006_offsite(
    result: CollectionResult, evidence_id: str, evaluated_at: datetime
) -> list[CheckResult]:
    return _evaluate(OFFSITE, result, evidence_id, evaluated_at, _repository_id(result), _offsite)


def _restore_test_subject(result: CollectionResult) -> str | None:
    section = _dict(_dict(result.data.get("sections")).get("restore_tests"))
    tests = _items(section)
    latest = _latest(tests, "performed_at") if tests else None
    if isinstance(latest, dict) and isinstance(latest.get("test_id"), str):
        return latest["test_id"]
    return _repository_id(result)


ALL_CHECKS = (
    backup_001_recent,
    backup_002_no_errors,
    backup_003_repository_accessible,
    backup_004_integrity,
    backup_005_restore_test,
    backup_006_offsite,
)
