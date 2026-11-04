"""Construye el workspace de un perfil del banco con las APIs del producto y un reloj fijo.

Cada paso hace lo que haría una persona: editar los YAML del alcance, aprobar con la función del
producto, decidir la aplicabilidad e importar evidencias. Nada se escribe a mano en `evidence/`
ni en `assessment/`. Los instantes salen de `clock.start` (`now=` explícito) y los ids de
evidencia de una fuente sembrada: el mismo perfil y la misma semilla dan los mismos bytes.

Los pasos están en `STEPS`, en orden; los que faltan (org-docs, matriz e instantánea del
expediente) se añaden al final de esa tupla.
"""

import hashlib
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from ens_evidence.applicability import approve_declaration, propose_applicability
from ens_evidence.applicability.context import Context, load_context
from ens_evidence.approvals import Rejected
from ens_evidence.benchmark.cases import BenchError, load_profile, profile_files_dir
from ens_evidence.benchmark.model import (
    ApplicabilityDecision,
    EvidenceSource,
    Profile,
    ProfileEvidence,
)
from ens_evidence.catalog.controls import init_measures, load_measures, save_measures
from ens_evidence.categorization import (
    CategorizationNotCurrent,
    approve_categorization,
    categorization_status,
)
from ens_evidence.evidence.importer import (
    EvidenceImportError,
    IdFactory,
    import_collected,
    import_manual,
)
from ens_evidence.models.contracts import CollectionResult, EvidenceManifest
from ens_evidence.models.enums import Applicability, Role
from ens_evidence.models.readiness import Measure
from ens_evidence.scope import (
    ScopeApprovalRejected,
    ScopeNotApproved,
    approve_scope,
    load_inventory,
)
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import LocalStore

SCOPE_DIR = "scope"
COLLECTOR_VERSION = "benchmark"
"""Versión declarada de las recogidas del banco: el JSON es un fixture, no salió de un collector."""
TICK = timedelta(minutes=1)


def seeded_ids(seed: str) -> IdFactory:
    """Ids de evidencia con el formato del producto, derivados de `seed` y de un contador."""
    counter = itertools.count()

    def next_id(now: datetime) -> str:
        digest = hashlib.sha256(f"{seed}:{next(counter)}".encode()).hexdigest()
        return f"EV-{now:%Y%m%d}-{digest[:8]}"

    return next_id


@dataclass
class Build:
    """Estado de una construcción: el workspace, su reloj y los ids reales de cada alias."""

    profile: Profile
    files: Path
    store: LocalStore
    id_factory: IdFactory
    evidence_ids: dict[str, str] = field(default_factory=dict)
    ticks: int = 0

    def tick(self) -> datetime:
        """El siguiente instante del reloj fijo, un minuto después del anterior."""
        moment = self.profile.clock.start + self.ticks * TICK
        self.ticks += 1
        return moment

    @property
    def now(self) -> datetime:
        """El último instante usado (o `clock.start` si aún no se usó ninguno)."""
        return self.profile.clock.start + max(self.ticks - 1, 0) * TICK

    def signer(self, role: Role) -> str:
        """Nombre de la primera persona del perfil con `role`."""
        for person in self.profile.people:
            if role in person.roles:
                return person.name
        raise BenchError([f"nadie tiene el rol {role.value}"])


Step = Callable[[Build], None]


def _scope(build: Build) -> None:
    """Los YAML del alcance y su categorización, como los edita una persona; y su aprobación."""
    source = build.files / SCOPE_DIR
    if not source.is_dir():
        raise BenchError([f"falta el directorio files/{SCOPE_DIR}/"])
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        key = f"{SCOPE_DIR}/{path.relative_to(source).as_posix()}"
        build.store.write_bytes(key, path.read_bytes())
    approve_scope(build.store, build.signer(Role.SYSTEM_OWNER), Role.SYSTEM_OWNER, build.tick())


def _categorization(build: Build) -> None:
    now = build.tick()
    approve_categorization(build.store, build.signer(Role.SECURITY_OFFICER), now)
    computation = categorization_status(build.store, now).computation
    assert computation is not None
    computed = {item.dimension: item.level for item in computation.levels if item.level}
    if computed != build.profile.categorization:
        raise BenchError(
            [
                f"los niveles calculados ({_levels(computed)}) no son los del perfil "
                f"({_levels(build.profile.categorization)})"
            ]
        )


def _levels(levels: dict) -> str:
    return ", ".join(f"{d.value}={levels[d].value}" for d in sorted(levels)) or "ninguno"


def _applicability(build: Build) -> None:
    """Propuesta del producto, decisiones del perfil por encima y firma de la Declaración."""
    init_measures(build.store)
    now = build.tick()
    propose_applicability(build.store, now)
    context = load_context(build.store, now)
    document = load_measures(build.store)
    assert document is not None
    decisions = {d.measure: d for d in build.profile.applicability}
    measures = [_decided(m, decisions.get(m.id), context, build.profile) for m in document.measures]
    if undecided := [m.id for m in measures if m.applicability is Applicability.UNDETERMINED]:
        raise BenchError(
            [
                "medidas sin decidir, ni en `applicability` ni con "
                f"`not_required_justification`: {', '.join(undecided)}"
            ]
        )
    save_measures(build.store, document.model_copy(update={"measures": measures}))
    approve_declaration(build.store, build.signer(Role.SECURITY_OFFICER), build.tick())


