import multiprocessing
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ens_evidence.workspace.cycle_lock import (
    CycleBusy,
    CycleContext,
    CycleLock,
    acquire_or_wait,
    resolve_state_dir,
    workspace_identity,
)
from ens_evidence.workspace.settings import (
    LocalSettings,
    S3Settings,
    Settings,
    WorkspaceSettings,
)

_SPAWN = multiprocessing.get_context("spawn")


def _local(path: Path) -> Settings:
    return Settings(workspace=WorkspaceSettings(backend="local", local=LocalSettings(path=path)))


def _s3(bucket: str, prefix: str) -> Settings:
    return Settings(
        workspace=WorkspaceSettings(backend="s3", s3=S3Settings(bucket=bucket, prefix=prefix))
    )


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


def _hold(path: str, ready, release) -> None:
    lock = CycleLock("shared", Path(path))
    lock.acquire()
    ready.set()
    release.wait(30)


def _try(path: str, queue) -> None:
    try:
        CycleLock("shared", Path(path)).acquire()
        queue.put("acquired")
    except CycleBusy:
        queue.put("busy")


def _probe_fd(fd: int, queue) -> None:
    try:
        os.fstat(fd)
        queue.put("inherited")
    except OSError:
        queue.put("closed")


def test_acquire_and_release(tmp_path: Path) -> None:
    lock = CycleLock("ws", tmp_path)
    with lock:
        assert lock.held
        assert lock.path.parent == tmp_path
        assert lock.path.name.startswith("cycle-") and lock.path.suffix == ".lock"
        assert len(lock.path.stem) == len("cycle-") + 12
    assert not lock.held
    with CycleLock("ws", tmp_path):
        pass
    assert lock.path.exists()


def test_busy_lock_raises(tmp_path: Path) -> None:
    with CycleLock("ws", tmp_path):
        with pytest.raises(CycleBusy):
            CycleLock("ws", tmp_path).acquire()


def test_failed_acquire_keeps_owner_lock_file(tmp_path: Path) -> None:
    with CycleLock("ws", tmp_path) as owner:
        inode = owner.path.stat().st_ino
        with pytest.raises(CycleBusy):
            CycleLock("ws", tmp_path).acquire()
        assert owner.path.stat().st_ino == inode


def test_distinct_identities_do_not_collide(tmp_path: Path) -> None:
    with CycleLock("a", tmp_path), CycleLock("b", tmp_path):
        pass


def test_double_acquire_on_same_object_is_rejected(tmp_path: Path) -> None:
    with CycleLock("ws", tmp_path) as lock:
        with pytest.raises(RuntimeError):
            lock.acquire()


def test_context_requires_held_lock(tmp_path: Path) -> None:
    lock = CycleLock("ws", tmp_path)
    with pytest.raises(ValueError):
        CycleContext(lock)
    with lock:
        assert CycleContext(lock).lock is lock


def test_symlinked_local_path_shares_lock(tmp_path: Path) -> None:
    real = tmp_path / "ws"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    state = tmp_path / "state"
    env = {"ENS_STATE_DIR": str(state)}
    first = CycleLock.for_settings(_local(real), env)
    second = CycleLock.for_settings(_local(link), env)
    assert first.path == second.path
    with first:
        with pytest.raises(CycleBusy):
            second.acquire()


def test_s3_identity_is_normalized(tmp_path: Path) -> None:
    env = {"ENS_STATE_DIR": str(tmp_path)}
    assert workspace_identity(_s3("b", "/x//y/")) == "s3://b/x/y"
    assert workspace_identity(_s3("b", "")) == "s3://b"
    assert (
        CycleLock.for_settings(_s3("b", "x/y"), env).path
        == CycleLock.for_settings(_s3("b", "/x/y/"), env).path
    )
    assert (
        CycleLock.for_settings(_s3("b", "x"), env).path
        != CycleLock.for_settings(_s3("b2", "x"), env).path
    )


