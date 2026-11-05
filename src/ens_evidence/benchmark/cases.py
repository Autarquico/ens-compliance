"""Carga y validación estática del banco: perfiles, casos y grabaciones.

Disposición (plan, «Formato de caso»): `profiles/<perfil>/{profile.yaml,files/}`,
`cases/<partición>/<caso>.yaml`, `cases/<partición>/files/<caso>/`, `recordings/…`.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.benchmark.fictional import value_problems
from ens_evidence.benchmark.model import (
    Case,
    CollectedOverride,
    Compensate,
    DeclareNotApplicable,
    DefectLayer,
    ExpireEvidence,
    Partition,
    Profile,
    Recording,
    ReplaceEvidenceText,
    TargetKind,
    UnlinkDocument,
    UnlinkEvidence,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.yaml_unique import safe_load_unique

PROFILE_FILE = "profile.yaml"
TEXT_SUFFIXES = frozenset({".txt", ".md"})
STRUCTURED_SUFFIXES = frozenset({".yaml", ".yml", ".json"})
_SPACES = re.compile(r"\s+")


class BenchError(ValueError):
    """Un fichero del banco no se puede leer o no cumple su contrato."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --- carga -----------------------------------------------------------------------------------


def profiles_dir(root: Path) -> Path:
    return root / "profiles"


def cases_dir(root: Path, partition: Partition) -> Path:
    return root / "cases" / partition.value


def case_files_dir(root: Path, case: Case) -> Path:
    return cases_dir(root, case.partition) / "files" / case.id


def profile_files_dir(root: Path, profile_id: str) -> Path:
    return profiles_dir(root) / profile_id / "files"


def case_paths(root: Path, partition: Partition) -> list[Path]:
    directory = cases_dir(root, partition)
    return sorted(directory.glob("*.yaml")) if directory.is_dir() else []


def load_profile(root: Path, profile_id: str) -> Profile:
    path = profiles_dir(root) / profile_id / PROFILE_FILE
    profile = _load_model(path, Profile, _parse_yaml, root)
    if profile.id != profile_id:
        raise BenchError([f"{_shown(path, root)}: el id no coincide con su directorio"])
    return profile


def load_case(path: Path, root: Path | None = None) -> Case:
    case = _load_model(path, Case, _parse_yaml, root)
    if case.id != path.stem:
        raise BenchError([f"{_shown(path, root)}: el id no coincide con el nombre del fichero"])
    if path.parent.name != case.partition.value:
        raise BenchError([f"{_shown(path, root)}: la partición no coincide con su directorio"])
    return case


def load_recording(path: Path, root: Path | None = None) -> Recording:
    return _load_model(path, Recording, json.loads, root)


def _parse_yaml(text: str) -> Any:
    return safe_load_unique(text)


def _load_model[M: BaseModel](path: Path, model: type[M], parse: Any, root: Path | None) -> M:
    shown = _shown(path, root)
    try:
        raw = parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BenchError([f"{shown}: no existe"]) from None
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise BenchError([f"{shown}: no se puede leer ({type(exc).__name__})"]) from None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise BenchError(describe_validation(shown, exc, raw)) from None


def _shown(path: Path, root: Path | None) -> str:
    if root is not None and path.is_relative_to(root):
        return path.relative_to(root).as_posix()
    return path.name


# --- citas del anexo II ----------------------------------------------------------------------


@cache
def clause_texts() -> dict[str, dict[str, str]]:
    """Por medida, el texto de cada cláusula del catálogo empaquetado por su id. Los párrafos sin
    etiqueta del anexo se citan con el id de la medida (cuerpo) o del refuerzo (`<medida>.rN`)."""
    texts: dict[str, dict[str, str]] = {}
    for measure in load_catalog().measures:
        clauses: dict[str, list[str]] = {}
        for clause in measure.base:
            clauses.setdefault(clause.id or measure.id, []).append(clause.text)
        for reinforcement in measure.reinforcements:
            unlabeled = f"{measure.id}.{reinforcement.id.lower()}"
            for clause in reinforcement.clauses:
                clauses.setdefault(clause.id or unlabeled, []).append(clause.text)
        texts[measure.id] = {key: "\n".join(parts) for key, parts in clauses.items()}
    return texts


