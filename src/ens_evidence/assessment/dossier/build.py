"""Construcción de la instantánea, su proyección de deriva y la diferencia entre dos instantáneas.

`build_snapshot` es puro y determinista: con el mismo workspace y las mismas entradas congeladas
produce los mismos bytes. Se niega a emitir un texto con credenciales o datos personales (regla 6)
y nombra la ruta, nunca el valor.
"""

import re
from collections.abc import Iterator
from datetime import datetime

import yaml
from pydantic import JsonValue

from ens_evidence import __version__
from ens_evidence.applicability import declaration_status
from ens_evidence.assessment.dossier.methodology import METHODOLOGY_VERSION
from ens_evidence.assessment.dossier.model import (
    INTERNAL_REVIEW_NOTICE,
    DossierSnapshot,
    SnapshotInputs,
)
from ens_evidence.assessment.dossier.sections import (
    CHECK_EVIDENCE,
    CHECK_RUN,
    SectionContext,
    build_sections,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import current_catalog_version
from ens_evidence.categorization import CATEGORIZATION_KEY, open_dossier
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import MATRIX_KEY, CheckResultsSource, ExportRejected, assess, build_export
from ens_evidence.models.enums import Category
from ens_evidence.orgdocs.keys import ANSWERS_KEY
from ens_evidence.privacy_text import has_credential, text_problems
from ens_evidence.risk.models import RISK_FILES
from ens_evidence.scope.inventory import ASSETS_KEY, DEPENDENCIES_KEY, SERVICES_KEY, SYSTEM_KEY
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

TEMPORAL_INPUTS = ("generated_at", "evaluated_at", "reference_now")
RESULTS_SECTION = 7
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
    `checks` por defecto: las ejecuciones guardadas, con `inputs.reference_now` como reloj.
    """
    _require_reproducible(inputs)
    opened = open_dossier(store, inputs.evaluated_at)
    declaration = declaration_status(store, inputs.evaluated_at)
    if declaration.declaration is None:
        raise DossierRejected(
            [f"No hay Declaración de Aplicabilidad legible ({declaration.state.value})."]
            + list(declaration.errors)
        )
    source = checks or StoredCheckResults(reference_now=inputs.reference_now)
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


def walk_texts(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    """(ruta, texto) de cada cadena; las rutas de lista llevan índice."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from walk_texts(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_texts(child, f"{path}[{index}]")


def _is_generated(path: str, text: str) -> bool:
    """Una huella sha256 o la clave de un fichero del workspace no son texto libre."""
    field = path.rsplit(".", 1)[-1]
    return bool(_SHA256.fullmatch(text)) or field == "key" or field.endswith("_key")


def text_leaks(where: str, value: object) -> list[str]:
    """Credenciales y datos personales en el texto libre; solo errores, no avisos."""
    return [
        f"{where}{path}: {problem}"
        for path, text in walk_texts(value)
        if not _is_generated(path, text)
        for problem in text_problems(text)[0]
    ]


def _leaks(store: WorkspaceStore, snapshot: DossierSnapshot) -> list[str]:
    document = snapshot.model_dump(mode="json")
    found = [f"instantánea.{path}: parece un secreto" for path in find_secrets(document)]
    for section in document["sections"]:
        where = f"sección {section['number']} ({section['title']}): "
        found += text_leaks(where, section["content"])
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
    found = [f"{key}.{path}: parece un secreto" for path in find_secrets(raw)]
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
    _drop_outcomes(sections[RESULTS_SECTION]["content"]["matrix"], derived)
    treatment = sections[TREATMENT_SECTION]["content"]
    del treatment["readiness"], treatment["open_blockers"]
    return document


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


def diff_projection(a: DossierSnapshot, b: DossierSnapshot) -> dict[str, list[str]]:
    """Diferencias entre las proyecciones de deriva, por sección; vacío si no hay deriva."""
    left, right = drift_projection(a), drift_projection(b)
    differences: dict[str, list[str]] = {}
    general = _diff(_general(left), _general(right), "")
    if general:
        differences[GENERAL] = general
    for before, after in zip(left["sections"], right["sections"], strict=True):
        lines = _diff(_by_source(before), _by_source(after), "")
        if lines:
            differences[f"{before['number']}. {before['title']}"] = lines
    return differences


def _general(projection: dict) -> dict:
    return {name: value for name, value in projection.items() if name != "sections"}


def _by_source(section: dict) -> dict:
    """Las fuentes por clave: «añadida», «quitada» o «cambió la huella» se leen directamente."""
    sources = {f"{s['kind']}:{s['key']}": s["sha256"] for s in section["sources"]}
    return {**section, "sources": sources}


def _diff(before: object, after: object, path: str) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            line
            for key in sorted(before.keys() | after.keys())
            for line in _diff(
                before.get(key, _MISSING),
                after.get(key, _MISSING),
                f"{path}.{key}" if path else key,
            )
        ]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            line
            for index, (x, y) in enumerate(zip(before, after, strict=True))
            for line in _diff(x, y, f"{path}[{index}]")
        ]
    if isinstance(before, list) and isinstance(after, list):
        return [f"{path}: {len(before)} → {len(after)} elementos"]
    if before == after:
        return []
    return [f"{path}: {_show(before)} → {_show(after)}"]


def _show(value: object) -> str:
    if value is _MISSING:
        return "(ausente)"
    text = str(value)
    if _SHA256.fullmatch(text):
        return text[:12]
    return text if len(text) <= 60 else text[:57] + "..."
