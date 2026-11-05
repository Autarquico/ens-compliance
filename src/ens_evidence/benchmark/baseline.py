"""Línea base de la partición de desarrollo: inventario de casos y defectos, lectura (fichero,
árbol de trabajo o una ref de git) y escritura con un diff resumido.

La puerta compara con la línea base de la rama base; las retiradas (`retired`) salen siempre
del fichero del árbol de trabajo, que es la migración que anota el PR (plan, crítica 2).
"""

import hashlib
import json
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from ens_evidence.benchmark.cases import case_files_dir, read_bench_bytes
from ens_evidence.benchmark.model import Baseline, BaselineEntry, Retired
from ens_evidence.benchmark.recordings import BenchCase
from ens_evidence.preaudit.payload import canonical_json
from ens_evidence.scope.messages import describe_validation

BASELINE_FILE = "baseline/dev.json"
BASELINE_FORMAT = "ens-benchmark-baseline/1"


class BaselineUnreadable(Exception):
    """La línea base pedida no se puede leer: fichero roto, ref inexistente o git ausente."""


def _sha256(data: object) -> str:
    return hashlib.sha256(canonical_json(data)).hexdigest()


def inventory(root: Path, cases: Sequence[BenchCase]) -> dict[str, str]:
    """La huella de cada caso (sin sus defectos, con sus ficheros) y de cada defecto. Cambiar o
    quitar cualquiera exige una entrada `retired`."""
    found: dict[str, str] = {}
    for bench in cases:
        case = bench.case
        files = case_files_dir(root, case)
        contents = sorted(
            (
                p.relative_to(files).as_posix(),
                hashlib.sha256(read_bench_bytes(p, files)).hexdigest(),
            )
            for p in (files.rglob("*") if files.is_dir() else ())
            if p.is_file()
        )
        found[case.key] = _sha256(
            {"case": case.model_dump(mode="json", exclude={"defects"}), "files": contents}
        )
        for defect in case.defects:
            found[f"{case.key}#{defect.id}"] = _sha256(defect.model_dump(mode="json"))
    return found


def empty_baseline(now: datetime, pack_sha256: str) -> Baseline:
    return Baseline(format=BASELINE_FORMAT, written_at=now, pack_sha256=pack_sha256)


def parse_baseline(text: str, shown: str) -> Baseline:
    try:
        return Baseline.model_validate(json.loads(text))
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            raise BaselineUnreadable("; ".join(describe_validation(shown, exc))) from None
        raise BaselineUnreadable(f"{shown}: no es JSON") from None


def read_file(path: Path) -> Baseline | None:
    """La línea base de `path`; None si no existe."""
    if not path.is_file():
        return None
    return parse_baseline(path.read_text(encoding="utf-8"), path.as_posix())


def working_tree_retired(root: Path) -> list[Retired]:
    """Las retiradas que anota el PR, en el fichero del árbol de trabajo."""
    found = read_file(root / BASELINE_FILE)
    return list(found.retired) if found else []


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise BaselineUnreadable(f"no se puede ejecutar git ({type(exc).__name__})") from None


def read_ref(root: Path, ref: str) -> Baseline | None:
    """La línea base en `ref` (`git show <ref>:<banco>/baseline/dev.json`). None si la ref no
    la tiene: no es un error, la línea base está vacía. `BaselineUnreadable` si la ref no
    existe."""
    if ref.startswith("-") or not ref.strip():
        raise BaselineUnreadable(f"ref no válida: {ref!r}")
    if _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
        raise BaselineUnreadable(f"la ref {ref} no existe en el repositorio")
    prefix = _git(root, "rev-parse", "--show-prefix")
    if prefix.returncode:
        raise BaselineUnreadable(f"{root} no está en un repositorio git")
    spec = f"{ref}:{prefix.stdout.strip()}{BASELINE_FILE}"
    if _git(root, "cat-file", "-e", spec).returncode:
        return None
    shown = _git(root, "show", spec)
    if shown.returncode:
        raise BaselineUnreadable(f"no se puede leer {spec}")
    return parse_baseline(shown.stdout, spec)


def build_baseline(
    now: datetime,
    pack_sha256: str,
    found: dict[str, str],
    entries: Sequence[BaselineEntry],
    retired: Sequence[Retired],
) -> Baseline:
    return Baseline(
        format=BASELINE_FORMAT,
        written_at=now,
        pack_sha256=pack_sha256,
        inventory=dict(sorted(found.items())),
        entries=sorted(entries, key=lambda e: (e.provider.value, e.model)),
        retired=list(retired),
    )


def write_baseline(root: Path, baseline: Baseline) -> Path:
    path = root / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline.model_dump(mode="json"), ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def diff(previous: Baseline, current: Baseline) -> list[str]:
    """Diff resumido: inventario, pares y, por par, defectos ganados o perdidos y tasas."""
    lines = []
    old, new = previous.inventory, current.inventory
    for label, keys in (
        ("altas", sorted(new.keys() - old.keys())),
        ("bajas", sorted(old.keys() - new.keys())),
        ("cambian", sorted(k for k in new.keys() & old.keys() if new[k] != old[k])),
    ):
        if keys:
            lines.append(f"inventario, {label}: {', '.join(keys)}")
    before = {f"{e.provider}/{e.model}": e for e in previous.entries}
    after = {f"{e.provider}/{e.model}": e for e in current.entries}
    lines += [f"par nuevo: {p}" for p in sorted(after.keys() - before.keys())]
    lines += [f"par que sale: {p}" for p in sorted(before.keys() - after.keys())]
    for pair in sorted(after.keys() & before.keys()):
        was, now = set(before[pair].detected), set(after[pair].detected)
        if gained := sorted(now - was):
            lines.append(f"{pair}: detecta además {', '.join(gained)}")
        if lost := sorted(was - now):
            lines.append(f"{pair}: deja de detectar {', '.join(lost)}")
        for name, attr in (("A", "false_alarms"), ("B", "correct_with_false_alarms")):
            a, b = getattr(before[pair].totals, attr), getattr(after[pair].totals, attr)
            if a.value != b.value:
                lines.append(f"{pair}: tasa {name} {a.value * 100:.1f} % → {b.value * 100:.1f} %")
    if previous.pack_sha256 != current.pack_sha256:
        lines.append(f"pack: {previous.pack_sha256[:12]} → {current.pack_sha256[:12]}")
    return lines or ["sin cambios frente a la línea base anterior"]