def test_state_dir_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_state_dir({"ENS_STATE_DIR": str(tmp_path)}) == tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_state_dir({}) == tmp_path / ".local/state/ens-evidence"


def test_flock_between_processes(tmp_path: Path) -> None:
    ready, release, queue = _SPAWN.Event(), _SPAWN.Event(), _SPAWN.Queue()
    holder = _SPAWN.Process(target=_hold, args=(str(tmp_path), ready, release))
    holder.start()
    try:
        assert ready.wait(30)
        probe = _SPAWN.Process(target=_try, args=(str(tmp_path), queue))
        probe.start()
        probe.join(30)
        assert queue.get(timeout=5) == "busy"
    finally:
        release.set()
        holder.join(30)
    after = _SPAWN.Process(target=_try, args=(str(tmp_path), queue))
    after.start()
    after.join(30)
    assert queue.get(timeout=5) == "acquired"


def test_owner_sigkill_frees_lock(tmp_path: Path) -> None:
    ready, release = _SPAWN.Event(), _SPAWN.Event()
    holder = _SPAWN.Process(target=_hold, args=(str(tmp_path), ready, release))
    holder.start()
    assert ready.wait(30)
    with pytest.raises(CycleBusy):
        CycleLock("shared", tmp_path).acquire()
    assert holder.pid is not None
    os.kill(holder.pid, signal.SIGKILL)
    holder.join(30)
    assert holder.exitcode == -signal.SIGKILL
    with CycleLock("shared", tmp_path):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="flock es POSIX")
def test_descriptor_not_inherited_by_spawned_child(tmp_path: Path) -> None:
    queue = _SPAWN.Queue()
    with CycleLock("ws", tmp_path) as lock:
        fd = lock._fd
        assert fd is not None
        assert not os.get_inheritable(fd)
        child = _SPAWN.Process(target=_probe_fd, args=(fd, queue))
        child.start()
        child.join(30)
        assert queue.get(timeout=5) == "closed"


def test_wait_acquires_when_released_midway(tmp_path: Path) -> None:
    clock = FakeClock()
    owner = CycleLock("ws", tmp_path).acquire()
    real_sleep = clock.sleep

    def release_then_sleep(seconds: float) -> None:
        owner.release()
        real_sleep(seconds)

    clock.sleep = release_then_sleep  # type: ignore[method-assign]
    waiter = CycleLock("ws", tmp_path)
    assert acquire_or_wait(waiter, clock, 10) is waiter
    assert waiter.held
    assert len(clock.sleeps) == 1
    waiter.release()


def test_wait_gives_up_at_limit_without_sleeping_for_real(tmp_path: Path) -> None:
    clock = FakeClock()
    start = clock.current
    with CycleLock("ws", tmp_path):
        with pytest.raises(CycleBusy):
            acquire_or_wait(CycleLock("ws", tmp_path), clock, 1)
    assert clock.current == start + timedelta(minutes=1)
    assert sum(clock.sleeps) == 60
    assert max(clock.sleeps) <= 5


def test_zero_wait_fails_immediately(tmp_path: Path) -> None:
    clock = FakeClock()
    with CycleLock("ws", tmp_path):
        with pytest.raises(CycleBusy):
            acquire_or_wait(CycleLock("ws", tmp_path), clock, 0)
    assert clock.sleeps == []


# --- directorio y fichero de estado ------------------------------------------------------------


def test_a_state_dir_writable_by_others_is_refused(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o777)
    with pytest.raises(PermissionError):
        CycleLock("x", state).acquire()


def test_a_symlinked_lock_file_is_not_followed(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    victim = tmp_path / "ajeno"
    victim.write_text("intacto")
    lock = CycleLock("x", state)
    lock.path.symlink_to(victim)
    with pytest.raises(OSError):
        lock.acquire()
    assert victim.read_text() == "intacto"


def test_a_private_state_dir_is_accepted(tmp_path):
    with CycleLock("x", tmp_path / "state") as lock:
        assert lock.held
