import errno
import os
import stat
from pathlib import Path, PurePosixPath

from ens_evidence.endpoint.pack import SCRIPT_NAME, bundle_files

OPEN_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class BundleTargetError(Exception):
    pass


def write_bundle(out: Path) -> list[Path]:
    """Escribe el script y el paquete de consultas en `out`, que debe estar vacío o no existir."""
    written = []
    opened: dict[PurePosixPath, int] = {}
    try:
        opened[PurePosixPath(".")] = _create_or_open(out)
        for relative, body in bundle_files().items():
            path = PurePosixPath(relative)
            parent = _subdirectory(opened, path.parent)
            _create_exclusive(parent, path.name, body, 0o755 if relative == SCRIPT_NAME else 0o644)
            written.append(out / relative)
    except BundleTargetError:
        raise
    except PermissionError:
        raise BundleTargetError(f"sin permiso para escribir en {out}") from None
    except (FileExistsError, NotADirectoryError):
        raise BundleTargetError(
            f"no se pudo escribir {out}: algo cambió mientras se escribía"
        ) from None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise BundleTargetError(
                f"no se pudo escribir {out}: un enlace simbólico sustituyó a un fichero"
            ) from None
        raise
    finally:
        for descriptor in opened.values():
            os.close(descriptor)
    return written


def _create_or_open(out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.mkdir(mode=0o755)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(out, OPEN_DIRECTORY)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise BundleTargetError(f"{out} debe ser un directorio vacío o no existir") from None
        raise
    try:
        _check_directory(out, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _check_directory(out: Path, descriptor: int) -> None:
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid():
        raise BundleTargetError(f"{out} pertenece a otro usuario")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise BundleTargetError(
            f"{out} es escribible por grupo u otros; usa permisos 755 o más estrictos"
        )
    if os.listdir(descriptor):
        raise BundleTargetError(f"{out} debe ser un directorio vacío o no existir")


def _subdirectory(opened: dict[PurePosixPath, int], path: PurePosixPath) -> int:
    if path not in opened:
        parent = _subdirectory(opened, path.parent)
        os.mkdir(path.name, 0o755, dir_fd=parent)
        opened[path] = os.open(path.name, OPEN_DIRECTORY, dir_fd=parent)
    return opened[path]


def _create_exclusive(directory: int, name: str, body: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(name, flags, mode, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(body)
