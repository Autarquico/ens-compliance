"""Validación de la aplicabilidad de `controls/measures.yaml` contra el contexto vigente."""

from dataclasses import dataclass, field
from datetime import datetime

from ens_evidence.applicability.absent import Exclusion, absent_asset_errors, absent_asset_warnings
from ens_evidence.applicability.context import Context, load_context
from ens_evidence.applicability.selection import is_own_proposal
from ens_evidence.approvals import Rejected, load_model
from ens_evidence.catalog.controls import MeasuresFile, MeasuresFileError, load_measures
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import Measure
from ens_evidence.risk.models import REGISTER_KEY, RegisterFile
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.messages import SEPARATOR
from ens_evidence.scope.models import Asset
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.workspace import WorkspaceStore

INIT_HINT = "ejecuta controls init"


class ApplicabilityRejected(Rejected):
    """Motivos en `errors`."""


@dataclass(frozen=True)
class Validation:
    context: Context
    measures: MeasuresFile | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exclusions: list[Exclusion] = field(default_factory=list)


def load_checked_measures(store: WorkspaceStore, context: Context) -> MeasuresFile:
    """El fichero de medidas, alineado con el catálogo vigente; `ApplicabilityRejected` si no."""
    try:
        document = load_measures(store)
    except MeasuresFileError as exc:
        raise ApplicabilityRejected(exc.errors) from None
    if document is None:
        raise ApplicabilityRejected([f"{MEASURES_KEY}: no existe; {INIT_HINT}"])
    known = {m.id for m in context.catalog.measures}
    present = {m.id for m in document.measures}
    errors = [
        f"{MEASURES_KEY}{SEPARATOR}{i}: no está en el catálogo; bórrala o corrige el id"
        for i in sorted(present - known)
    ] + [
        f"{MEASURES_KEY}{SEPARATOR}{i}: falta la medida del catálogo; {INIT_HINT}"
        for i in sorted(known - present)
    ]
    if document.catalog_version != context.version:
        errors.append(f"{MEASURES_KEY}: la versión del catálogo no es la vigente; {INIT_HINT}")
    if errors:
        raise ApplicabilityRejected(errors)
    return document


def validate_applicability(store: WorkspaceStore, now: datetime) -> Validation:
    """Errores de aplicabilidad; sin contexto vigente lanza el error del alcance o categoría."""
    context = load_context(store, now)
    try:
        document = load_checked_measures(store, context)
    except ApplicabilityRejected as exc:
        return Validation(context, None, exc.errors)
    assets_file, _ = load_assets(store)
    assets = assets_file.assets if assets_file else []
    errors = [e for m in document.measures for e in _measure_errors(m, context, assets)]
    risk_errors, warnings = safeguard_findings(store, document)
    warnings = [*warnings, *absent_asset_warnings(document.measures, assets)]
    return Validation(
        context, document, [*errors, *risk_errors], warnings, _exclusions(document, context)
    )


def _exclusions(document: MeasuresFile, context: Context) -> list[Exclusion]:
    names = {card.id: card.name for card in context.catalog.measures}
    return [
        Exclusion(m.id, names[m.id], m.absent_assets.asset_type, m.absent_assets.justification)
        for m in document.measures
        if m.absent_assets is not None
    ]


def safeguard_findings(
    store: WorkspaceStore, document: MeasuresFile
) -> tuple[list[str], list[str]]:
    """Toda salvaguarda de `risk/register.yaml` debe estar seleccionada (art. 28.1.c)."""
    if not store.exists(REGISTER_KEY):
        return [], [f"{REGISTER_KEY}: no existe; no se comprobaron las salvaguardas de los riesgos"]
    errors: list[str] = []
    register = load_model(store, REGISTER_KEY, RegisterFile, errors)
    if register is None:
        return errors, []
    selected = {m.id for m in document.measures if m.applicability is Applicability.APPLICABLE}
    cited: dict[str, list[str]] = {}
    for risk in register.risks:
        for measure_id in risk.safeguards:
            if measure_id not in selected:
                cited.setdefault(measure_id, []).append(risk.id)
    return [
        f"{MEASURES_KEY}{SEPARATOR}{measure_id}: es salvaguarda de {', '.join(risks)} en "
        f"{REGISTER_KEY} y no está seleccionada; márcala APPLICABLE o cambia el tratamiento"
        for measure_id, risks in sorted(cited.items())
    ], []


def _measure_errors(measure: Measure, context: Context, assets: list[Asset]) -> list[str]:
    selection = context.selections[measure.id]
    where = f"{MEASURES_KEY}{SEPARATOR}{measure.id}"
    if measure.applicability is Applicability.UNDETERMINED:
        return [f"{where}: aplicabilidad sin determinar; decide APPLICABLE o NOT_APPLICABLE"]
    errors = []
    if not (measure.justification and measure.justification.strip()):
        errors.append(f"{where}: falta la justificación")
    excluded = measure.applicability is Applicability.NOT_APPLICABLE
    if selection.required and excluded and measure.absent_assets is None:
        errors.append(
            f"{where}: el anexo II la exige ({selection.required_by}) y no puede excluirse "
            "salvo que no exista el tipo de activo (`absent_assets`, anexo II, 2.1.a); "
            "si no, márcala APPLICABLE y, si la sustituyes, añade `compensation` (art. 28.3)"
        )
    if measure.absent_assets is not None and not selection.required:
        errors.append(f"{where}: `absent_assets` solo excluye una medida exigida por el anexo II")
    errors.extend(absent_asset_errors(measure, where, assets))
    if measure.compensation and not selection.required:
        errors.append(f"{where}: `compensation` solo sustituye una medida exigida por el anexo II")
    errors.extend(_reinforcement_errors(measure, selection.alternatives, where))
    if measure.selection != context.basis(measure.id):
        confirm = f"`applicability propose --confirm {measure.id}`"
        if measure.selection is None:
            errors.append(f"{where}: decisión sin registrar; confírmala con {confirm}")
        else:
            errors.append(
                f"{where}: revisión pendiente, cambió el fundamento de la decisión (categoría, "
                f"niveles o catálogo); revísala y confírmala con {confirm}"
            )
    elif measure.proposed and not is_own_proposal(measure):
        errors.append(
            f"{where}: la justificación propuesta se editó; quita `proposed` para conservarla"
        )
    return errors


def _reinforcement_errors(
    measure: Measure, groups: tuple[tuple[str, ...], ...], where: str
) -> list[str]:
    chosen = measure.chosen_reinforcements
    if measure.applicability is not Applicability.APPLICABLE:
        groups = ()
    errors = []
    for group in groups:
        picked = [ref for ref in chosen if ref in group]
        if len(picked) != 1:
            errors.append(
                f"{where}: elige exactamente un refuerzo de ({', '.join(group)}) y anótalo en "
                "`chosen_reinforcements`; la Declaración debe decir cuál adopta la organización"
            )
    known = {ref for group in groups for ref in group}
    errors.extend(
        f"{where}: chosen_reinforcements: {ref} no es un refuerzo alternativo de esta medida"
        for ref in chosen
        if ref not in known
    )
    return errors
