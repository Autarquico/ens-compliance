"""Entorno del script de endpoints: un `osqueryi` falso por SQL y paquetes a medida.

Las únicas fronteras simuladas son osqueryi, uname, hostname y timeout, en un PATH
temporal; el script `sh` y el importador se ejecutan de verdad.
"""

import io
import json
import os
import shlex
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ens_evidence.endpoint.bundle import write_bundle
from ens_evidence.endpoint.importer import import_endpoint
from ens_evidence.endpoint.pack import SCRIPT_NAME, load_pack, pack_files
from ens_evidence.evidence import list_manifests

LAPTOP_UUID = "11111111-2222-3333-4444-555555555555"
LAPTOP_HOST = "lap-ana.taller-norte.example"
SERVER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SERVER_HOST = "srv-app.taller-norte.example"

ASSETS = """\
assets:
  - id: lap-ana
    type: laptop
    deployment: deployed
    hostname: lap-ana.taller-norte.example
    os: macOS 15
    owner: Ana Ruiz
    encrypted: true
    usage: Desarrollo
  - id: srv-app
    type: server
    deployment: deployed
    hostname: srv-app.taller-norte.example
    function: Aplicaciones
    os: Debian 12
    environment: producción
    location: Oficina
  - id: app-facturas
    type: application
    deployment: deployed
    service: Facturación
    domain: facturas.taller-norte.example
    repo: org/facturas
    database: db-facturas
"""

CONFIG: dict[str, Any] = {
    "max_evidence_age_days": 30,
    "supported_os": [{"platform": "darwin", "name": "macOS", "release": "15"}],
    "hosts": {
        "lap-ana": {"hardware_uuid": LAPTOP_UUID, "hostname": LAPTOP_HOST},
        "srv-app": {"hardware_uuid": SERVER_UUID, "hostname": SERVER_HOST},
    },
}

ROWS: dict[str, list[dict[str, str]]] = {
    "os_version": [{"name": "macOS", "platform": "darwin", "version": "15.1", "major": "15"}],
    "osquery_info": [{"version": "5.14.1"}],
    "root_mount": [{"device": "/dev/disk3s1s1", "path": "/", "type": "apfs"}],
    "disk_encryption": [{"name": "/dev/disk3s1", "encrypted": "1", "type": "APFS"}],
    "filevault_status": [{"filevault_status": "on"}],
    "alf": [{"global_state": "1", "stealth_enabled": "0"}],
    "software_update_policy": [
        {
            "domain": "com.apple.SoftwareUpdate",
            "name": "AutomaticallyInstallMacOSUpdates",
            "value": "1",
            "user_scoped": "0",
        }
    ],
    "apps": [
        {
            "name": "Safari.app",
            "bundle_short_version": "18",
            "path": "/Users/ana/Safari.app",
        }
    ],
    "deb_packages": [
        {"name": "openssl", "version": "3.0", "arch": "amd64", "status": "install ok installed"}
    ],
    "rpm_packages": [],
}


def default_rows(uuid: str, hostname: str) -> dict[str, list[dict[str, str]]]:
    return {**ROWS, "system_info": [{"hostname": hostname, "uuid": uuid}]}


@dataclass
class Answer:
    out: bytes | list[dict[str, Any]] | None = None
    code: int = 0
    err: str = ""


@dataclass
class ScriptRun:
    process: subprocess.CompletedProcess[str]
    bundle: Path
    out_dir: Path
    tmp: Path
    home: Path
    cwd: Path
    fake_dir: Path
    archive: Path | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def data(self) -> bytes:
        assert self.archive is not None, self.process.stderr
        return self.archive.read_bytes()


FAKE_OSQUERYI = """\
#!/bin/sh
if [ "$1" = "--version" ]; then echo "osqueryi version 5.14.1"; exit 0; fi
[ "$1" = "--json" ] || exit 64
sql=$2
id=
{dispatch}
[ -n "$id" ] || { echo "consulta desconocida" >&2; exit 65; }
[ "$FAKE_TERM_ON" = "$id" ] && { kill -TERM "$PPID"; sleep 1; }
[ -f "$FAKE_DIR/$id.err" ] && cat "$FAKE_DIR/$id.err" >&2
[ -f "$FAKE_DIR/$id.out" ] && cat "$FAKE_DIR/$id.out"
code=0
[ -f "$FAKE_DIR/$id.code" ] && code=$(cat "$FAKE_DIR/$id.code")
exit "$code"
"""

FAKE_DATE = """\
#!/bin/sh
case "$*" in
    *%Y%m%dT%H%M%SZ*) [ -z "$FAKE_STAMP" ] || { echo "$FAKE_STAMP"; exit 0; } ;;
esac
exec /bin/date "$@"
"""

FAKE_TIMEOUT = """\
#!/bin/sh
echo "$@" >>"$FAKE_DIR/timeout.log"
[ -z "$FAKE_TIMEOUT_EXPIRE" ] || exit 124
shift
exec "$@"
"""