def normalize_spaces(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def requirement_problem(measure: str, clause: str, quote: str) -> str | None:
    """Por qué la cita no vale: medida o cláusula inexistentes, o texto no literal."""
    clauses = clause_texts().get(measure)
    if clauses is None:
        return f"la medida {measure} no existe en el catálogo"
    if clause not in clauses:
        return f"la cláusula {clause} no existe en la medida {measure}"
    if normalize_spaces(quote) not in normalize_spaces(clauses[clause]):
        return f"la cita no es literal del texto de {clause}"
    return None


# --- validación ------------------------------------------------------------------------------


def inside_git_repo(path: Path) -> bool:
    current = path.resolve()
    return any((parent / ".git").exists() for parent in (current, *current.parents))


def validate_bench(root: Path, partition: Partition) -> list[str]:
    """Problemas del banco para una partición: esquema, citas, ids, datos ficticios. De la otra
    partición solo se miran los nombres de fichero (ids), nunca su contenido."""
    problems: list[str] = []
    profiles = _validated_profiles(root, problems)
    cases: list[Case] = []
    for path in case_paths(root, partition):
        try:
            cases.append(load_case(path, root))
        except BenchError as exc:
            problems += exc.problems
    problems += _disjoint(root, partition)
    in_repo = inside_git_repo(root)
    for case in cases:
        problems += [f"{case.key}: {p}" for p in _case_problems(root, case, profiles, in_repo)]
        problems += _scan_file(cases_dir(root, partition) / f"{case.id}.yaml", root)
        problems += _scan_tree(case_files_dir(root, case), root)
    return problems


def recording_problems(recording: Recording) -> list[str]:
    """Datos detectables en una grabación: metadatos y respuesta, decodificados y recorridos
    entero (un escape Unicode no esconde un secreto). Las huellas y los instantes no se miran:
    son hexadecimales o ISO y la heurística de teléfonos los confundiría. El destino lo compone
    el producto (URL base o CLI con su sha256): en él solo se buscan secretos."""
    shown = recording.model_dump(
        mode="json", include={"model", "temperature_note", "recorded_by", "tool_version"}
    )
    problems = value_problems(shown)
    problems += [f"{p}: parece un secreto" for p in find_secrets(recording.destination, "destino")]
    for repetition in recording.repetitions:
        if repetition.response is None:
            continue
        where = f"repetitions[{repetition.n}].response"
        try:
            decoded: Any = json.loads(repetition.response)
        except ValueError:
            decoded = repetition.response
        problems += value_problems(decoded, where)
    return problems


def _validated_profiles(root: Path, problems: list[str]) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    directory = profiles_dir(root)
    if not directory.is_dir():
        return profiles
    measures = clause_texts()
    for path in sorted(directory.glob(f"*/{PROFILE_FILE}")):
        profile_id = path.parent.name
        try:
            profile = load_profile(root, profile_id)
        except BenchError as exc:
            problems += exc.problems
            continue
        profiles[profile_id] = profile
        problems += [
            f"perfil {profile_id}: la medida {m} no existe en el catálogo"
            for m in sorted(profile.measures() - measures.keys())
        ]
        files = profile_files_dir(root, profile_id)
        problems += [
            f"perfil {profile_id}: falta el fichero de la evidencia {e.alias}"
            for e in profile.evidence
            if not (files / e.file).is_file()
        ]
        problems += _scan_file(path, root)
        problems += _scan_tree(files, root)
    return profiles


def _disjoint(root: Path, partition: Partition) -> list[str]:
    other = Partition.HOLDOUT if partition is Partition.DEV else Partition.DEV
    mine = {p.stem for p in case_paths(root, partition)}
    theirs = {p.stem for p in case_paths(root, other)}
    return [f"caso {c}: está en las dos particiones" for c in sorted(mine & theirs)]


def _case_problems(
    root: Path, case: Case, profiles: dict[str, Profile], in_repo: bool
) -> list[str]:
    problems: list[str] = []
    if case.workspace is not None and in_repo:
        problems.append("una base `workspace:` no se admite con los casos dentro de un repo git")
    criteria = {c.id for c in load_pack().criteria}
    for defect in case.defects:
        requirement = defect.requirement
        problem = requirement_problem(requirement.measure, requirement.clause, requirement.quote)
        if problem:
            problems.append(f"defecto {defect.id}: {problem}")
        if defect.layer is DefectLayer.JUDGMENT and defect.expected_criterion not in criteria:
            problems.append(f"defecto {defect.id}: criterio desconocido en el pack")
    problems += _mutation_measure_problems(case)
    profile = profiles.get(case.profile) if case.profile else None
    if case.profile is not None and profile is None:
        problems.append(f"el perfil {case.profile} no existe o no es válido")
    if profile is not None:
        problems += _reference_problems(root, case, profile)
    return problems


def _mutation_measure_problems(case: Case) -> list[str]:
    measures = clause_texts()
    used = [
        m.measure
        for m in case.mutations
        if isinstance(m, UnlinkEvidence | UnlinkDocument | DeclareNotApplicable | Compensate)
    ]
    return [f"mutación: la medida {m} no existe en el catálogo" for m in used if m not in measures]


def _reference_problems(root: Path, case: Case, profile: Profile) -> list[str]:
    aliases = {e.alias for e in profile.evidence}
    documents = {d.ref for d in profile.documents}
    problems = [
        f"defecto {d.id}: la evidencia {d.target.alias} no está en el perfil"
        for d in case.defects
        if d.target.kind is TargetKind.EVIDENCE and d.target.alias not in aliases
    ]
    problems += [
        f"defecto {d.id}: el documento {d.target.ref} no está en el perfil"
        for d in case.defects
        if d.target.kind is TargetKind.DOCUMENT and d.target.ref not in documents
    ]
    files = case_files_dir(root, case)
    for index, mutation in enumerate(case.mutations):
        where = f"mutations[{index}] ({mutation.op})"
        alias = getattr(mutation, "alias", None)
        if alias is not None and alias not in aliases:
            problems.append(f"{where}: la evidencia {alias} no está en el perfil")
        if isinstance(mutation, UnlinkDocument) and mutation.ref not in documents:
            problems.append(f"{where}: el documento {mutation.ref} no está en el perfil")
        if isinstance(mutation, ReplaceEvidenceText | CollectedOverride):
            if not (files / mutation.file).is_file():
                problems.append(f"{where}: falta el fichero {mutation.file}")
        if isinstance(mutation, ExpireEvidence) and mutation.valid_until >= profile.clock.start:
            problems.append(f"{where}: `valid_until` debe quedar antes del reloj del perfil")
    return problems


def _scan_tree(directory: Path, root: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return [
        p for path in sorted(directory.rglob("*")) if path.is_file() for p in _scan_file(path, root)
    ]


def _scan_file(path: Path, root: Path) -> list[str]:
    shown = _shown(path, root)
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES | STRUCTURED_SUFFIXES:
        return [f"{shown}: tipo de fichero no admitido en el banco (.txt, .md, .yaml, .json)"]
    try:
        text = path.read_text(encoding="utf-8")
        value: Any = text
        if suffix == ".json":
            value = json.loads(text)
        elif suffix in STRUCTURED_SUFFIXES:
            value = safe_load_unique(text)
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        return [f"{shown}: no se puede leer ({type(exc).__name__})"]
    return [f"{shown} › {p}" for p in value_problems(value)]
