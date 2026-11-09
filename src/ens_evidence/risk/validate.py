"""Carga y validación cruzada de `risk/` contra la metodología, el inventario y el catálogo."""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.approvals import load_model
from ens_evidence.catalog import load_catalog
from ens_evidence.risk.models import (
    METHODOLOGY_KEY,
    METHODOLOGY_MD_KEY,
    REGISTER_KEY,
    TREATMENT_PLAN_KEY,
    Methodology,
    RegisterFile,
    TreatmentPlanFile,
)
from ens_evidence.scope import require_approved_scope
from ens_evidence.scope.inventory import load_inventory
from ens_evidence.scope.messages import SEPARATOR
from ens_evidence.workspace import WorkspaceStore

_ADOPTION = (
    f"{METHODOLOGY_KEY}: sigue siendo la propuesta de la plantilla; adóptala con adopted: true"
)


@dataclass(frozen=True)
class RiskAnalysis:
    methodology: Methodology
    register: RegisterFile
    plan: TreatmentPlanFile


def validate_risk(store: WorkspaceStore, now: datetime) -> tuple[RiskAnalysis | None, list[str]]:
    """Lanza `ScopeNotApproved` si el alcance no está aprobado ahora."""
    require_approved_scope(store)
    errors: list[str] = []
    methodology = load_model(store, METHODOLOGY_KEY, Methodology, errors)
    register = load_model(store, REGISTER_KEY, RegisterFile, errors)
    plan = load_model(store, TREATMENT_PLAN_KEY, TreatmentPlanFile, errors)
    if not store.exists(METHODOLOGY_MD_KEY):
        errors.append(f"{METHODOLOGY_MD_KEY}: no existe; créalo con ens-evidence risk init")
    inventory = load_inventory(store)
    errors.extend(inventory.errors)
    if methodology is None or register is None or plan is None or inventory.inventory is None:
        return None, errors
    if not methodology.adopted:
        errors.append(_ADOPTION)
    if not register.risks:
        errors.append(
            f"{REGISTER_KEY}: no hay ningún riesgo; op.pl.1 pide identificar al menos "
            "las amenazas más probables y sus riesgos residuales"
        )
    assets = {a.id for a in inventory.inventory.assets.assets}
    services = {s.id for s in inventory.inventory.services.services}
    errors.extend(check_register_entries(register, methodology, assets, services, now))
    errors.extend(_check_plan(plan, register))
    return (None if errors else RiskAnalysis(methodology, register, plan)), errors


def check_register_entries(
    register: RegisterFile,
    methodology: Methodology,
    assets: set[str],
    services: set[str],
    now: datetime,
) -> list[str]:
    catalog = load_catalog()
    levels = methodology.risk_level_ids
    errors: list[str] = []
    for risk in register.risks:
        where = f"{REGISTER_KEY}{SEPARATOR}{risk.id}{SEPARATOR}"
        for field, refs, known in (
            ("assets", risk.assets, assets),
            ("services", risk.services, services),
        ):
            errors.extend(
                f"{where}{field}[{i}]: no existe en el inventario; corrige la referencia"
                for i, ref in enumerate(refs)
                if ref not in known
            )
        errors.extend(
            f"{where}safeguards[{i}]: no es una medida del catálogo; corrige el id"
            for i, ref in enumerate(risk.safeguards)
            if catalog.get(ref) is None
        )
        if risk.likelihood not in methodology.likelihood_ids:
            errors.append(f"{where}likelihood: no es un valor de la escala")
        if risk.impact not in methodology.impact_ids:
            errors.append(f"{where}impact: no es un valor de la escala")
        expected = methodology.inherent(risk.likelihood, risk.impact)
        if expected is not None and risk.inherent != expected:
            errors.append(f"{where}inherent: no coincide con la matriz; recalcúlalo")
        for field, value in (("inherent", risk.inherent), ("residual", risk.residual)):
            if value not in levels:
                errors.append(f"{where}{field}: no es un valor de risk_levels")
        if risk.inherent in levels and risk.residual in levels:
            if levels.index(risk.residual) > levels.index(risk.inherent):
                errors.append(f"{where}residual: no puede superar al riesgo inherente")
            if risk.residual != methodology.minimum_level and risk.acceptance is None:
                errors.append(
                    f"{where}acceptance: el residual no es el mínimo de la escala; "
                    "registra quién lo acepta"
                )
        if risk.acceptance and risk.acceptance.accepted_at > now:
            errors.append(f"{where}acceptance.accepted_at: la fecha está en el futuro")
    return errors


def _check_plan(plan: TreatmentPlanFile, register: RegisterFile) -> list[str]:
    known = {r.id for r in register.risks}
    return [
        f"{TREATMENT_PLAN_KEY}{SEPARATOR}{action.id}{SEPARATOR}risk: "
        "no existe en risk/register.yaml; corrige la referencia"
        for action in plan.actions
        if action.risk not in known
    ]
