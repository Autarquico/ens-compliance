"""Paquete de instalación del kit: MANIFEST v1 y tar.gz determinista (compartido por los kits)."""

import gzip
import hashlib
import io
import tarfile
from datetime import datetime

MANIFEST_VERSION = 1


def manifest(files: dict[str, bytes]) -> bytes:
    lines = [f"version: {MANIFEST_VERSION}"]
    lines += [f"{hashlib.sha256(body).hexdigest()}  {name}" for name, body in files.items()]
    return ("\n".join(lines) + "\n").encode()


def pack(
    files: dict[str, bytes], now: datetime, *, root: str, private: tuple[str, ...] = ()
) -> bytes:
    mtime = int(now.timestamp())
    files = {**files, f"{root}/MANIFEST": manifest(files)}
    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=mtime, compresslevel=9) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar,
    ):
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mtime = mtime
            info.mode = 0o600 if name in private else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(body))
    return raw.getvalue()