def _decided(
    measure: Measure, decision: ApplicabilityDecision | None, context: Context, profile: Profile
) -> Measure:
    """La medida tras la decisión humana del perfil; sin decisión, la propuesta queda intacta."""
    update: dict = {}
    if decision is not None:
        update = {
            "applicability": (
                Applicability.APPLICABLE if decision.applicable else Applicability.NOT_APPLICABLE
            ),
            "justification": decision.justification,
        }
    elif measure.applicability is Applicability.UNDETERMINED and profile.not_required_justification:
        update = {
            "applicability": Applicability.NOT_APPLICABLE,
            "justification": profile.not_required_justification,
        }
    if update:
        update |= {"proposed": False, "selection": context.basis(measure.id)}
    if measure.id in profile.reinforcements:
        update["chosen_reinforcements"] = profile.reinforcements[measure.id]
    return measure.model_copy(update=update) if update else measure


def _evidence(build: Build) -> None:
    for evidence in build.profile.evidence:
        path = build.files / evidence.file
        if not path.is_file():
            raise BenchError([f"{evidence.alias}: falta files/{evidence.file}"])
        importer = _manual if evidence.source is EvidenceSource.MANUAL else _collected
        build.evidence_ids[evidence.alias] = importer(build, evidence, path.read_bytes()).id


def _manual(build: Build, evidence: ProfileEvidence, data: bytes) -> EvidenceManifest:
    inventory = load_inventory(build.store).inventory
    assert inventory is not None  # el alcance ya está aprobado
    if evidence.asset is None or evidence.asset not in inventory.system.included_assets:
        shown = evidence.asset or "sin `asset`"
        raise BenchError(
            [f"{evidence.alias}: una evidencia manual nombra un activo del alcance ({shown})"]
        )
    return import_manual(
        build.store,
        data,
        asset_id=evidence.asset,
        submitted_by=build.signer(Role.SECURITY_OFFICER),
        now=build.tick(),
        valid_until=evidence.valid_until,
        id_factory=build.id_factory,
    )


def _collected(build: Build, evidence: ProfileEvidence, data: bytes) -> EvidenceManifest:
    if evidence.valid_until is not None:
        raise BenchError(
            [f"{evidence.alias}: una recogida no lleva `valid_until`; caduca por su check"]
        )
    try:
        result = CollectionResult.model_validate_json(data)
    except ValidationError as exc:
        raise BenchError(describe_validation(f"files/{evidence.file}", exc)) from None
    if evidence.asset is not None and evidence.asset != result.asset_id:
        raise BenchError([f"{evidence.alias}: `asset` ({evidence.asset}) no es el de la recogida"])
    return import_collected(
        build.store,
        result,
        collector_version=COLLECTOR_VERSION,
        now=build.tick(),
        id_factory=build.id_factory,
    )


STEPS: tuple[tuple[str, Step], ...] = (
    ("alcance", _scope),
    ("categorización", _categorization),
    ("aplicabilidad", _applicability),
    ("evidencias", _evidence),
)


def build_profile(
    profile: Profile,
    files: Path,
    destination: Path,
    *,
    seed: str | None = None,
    steps: tuple[tuple[str, Step], ...] = STEPS,
) -> Build:
    """Construye el workspace de `profile` en `destination` (nuevo o vacío).

    `seed` siembra los ids de evidencia (por defecto, el id del perfil). Lanza `BenchError` con
    el paso y el motivo si el perfil no se puede construir.
    """
    if destination.exists() and any(destination.iterdir()):
        raise BenchError([f"perfil {profile.id}: el destino no está vacío"])
    destination.mkdir(parents=True, exist_ok=True)
    build = Build(profile, files, LocalStore(destination), seeded_ids(seed or profile.id))
    for name, step in steps:
        try:
            step(build)
        except BenchError as exc:
            raise _failed(profile, name, exc.problems) from None
        except (Rejected, ScopeApprovalRejected, EvidenceImportError) as exc:
            errors = getattr(exc, "errors", None) or [str(exc)]
            raise _failed(profile, name, errors) from None
        except ScopeNotApproved as exc:
            state = exc.status.state.value
            raise _failed(profile, name, [f"el alcance no está aprobado ({state})"]) from None
        except CategorizationNotCurrent as exc:
            state = exc.status.state.value
            problems = [f"la categorización no está vigente ({state})", *exc.status.errors]
            raise _failed(profile, name, problems) from None
        except ValidationError as exc:
            raise _failed(profile, name, describe_validation(name, exc)) from None
    return build


def build_bench_profile(
    root: Path, profile_id: str, destination: Path, *, seed: str | None = None
) -> Build:
    """Carga `profiles/<profile_id>` de un banco y lo construye."""
    profile = load_profile(root, profile_id)
    return build_profile(profile, profile_files_dir(root, profile_id), destination, seed=seed)


def _failed(profile: Profile, step: str, problems: list[str]) -> BenchError:
    return BenchError([f"perfil {profile.id} › {step}: {p}" for p in problems])
