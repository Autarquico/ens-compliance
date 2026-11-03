"""Construcción de la instantánea, su proyección de deriva y la diferencia entre dos instantáneas.

`build_snapshot` es puro y determinista: con el mismo workspace y las mismas entradas congeladas
produce los mismos bytes. Se niega a emitir un texto con credenciales o datos personales (regla 6)
y nombra la ruta, nunca el valor.
"""

import re
from datetime import datetime

import yaml
from pydantic import JsonValue

from ens_evidence import __version__
from ens_evidence.applicability import declaration_status
from ens_evidence.assessment.dossier.frozen import FrozenCheckResults
from ens_evidence.assessment.dossier.methodology import METHODOLOGY_VERSION
from ens_evidence.assessment.dossier.model import (
    INTERNAL_REVIEW_NOTICE,
    RESULTS_SECTION,
    DossierSnapshot,
    SnapshotInputs,
)
from ens_evidence.assessment.dossier.privacy import (
    export_problems,
    secret_problems,
    walk_texts,
)
from ens_evidence.assessment.dossier.readonly import ReadOnlyStore
from ens_evidence.assessment.dossier.sections import (
    CHECK_EVIDENCE,
    CHECK_RUN,
    INCIDENT_RECORD,
    SectionContext,
    build_sections,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import current_catalog_version
from ens_evidence.categorization import CATEGORIZATION_KEY, open_dossier
from ens_evidence.incidents.layout import INCIDENTS_PREFIX
from ens_evidence.incidents.model import IncidentStatus
from ens_evidence.matrix import MATRIX_KEY, CheckResultsSource, ExportRejected, assess, build_export
from ens_evidence.models.enums import Category
from ens_evidence.orgdocs.keys import ANSWERS_KEY
from ens_evidence.privacy_text import has_credential
from ens_evidence.risk.models import RISK_FILES
from ens_evidence.scope.inventory import ASSETS_KEY, DEPENDENCIES_KEY, SERVICES_KEY, SYSTEM_KEY
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

TEMPORAL_INPUTS = ("generated_at", "evaluated_at", "reference_now")
EVIDENCE_SECTION = 8
TREATMENT_SECTION = 9
OUTCOME_SECTIONS = (RESULTS_SECTION, EVIDENCE_SECTION, TREATMENT_SECTION)
GENERAL = "Datos generales"
# Ficheros que edita una persona: su texto libre se revisa aunque el expediente solo cite su huella.
HAND_EDITED = frozenset(
    (
        ASSETS_KEY,
        SERVICES_KEY,
        DEPENDENCIES_KEY,
        SYSTEM_KEY,
        CATEGORIZATION_KEY,
        MEASURES_KEY,
        MATRIX_KEY,
        ANSWERS_KEY,
        *RISK_FILES,
    )
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_INCIDENT_OF_KEY = re.compile(rf"{INCIDENTS_PREFIX}/([^/]+)/")
_MISSING = object()


class DossierRejected(ValueError):
    """El expediente no se genera; `errors` dice por qué (rutas y tipos, nunca valores)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def current_inputs(now: datetime) -> SnapshotInputs:
    """Entradas con un único instante y las versiones vigentes del producto."""
    return SnapshotInputs(
        generated_at=now,
        evaluated_at=now,
        reference_now=now,
        methodology_version=METHODOLOGY_VERSION,
        product_version=__version__,
        catalog_version=current_catalog_version(load_catalog()),
    )


def build_snapshot(
    store: WorkspaceStore, inputs: SnapshotInputs, *, checks: CheckResultsSource | None = None
) -> DossierSnapshot:
    """La instantánea del workspace en `inputs.evaluated_at`.

    Lanza `CategorizationNotCurrent` sin categorización vigente y `DossierRejected` si la
    Declaración no es legible, las versiones de las entradas no son las del producto o algún texto
    exportable contiene una credencial o un dato personal. NOT_READY se genera igualmente.
    `checks` por defecto: el historial guardado congelado en `inputs.reference_now` (lo que
    reproduce una instantánea); al generar se pasa el historial vivo. El workspace se lee una vez
    por clave.
    """
    store = ReadOnlyStore(store)
    _require_reproducible(inputs)
    opened = open_dossier(store, inputs.evaluated_at)
    declaration = declaration_status(store, inputs.evaluated_at)
    if declaration.declaration is None:
        raise DossierRejected(
            [f"No hay Declaración de Aplicabilidad legible ({declaration.state.value})."]
            + list(declaration.errors)
        )
    source = checks or FrozenCheckResults(reference_now=inputs.reference_now)
    evaluation = assess(store, inputs.evaluated_at, checks=source)
    try:
        export = build_export(store, evaluation, generated_at=inputs.generated_at)
    except ExportRejected as exc:
        raise DossierRejected(exc.errors) from None
    category = opened.dossier.category
    snapshot = DossierSnapshot(
        category=category,
        notice=None if category is Category.BASICA else INTERNAL_REVIEW_NOTICE,
        inputs=inputs,
        sections=build_sections(SectionContext(store, inputs, category, declaration, export)),
    )
    leaks = _leaks(store, snapshot)
    if leaks:
        raise DossierRejected(leaks)
    return snapshot


def _require_reproducible(inputs: SnapshotInputs) -> None:
    """Las entradas describen el código que construye: si no coinciden, no se reproduce."""
    expected = {
        "methodology_version": METHODOLOGY_VERSION,
        "product_version": __version__,
        "catalog_version": current_catalog_version(load_catalog()),
    }
    errors = [
        f"inputs.{name}: no es el vigente en este producto; genera un expediente nuevo"
        for name, value in expected.items()
        if getattr(inputs, name) != value
    ]
    if errors:
        raise DossierRejected(errors)


# --- textos exportables ------------------------------------------------------------------------


def _leaks(store: WorkspaceStore, snapshot: DossierSnapshot) -> list[str]:
    document = snapshot.model_dump(mode="json")
    found = secret_problems(document)
    for section in document["sections"]:
        where = f"sección {section['number']} ({section['title']}): "
        found += export_problems(where, section["content"])
    for key in sorted({s["key"] for sec in document["sections"] for s in sec["sources"]}):
        if key in HAND_EDITED:
            found += _file_leaks(store, key)
    return sorted(set(found))


def _file_leaks(store: WorkspaceStore, key: str) -> list[str]:
    """Credenciales en un fichero que edita una persona y que el expediente solo cita por su
    huella. Los datos personales de contacto son legítimos ahí: no se exportan."""
    try:
        text = store.read_text(key)
    except (NotFound, NotText):
        return []
    if not key.endswith((".yaml", ".yml")):
        return [f"{key}: contiene una credencial"] if has_credential(text) else []
    try:
        raw = safe_load_unique(text)
    except yaml.YAMLError:
        return []
    found = secret_problems(raw, f"{key}.")
    return found + [
        f"{key}.{path}: contiene una credencial"
        for path, value in walk_texts(raw)
        if has_credential(value)
    ]


# --- deriva ------------------------------------------------------------------------------------


def drift_projection(snapshot: DossierSnapshot) -> dict[str, JsonValue]:
    """Lo que, si cambia, hace que el expediente ya no describa lo que las personas registraron.

    Conserva las entradas organizativas y las decisiones: alcance, categorización, riesgos,
    Declaración, la matriz (enlaces, revisiones y su huella), documentos, incidentes y plan de
    tratamiento, con la clave y la huella de cada fuente. Quita:
    - los incidentes abiertos y los registros de su cadena (la proyección conserva los cerrados);
      abrir o actualizar un incidente no invalida un expediente en curso (plan r2, punto 4);
    - los metadatos temporales de generación (`generated_at`, `evaluated_at`, `reference_now`),
      los instantes de las fuentes y `last_run_at`;
    - las ejecuciones de checks y las evidencias que solo son su entrada;
    - los resultados de evaluación (bloqueos, preparación, `implemented`, `counts`, estados de las
      secciones 7 a 9), que se recalculan: una ejecución nueva y equivalente no deriva, y una
      fallida tampoco, pero la evaluación actual impide declarar (lo comprueba el servicio).
    """
    document = snapshot.model_dump(mode="json")
    for name in TEMPORAL_INPUTS:
        del document["inputs"][name]
    sections = {s["number"]: s for s in document["sections"]}
    derived = {m["id"] for m in sections[EVIDENCE_SECTION]["content"]["check_inputs"]}
    for section in document["sections"]:
        section["sources"] = [
            {name: source[name] for name in ("kind", "key", "sha256")}
            for source in section["sources"]
            if source["kind"] not in (CHECK_RUN, CHECK_EVIDENCE)
        ]
        if section["number"] in OUTCOME_SECTIONS:
            del section["state"]
    del sections[EVIDENCE_SECTION]["content"]["check_inputs"]
    _drop_open_incidents(sections[EVIDENCE_SECTION])
    _drop_outcomes(sections[RESULTS_SECTION]["content"]["matrix"], derived)
    treatment = sections[TREATMENT_SECTION]["content"]
    del treatment["readiness"], treatment["open_blockers"]
    return document


def _drop_open_incidents(section: dict) -> None:
    incidents = section["content"]["incidents"]
    closed = [i for i in incidents if i["status"] == IncidentStatus.CLOSED.value]
    kept = {i["id"] for i in closed}
    section["content"]["incidents"] = closed
    section["sources"] = [
        s
        for s in section["sources"]
        if s["kind"] != INCIDENT_RECORD or _INCIDENT_OF_KEY.match(s["key"]).group(1) in kept
    ]


def _drop_outcomes(matrix: dict, derived: set[str]) -> None:
    for name in ("generated_at", "evaluated_at", "readiness", "blockers", "errors"):
        del matrix[name]
    del matrix["declaration"]["state"]
    for row in matrix["rows"]:
        for name in ("implemented", "review_current", "blockers"):
            del row[name]
        for link in [*row["technical"], *row["manual"]]:
            del link["counts"]
    sources = matrix["sources"]
    sources["evidence"] = [e for e in sources["evidence"] if e["id"] not in derived]
    for check in sources["checks"]:
        del check["last_run_at"], check["evidence_ids"]


FIELD_LABELS = {
    "content": "contenido",
    "matrix": "matriz",
    "rows": "medidas",
    "review_every_days": "periodicidad de revisión (días)",
    "owner": "responsable",
    "last_review": "última revisión",
    "technical": "checks vinculados",
    "manual": "evidencias vinculadas",
    "declaration": "Declaración de Aplicabilidad",
    "sources": "fuentes",
    "approval": "aprobación",
    "inputs": "entradas",
    "category": "categoría",
    "valid_until": "válida hasta",
}
_NOISE = "matrix_sha256"  # la huella de la matriz acompaña siempre a un cambio que ya se nombra


def diff_projection(a: DossierSnapshot, b: DossierSnapshot) -> dict[str, list[str]]:
    """Diferencias entre las proyecciones de deriva, por sección y en castellano; vacío si no hay
    deriva. Las medidas de la sección 7 se nombran por su id, no por su posición."""
    left, right = drift_projection(a), drift_projection(b)
    differences: dict[str, list[str]] = {}
    general = _diff(_general(left), _general(right), ())
    if general:
        differences[GENERAL] = general
    for before, after in zip(left["sections"], right["sections"], strict=True):
        lines = _diff(_by_source(before), _by_source(after), ())
        if len(lines) > 1:
            lines = [line for line in lines if not line.split(":")[0].endswith(_NOISE)] or lines
        if lines:
            differences[f"{before['number']}. {before['title']}"] = lines
    return differences


def _general(projection: dict) -> dict:
    return {name: value for name, value in projection.items() if name != "sections"}


def _by_source(section: dict) -> dict:
    """Las fuentes por clave («añadida», «quitada» o «cambió la huella» se leen directamente) y,
    en la sección 7, las medidas por id."""
    sources = {f"{s['kind']}:{s['key']}": s["sha256"] for s in section["sources"]}
    section = {**section, "sources": sources}
    if section["number"] == RESULTS_SECTION:
        matrix = section["content"]["matrix"]
        rows = {f"medida {row['measure_id']}": row for row in matrix["rows"]}
        section = {**section, "content": {"matrix": {**matrix, "rows": rows}}}
    return section


def _where(path: tuple[str, ...]) -> str:
    return " › ".join(FIELD_LABELS.get(part, part) for part in path)


def _diff(before: object, after: object, path: tuple[str, ...]) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            line
            for key in sorted(before.keys() | after.keys())
            for line in _diff(
                before.get(key, _MISSING), after.get(key, _MISSING), (*path, str(key))
            )
        ]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            line
            for index, (x, y) in enumerate(zip(before, after, strict=True))
            for line in _diff(x, y, (*path, f"[{index + 1}]"))
        ]
    if isinstance(before, list) and isinstance(after, list):
        return [f"{_where(path)}: {len(before)} → {len(after)} elementos"]
    if before == after:
        return []
    return [f"{_where(path)}: {_show(before)} → {_show(after)}"]


def _show(value: object) -> str:
    if value is _MISSING:
        return "(ausente)"
    text = str(value)
    if _SHA256.fullmatch(text):
        return text[:12]
    return text if len(text) <= 60 else text[:57] + "..."