def _install_fakes(base: Path) -> tuple[Path, Path]:
    fake_bin, fake_dir = base / "fakebin", base / "fake"
    fake_bin.mkdir()
    fake_dir.mkdir()
    sql_of = {
        query_id: pack_files()[f"queries/{query_id}.sql"].decode().rstrip("\n")
        for query_id in (q.id for q in load_pack().queries)
    }
    dispatch = "\n".join(
        f'[ "$sql" = {shlex.quote(sql)} ] && id={query_id}' for query_id, sql in sql_of.items()
    )
    scripts = {
        "osqueryi": FAKE_OSQUERYI.replace("{dispatch}", dispatch),
        "timeout": FAKE_TIMEOUT,
        "uname": "#!/bin/sh\nprintf '%s\\n' \"$FAKE_UNAME\"\n",
        "hostname": "#!/bin/sh\nprintf '%s\\n' \"$FAKE_HOSTNAME\"\n",
        "date": FAKE_DATE,
    }
    for name, body in scripts.items():
        (fake_bin / name).write_text(body)
        (fake_bin / name).chmod(0o755)
    return fake_bin, fake_dir


def _write_answers(fake_dir: Path, answers: dict[str, Answer]) -> None:
    for query_id, answer in answers.items():
        if answer.out is not None:
            body = (
                answer.out
                if isinstance(answer.out, bytes)
                else json.dumps(answer.out, indent=2).encode()
            )
            (fake_dir / f"{query_id}.out").write_bytes(body)
        (fake_dir / f"{query_id}.code").write_text(str(answer.code))
        if answer.err:
            (fake_dir / f"{query_id}.err").write_text(answer.err)


def collect(
    base: Path,
    *,
    platform: str = "darwin",
    uuid: str = LAPTOP_UUID,
    hostname: str = LAPTOP_HOST,
    answers: dict[str, Answer] | None = None,
    bundle_edit: Any = None,
    timeout_expires: bool = False,
    umask: str = "000",
    env: dict[str, str] | None = None,
    prepare: Any = None,
) -> ScriptRun:
    """Genera el bundle con `endpoint script`, lo edita si se pide y ejecuta el script."""
    base.mkdir(parents=True, exist_ok=True)
    bundle, out_dir = base / "bundle", base / "out"
    home, cwd, tmp = base / "home", base / "cwd", base / "tmp"
    for directory in (out_dir, home, cwd, tmp):
        directory.mkdir()
    write_bundle(bundle)
    if bundle_edit is not None:
        bundle_edit(bundle)
    fake_bin, fake_dir = _install_fakes(base)
    rows = {query_id: Answer(out=body) for query_id, body in default_rows(uuid, hostname).items()}
    _write_answers(fake_dir, {**rows, **(answers or {})})
    script_env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "FAKE_DIR": str(fake_dir),
        "FAKE_UNAME": "Darwin" if platform == "darwin" else "Linux",
        "FAKE_HOSTNAME": hostname,
    }
    if timeout_expires:
        script_env["FAKE_TIMEOUT_EXPIRE"] = "1"
    script_env.update(env or {})
    if prepare is not None:
        prepare(out_dir)
    return _run(bundle, out_dir, tmp, home, cwd, fake_dir, script_env, umask)


def _run(bundle, out_dir, tmp, home, cwd, fake_dir, env, umask) -> ScriptRun:
    process = subprocess.run(
        ["sh", "-c", f'umask {umask}; exec sh "$0" "$@"', str(bundle / SCRIPT_NAME), str(out_dir)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    archives = sorted(p for p in out_dir.glob("*.endpoint.tar.gz") if not p.is_symlink())
    run = ScriptRun(process, bundle, out_dir, tmp, home, cwd, fake_dir)
    if archives:
        run.archive = archives[0]
        run.meta = json.loads(members_of(run.data)["meta.json"])
    return run


def members_of(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive
        }


def sums_of(members: dict[str, bytes]) -> bytes:
    import hashlib

    return "".join(
        f"{hashlib.sha256(body).hexdigest()}  {name}\n"
        for name, body in members.items()
        if name != "SHA256SUMS"
    ).encode()


def tar_of(entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for info, body in entries:
            archive.addfile(info, None if body is None else io.BytesIO(body))
    return buffer.getvalue()


def regular(name: str, body: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    return info, body


def build(members: dict[str, bytes], *, fix_sums: bool = True) -> bytes:
    if fix_sums:
        members = {**members, "SHA256SUMS": sums_of(members)}
    return tar_of([regular(name, body) for name, body in members.items()])


def rebuilt(
    data: bytes,
    *,
    meta: dict[str, Any] | None = None,
    replace: dict[str, bytes] | None = None,
    drop: tuple[str, ...] = (),
    add: dict[str, bytes] | None = None,
    fix_sums: bool = True,
) -> bytes:
    members = members_of(data)
    if meta is not None:
        members["meta.json"] = json.dumps({**json.loads(members["meta.json"]), **meta}).encode()
    members.update(replace or {})
    for name in drop:
        del members[name]
    members.update(add or {})
    return build(members, fix_sums=fix_sums)


def import_package(store, data: bytes, asset: str = "lap-ana", by: str = "ana"):
    return import_endpoint(store, data, asset_id=asset, submitted_by=by, now=datetime.now(UTC))


def persisted_keys(store) -> list[str]:
    return store.list("evidence")


def normalized(store) -> dict[str, Any]:
    manifest = next(m for m in list_manifests(store).manifests if m.source == "endpoint-script")
    return json.loads(store.read_bytes(manifest.artifact.key))


def utc_now() -> datetime:
    return datetime.now(UTC)
