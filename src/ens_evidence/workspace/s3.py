"""Backend S3-compatible (S3, R2...). Requiere el extra opcional `ens-evidence[s3]`."""

from typing import Any

from ens_evidence.workspace.errors import (
    AccessDenied,
    AlreadyExists,
    ConditionalWriteUnsupported,
    NotFound,
    NotText,
    StorageUnavailable,
)
from ens_evidence.workspace.keys import normalize_key

_NOT_FOUND = {"404", "NoSuchKey", "NotFound"}
_EXISTS = {"412", "PreconditionFailed"}
_UNSUPPORTED = {"501", "NotImplemented", "InvalidArgument", "UnsupportedOperation"}
_DENIED = {"401", "403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}


class S3Store:
    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        client: Any = None,
    ) -> None:
        self._bucket = bucket
        base = normalize_key(prefix, allow_empty=True)
        self._prefix = f"{base}/" if base else ""
        self._client = client if client is not None else _build_client(endpoint_url, region)

    def read_text(self, key: str) -> str:
        try:
            return self.read_bytes(key).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotText("el fichero no es texto UTF-8") from exc

    def read_bytes(self, key: str) -> bytes:
        try:
            return self._client.get_object(Bucket=self._bucket, Key=self._full(key))["Body"].read()
        except Exception as exc:
            raise _translate(exc) from exc

    def write_text(self, key: str, text: str) -> None:
        self.write_bytes(key, text.encode("utf-8"))

    def write_bytes(self, key: str, data: bytes) -> None:
        try:
            self._client.put_object(Bucket=self._bucket, Key=self._full(key), Body=data)
        except Exception as exc:
            raise _translate(exc) from exc

    def create_bytes(self, key: str, data: bytes) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=self._full(key), Body=data, IfNoneMatch="*"
            )
        except Exception as exc:
            raise _translate_create(exc) from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._full(key))
        except Exception as exc:
            translated = _translate(exc)
            if isinstance(translated, NotFound):
                return False
            raise translated from exc
        return True

    def list(self, prefix: str = "") -> list[str]:
        base = normalize_key(prefix, allow_empty=True)
        full_prefix = self._prefix + (f"{base}/" if base else "")
        keys: list[str] = []
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=full_prefix
            )
            for page in pages:
                for item in page.get("Contents", []):
                    if not item["Key"].endswith("/"):
                        keys.append(item["Key"].removeprefix(self._prefix))
        except Exception as exc:
            raise _translate(exc) from exc
        return sorted(keys)

    def _full(self, key: str) -> str:
        return self._prefix + normalize_key(key)


def _build_client(endpoint_url: str | None, region: str | None) -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise StorageUnavailable(
            "el backend s3 requiere el extra opcional: pip install 'ens-evidence[s3]'"
        ) from exc
    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)


def _translate_create(exc: Exception) -> Exception:
    if isinstance(exc, (AlreadyExists, ConditionalWriteUnsupported)):
        return exc
    code = _error_code(exc)
    if code in _EXISTS:
        return AlreadyExists("la clave ya existe")
    if code in _UNSUPPORTED or type(exc).__name__ == "ParamValidationError":
        return ConditionalWriteUnsupported(
            "el backend no admite escritura condicional (If-None-Match); "
            "no se crean evidencias sin ella"
        )
    return _translate(exc)


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def _translate(exc: Exception) -> Exception:
    if isinstance(exc, (NotFound, AccessDenied, StorageUnavailable, ValueError)):
        return exc
    code = _error_code(exc)
    if code in _NOT_FOUND:
        return NotFound("clave no encontrada")
    if code in _DENIED or type(exc).__name__ == "NoCredentialsError":
        return AccessDenied("acceso denegado")
    return StorageUnavailable("almacenamiento no disponible")
