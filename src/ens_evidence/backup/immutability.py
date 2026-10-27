"""Prueba de inmutabilidad del destino con objetos canario desechables.

Solo escribe, intenta borrar y sobrescribir claves nuevas bajo cada prefijo protegido;
nunca lista, lee ni toca objetos reales del repositorio restic.
"""

import secrets as randomness
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from ens_evidence.backup.errors import BackupError
from ens_evidence.backup.records import CanaryResult, ImmutabilityCheck

PROTECTED_PREFIXES = ("data", "index", "snapshots", "keys")
_ORIGINAL = b"canario original\n"
_OVERWRITE = b"canario sobrescrito\n"


def parse_s3_repository(repository: str) -> tuple[str | None, str, str]:
    """Devuelve (endpoint, bucket, prefijo) de `s3:https://host/bucket/ruta`."""
    if not repository.startswith("s3:"):
        raise BackupError("verify-immutability solo admite repositorios s3")
    location = repository.removeprefix("s3:")
    if "://" not in location:
        location = f"https://{location}"
    parts = urlsplit(location)
    bucket, _, prefix = parts.path.strip("/").partition("/")
    if not parts.netloc or not bucket:
        raise BackupError("el repositorio s3 no indica endpoint y bucket")
    return f"{parts.scheme}://{parts.netloc}", bucket, prefix.strip("/")


def verify_immutability(
    client: Any, repository: str, bucket: str, base_prefix: str, now: datetime
) -> ImmutabilityCheck:
    results = [_probe(client, bucket, base_prefix, prefix) for prefix in PROTECTED_PREFIXES]
    return ImmutabilityCheck(checked_at=now, repository=repository, results=results)


def _probe(client: Any, bucket: str, base_prefix: str, prefix: str) -> CanaryResult:
    folder = f"{base_prefix}/{prefix}" if base_prefix else prefix
    key = f"{folder}/ens-canary-{randomness.token_hex(8)}"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=_ORIGINAL)
    except Exception as exc:
        raise BackupError(
            f"no se pudo crear el canario en {prefix}/ ({type(exc).__name__}); "
            "sin él no se puede probar la protección"
        ) from None
    deleted = _attempt(lambda: client.delete_object(Bucket=bucket, Key=key))
    gone = _read(client, bucket, key) is None
    overwritten = _attempt(lambda: client.put_object(Bucket=bucket, Key=key, Body=_OVERWRITE))
    changed = _read(client, bucket, key) == _OVERWRITE
    problems = []
    if gone:
        problems.append("el canario desapareció: el borrado surtió efecto")
    if changed:
        problems.append("el canario cambió de contenido: la sobrescritura surtió efecto")
    detail = "; ".join(problems) or (
        f"borrado {'aceptado pero sin efecto' if deleted else 'rechazado'}, "
        f"sobrescritura {'aceptada pero sin efecto' if overwritten else 'rechazada'}"
    )
    return CanaryResult(prefix=prefix, protected=not problems, detail=detail)


def _attempt(action: Any) -> bool:
    try:
        action()
    except Exception:
        return False
    return True


def _read(client: Any, bucket: str, key: str) -> bytes | None:
    try:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception:
        return None
