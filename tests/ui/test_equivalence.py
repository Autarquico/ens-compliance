"""Escenario «alcance y categorización equivalentes a la CLI»: mismo guion, mismo reloj."""

import itertools
import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from matrix.acceptance_helpers import APPROVER
from typer.testing import CliRunner

import ens_evidence.cli as cli
import ens_evidence.evidence.layout as evidence_layout
from ens_evidence.catalog.model import Dimension
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.evidence import list_manifests
from ens_evidence.matrix import documents_snapshot, matrix_status
from ens_evidence.orgdocs import document_names
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.ui.config import UiConfig
from ens_evidence.ui.wizard.applicability import _medidas_context
from ens_evidence.ui.wizard.base import StepInput
from ens_evidence.ui.wizard.docs import BLOCKS
from ens_evidence.workspace import LocalStore

from .conftest import (
    NOW,
    WRITE_HEADERS,
    Session,
    auth,
    mint,
    seed_example_scope,
    snapshot_of,
    ui_client,
)
from .test_status_flow import next_step_of
from .test_wizard_docs import ANSWERS, form_for

JUSTIFICATION = "Criterio 1.º del anexo I, 3"
LEVELS = {"datos": "ALTO", "svc-facturacion": "MEDIO", "svc-soporte": "BAJO"}
ELEMENTS = [
    ("information", "datos"),
    ("service", "svc-facturacion"),
    ("service", "svc-soporte"),
]
runner = CliRunner()


def cli_script(ens, levels=LEVELS) -> None:
    ens("scope", "set-system", "--name", "Sistema de prueba", "--description", "Para comparar")
    ens("scope", "add-asset", "--id", "lap-nuevo", "--type", "laptop", "--set", "os=macOS",
        "--set", "owner=Ana Ruiz", "--set", "encrypted=true", "--set", "usage=Trabajo",
        "--include")  # fmt: skip
    ens("scope", "add-service", "--id", "svc-soporte", "--name", "Soporte",
        "--asset", "lap-nuevo", "--asset", "srv-app", "--include")  # fmt: skip
    ens("scope", "add-dependency", "--id", "dep-nuevo", "--asset", "lap-nuevo",
        "--depends-on", "srv-app", "--purpose", "Acceso por SSH")  # fmt: skip
    ens("scope", "approve", "--by", "Ana Ruiz")
    ens("categorization", "init")
    ens("categorization", "add-information", "--id", "datos", "--description", "Datos de clientes")
    for kind, element in ELEMENTS:
        for dimension in Dimension:
            ens("categorization", "rate", "--id", element, "--dimension", dimension.value,
                "--level", levels[element], "--justification", JUSTIFICATION,
                *(("--service",) if kind == "service" else ()))  # fmt: skip
    for kind, element in ELEMENTS:
        ens("categorization", "sign-valuation", "--id", element, "--by", "Ana Ruiz",
            *(("--service",) if kind == "service" else ()))  # fmt: skip
    ens("categorization", "approve", "--by", "Marta Gil")


def ui_script(session: Session, levels=LEVELS) -> None:
    def post(step: str, data: dict) -> None:
        response = session.post(f"/asistente/{step}", data)
        assert response.status_code == 303, response.text

    post("sistema", {"name": "Sistema de prueba", "description": "Para comparar"})
    post("activos", {
        "accion": "anadir", "asset_type": "laptop", "asset_id": "lap-nuevo", "asset_os": "macOS",
        "asset_owner": "Ana Ruiz", "asset_encrypted": "true", "asset_usage": "Trabajo",
    })  # fmt: skip
    post("servicios", {
        "included_services": ["svc-facturacion"], "service_id": "svc-soporte",
        "service_name": "Soporte", "service_assets": ["lap-nuevo", "srv-app"],
    })  # fmt: skip
    post("dependencias", {
        "dependency_id": "dep-nuevo", "asset": "lap-nuevo", "depends_on": "srv-app",
        "purpose": "Acceso por SSH",
    })  # fmt: skip
    post("aprobacion-alcance", {"accion": "aprobar", "approved_by": "Ana Ruiz"})
    post("informacion", {"info_id": "datos", "description": "Datos de clientes"})
    form = {}
    for kind, element in ELEMENTS:
        for dimension in Dimension:
            base = f"rating~{kind}~{element}~{dimension.value}"
            form[f"{base}~level"] = levels[element]
            form[f"{base}~justification"] = JUSTIFICATION
        form[f"valued_by~{kind}~{element}"] = "Ana Ruiz"
    post("valoraciones", form)
    post("aprobacion-categorizacion", {"accion": "aprobar", "approved_by": "Marta Gil"})


def fingerprints(store: LocalStore) -> tuple:
    scope = scope_status(store)
    categorization = categorization_status(store, NOW)
    assert scope.state is ScopeState.APPROVED
    assert categorization.state is CategorizationState.CURRENT
    return (
        scope.fingerprint,
        scope.approval,
        categorization.approval,
        categorization.computation.category,
    )


def make_ens(workspace: Path, monkeypatch):
    """Una `ens(*args)` de la CLI sobre `workspace`, con el reloj fijo; devuelve lo que imprime."""
    settings = workspace.parent / f"{workspace.name}-settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(workspace)}}})
    )
    monkeypatch.setattr(cli, "clock", lambda: NOW)

    def ens(*args: str) -> str:
        result = runner.invoke(cli.app, ["--settings", str(settings), *args])
        assert result.exit_code == 0, (args, result.output)
        return result.output

    return ens


def run_cli(tmp_path: Path, monkeypatch, script=None) -> None:
    ens = make_ens(tmp_path / "cli-ws", monkeypatch)
    if script is None:
        cli_script(ens)
    else:
        ens(*script)


def test_including_an_existing_asset_is_the_same_in_ui_and_cli(
    tmp_path, workspaces, config, key, token, monkeypatch
):
    cli_store, ui_store = workspaces
    for store in workspaces:
        system = yaml.safe_load(store.read_text("scope/system.yaml"))
        system["included_assets"].remove("bk-externo")
        store.write_text("scope/system.yaml", yaml.safe_dump(system, sort_keys=False))
    included = yaml.safe_load(ui_store.read_text("scope/system.yaml"))["included_assets"]
    run_cli(tmp_path, monkeypatch, ("scope", "include-asset", "--id", "bk-externo"))
    session = Session(ui_client(config, ui_store, key), token)

    response = session.post("/asistente/activos", {"included_assets": [*included, "bk-externo"]})

    assert response.status_code == 303, response.text
    assert (
        "bk-externo" in yaml.safe_load(cli_store.read_text("scope/system.yaml"))["included_assets"]
    )
    assert snapshot_of(cli_store) == snapshot_of(ui_store)


def test_unauthenticated_requests_write_nothing(config, workspaces, key):
    _, ui_store = workspaces
    before = snapshot_of(ui_store)
    client = ui_client(config, ui_store, key)
    headers = {"Origin": "https://ui.example.com", "Sec-Fetch-Site": "same-origin"}

    responses = [
        client.post("/asistente/sistema", data={"name": "x", "description": "y"}, headers=headers),
        client.post(
            "/asistente/aprobacion-alcance",
            data={"accion": "aprobar", "approved_by": "x"},
            headers=headers,
        ),
        client.get("/asistente/sistema"),
        client.get("/"),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]
    assert snapshot_of(ui_store) == before


def test_wizard_store_is_the_whitelisted_one(raw_client, example_store):
    from ens_evidence.ui.store import WriteNotAllowed

    store = raw_client.app.state.store
    before = snapshot_of(example_store)

    with pytest.raises(WriteNotAllowed):
        store.write_text("connectors/github.yaml", "token_env: X\n")

    assert snapshot_of(example_store) == before


# --- el flujo completo: del alcance a la declaración -----------------------------------------

BASIC_LEVELS = dict.fromkeys(
    LEVELS, "BAJO"
)  # BÁSICA: la única categoría que declara la propia organización
RISK_APPROVER = "Marta Gil"
MANAGER = "Marta Ibáñez Soler"
SECURITY = "Lucía Navarro Gil"
SYSTEM_OWNER = "Hugo Serrano Ortiz"
ADMIN = "Ana Ruiz"
METHODOLOGY = {"name": "MAGERIT v3", "reference": "Guía MAGERIT", "adaptation": "Tres niveles"}
RISK = {
    "id": "R-001",
    "threat": "Acceso no autorizado a las facturas",
    "asset": "app-facturas",
    "service": "svc-facturacion",
    "dimension": "C",
    "level": "ALTO",
    "likelihood": "media",
    "impact": "alto",
    "inherent": "alto",
    "safeguard": "op.acc.1",
    "residual": "bajo",
    "treatment": "mitigate",
    "owner": ADMIN,
}
ACTION = {"id": "A-001", "description": "Activar el doble factor", "due": "2026-12-31"}
NOT_NEEDED = "No tenemos usuarios externos: el sistema solo lo usa el equipo interno."
EVIDENCE_TEXT = b"Acta de la revision de seguridad de la oficina"
REGISTER_MEASURE = "op.exp.9"  # solo la sustenta el historial de un incidente cerrado
CLOSURE = {
    "conclusion": "Contenido el acceso y rotadas las credenciales de servicio.",
    "auditable-events-result": "no_change",
    "auditable-events-detail": "Los eventos ya cubrian el caso.",
    "legal-relevance": "no",
}
REVIEW_FORM = {"by": ADMIN, "role": "system_administrator", "conclusion": "Revisada."}
OWNER_FORM = {"owner_name": ADMIN, "owner_role": "system_administrator", "every": "90"}
SELF_ASSESS = {"by": ADMIN, "role": "system_administrator"}
ANALYSIS = {
    "by": SECURITY,
    "conclusion": "Todo revisado.",
    "elevated_to": SYSTEM_OWNER,
    "elevated": "1",
    "manual_judgment_review": "1",
}
DECLARATION = {"by": MANAGER}


def no_guide(milestone: str) -> None:
    """Sin comprobar la guía."""


def risk_form() -> dict:
    return {
        "accion": "anadir-riesgo",
        "risk_id": RISK["id"],
        "threat": RISK["threat"],
        "risk_assets": [RISK["asset"]],
        "risk_services": [RISK["service"]],
        "risk_dimensions": [RISK["dimension"]],
        f"valuation~{RISK['dimension']}": RISK["level"],
        "likelihood": RISK["likelihood"],
        "impact": RISK["impact"],
        "safeguards": [RISK["safeguard"]],
        "residual": RISK["residual"],
        "treatment": RISK["treatment"],
        "owner": RISK["owner"],
    }


def action_form() -> dict:
    return {
        "accion": "anadir-accion",
        "action_id": ACTION["id"],
        "action_risk": RISK["id"],
        "action_description": ACTION["description"],
        "action_owner": ADMIN,
        "action_due": ACTION["due"],
        "action_status": "pending",
    }


@dataclass
class Decision:
    measure: str
    applies: bool
    justification: str
    reinforcements: list[str] = field(default_factory=list)


def pending_decisions(store) -> list[Decision]:
    """Lo que queda por decidir tras preparar: refuerzo de las exigidas, y lo demás, no aplica."""
    pending = _medidas_context(StepInput(store=store, now=NOW, email=""))["pending"]
    decisions = []
    for row in pending:
        if row["state"] == "reinforcement":
            chosen = [group["options"][0]["id"] for group in row["groups"]]
            decisions.append(Decision(row["id"], True, row["justification"], chosen))
        else:
            assert row["state"] == "undecided", row
            decisions.append(Decision(row["id"], False, NOT_NEEDED))
    return decisions


@dataclass
class Review:
    measure: str
    units: list[str]
    documents: list[tuple[str, list[str]]]
    evidence: list[str]

    @property
    def assessment(self) -> str | None:
        return "COMPLIANT" if self.units else None


def review_plan(store, evidence_covers: bool = True) -> list[Review]:
    """Por medida: los documentos aprobados que cubren cláusulas y la evidencia para el resto."""
    rows = matrix_status(store, NOW, checks=StoredCheckResults(reference_now=NOW))[1]
    snapshot = documents_snapshot(store, NOW)
    plan = []
    for status in rows:
        measure = status.row.spec.measure_id
        units = [u for o in status.row.spec.obligations for u in o.units]
        left, documents = list(units), []
        for name, document in sorted(snapshot.documents.items()):
            coverage = document.measures.get(measure)
            covered = [u for u in left if coverage and u in coverage.covered_clauses]
            if covered:
                documents.append((name, covered))
                left = [u for u in left if u not in covered]
        plan.append(Review(measure, units, documents, left))
    return plan


def close_incident(ens, by: str = SECURITY) -> None:
    """El incidente cerrado que sustenta op.exp.9; la web no los registra (lista blanca)."""
    detected = (NOW - timedelta(hours=6)).isoformat()
    ens(
        "incident",
        "new",
        "--title",
        "Acceso indebido",
        "--detected-at",
        detected,
        "--personal-data",
        "no",
        "--by",
        by,
    )
    closure = [arg for k, v in CLOSURE.items() for arg in (f"--{k}", v)]
    ens("incident", "close", "INC-2026-001", *closure, "--by", by)


def post_ok(session: Session, path: str, data: dict) -> str:
    response = session.post(path, data)
    assert response.status_code == 303, (path, re.findall(r"<li>(.*?)</li>", response.text)[6:])
    return response.headers["location"]


def upload_evidence(
    client, token: str, content: bytes = EVIDENCE_TEXT, asset: str = "lap-ana"
) -> None:
    response = client.post(
        "/evidencias/nueva",
        data={"asset_id": asset, "by": ADMIN},
        files={"file": ("acta.txt", content, "text/plain")},
        headers={**auth(token), **WRITE_HEADERS},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def evidence_target(store, plan: Review, acta: str) -> str:
    """El historial del incidente cerrado para op.exp.9; el acta, para las demás."""
    if plan.measure != REGISTER_MEASURE:
        return acta
    (history,) = [m.id for m in list_manifests(store).manifests if m.source == "incident-history"]
    return history


def evidence_id(store) -> str:
    (manifest,) = list_manifests(store).manifests
    return manifest.id


def approver_names() -> dict[str, str]:
    return {
        name: SECURITY if role.value == "security_officer" else MANAGER
        for name, role in APPROVER.items()
    }


def web_script_until_review(session: Session, client, token: str, guide=no_guide) -> None:
    """Alcance, categorización BÁSICA, riesgos, aplicabilidad y documentos, y la matriz abierta."""
    store = client.app.state.store
    guide("alcance vacío")
    ui_script(session, BASIC_LEVELS)
    guide("categorización aprobada")
    post_ok(session, "/asistente/riesgos-metodologia", METHODOLOGY)
    guide("metodología adoptada")
    post_ok(session, "/asistente/riesgos", risk_form())
    post_ok(session, "/asistente/riesgos", action_form())
    post_ok(
        session,
        "/asistente/aprobacion-riesgos",
        {"accion": "aprobar", "approved_by": RISK_APPROVER},
    )
    guide("riesgos aprobados")
    post_ok(session, "/asistente/aplicabilidad", {"accion": "preparar"})
    guide("medidas preparadas")
    for decision in pending_decisions(store):
        form = {"accion": "decidir", "measure_id": decision.measure}
        if decision.applies:
            form |= {"decision": "applies"}
            form |= {f"reinforcement~{i}": r for i, r in enumerate(decision.reinforcements)}
        else:
            form |= {"decision": "not_applicable", "justification": decision.justification}
        post_ok(session, "/asistente/aplicabilidad", form)
    post_ok(
        session,
        "/asistente/aprobacion-aplicabilidad",
        {"accion": "aprobar", "approved_by": RISK_APPROVER},
    )
    guide("Declaración firmada")
    for block in BLOCKS:
        post_ok(session, f"/asistente/{block.slug}", form_for(block.slug))
        if block is BLOCKS[0]:
            guide("primer bloque de documentos")
    guide("cuestionario contestado")
    post_ok(session, "/asistente/documentos-generar", {"accion": "generar"})
    guide("documentos generados")
    for name, approved_by in approver_names().items():
        data = {"accion": "aprobar", "documento": name, "approved_by": approved_by}
        post_ok(session, "/asistente/documentos-aprobar", data)
    assert sorted(approver_names()) == sorted(document_names())
    guide("documentos aprobados")
    assert session.get("/matriz").status_code == 200  # abrir la matriz la inicia
    upload_evidence(client, token)


def web_review(session: Session, store, plan: Review, acta: str, before_links=None) -> None:
    base = f"/matriz/{plan.measure}"
    post_ok(session, f"{base}/responsable", OWNER_FORM)
    if before_links:
        before_links()
    for name, covers in plan.documents:
        post_ok(session, f"{base}/enlazar", {"kind": "document", "target": name, "covers": covers})
    if plan.evidence:
        target = evidence_target(store, plan, acta)
        data = {"kind": "evidence", "target": target, "covers": plan.evidence}
        post_ok(session, f"{base}/enlazar", data)
    form = dict(REVIEW_FORM) | ({"assessment": plan.assessment} if plan.assessment else {})
    post_ok(session, f"{base}/revisar", form)


def web_full_flow(session: Session, client, token: str, close_the_incident, guide=no_guide) -> str:
    """Todo por la web; la única excepción es el incidente cerrado que sustenta op.exp.9."""
    store = client.app.state.store
    web_script_until_review(session, client, token, guide)
    acta = evidence_id(store)
    plans = review_plan(store)
    for done, plan in enumerate(plans):
        if done == len(plans) // 2:
            guide("matriz a medias")
        before = None
        if plan.measure == REGISTER_MEASURE:

            def before() -> None:
                refused = session.post(
                    f"/matriz/{REGISTER_MEASURE}/revisar", REVIEW_FORM | {"assessment": "COMPLIANT"}
                )
                assert refused.status_code == 422 and "cláusulas sin cubrir" in refused.text.lower()
                close_the_incident()

        web_review(session, store, plan, acta, before)
    guide("matriz revisada")
    return web_script_from_dossier(session, guide)


def web_script_from_dossier(session: Session, guide=no_guide) -> str:
    location = post_ok(session, "/expediente/generar", {})
    dossier = location.split("?")[0]
    guide("expediente generado")
    for action, data in (
        ("self-assess", SELF_ASSESS),
        ("analyze", ANALYSIS),
        ("declare", DECLARATION),
    ):
        post_ok(session, f"{dossier}/{action}", data)
        guide(f"expediente: {action}")
    return dossier.rsplit("/", 1)[1]


def cli_full_script(ens, tmp_path: Path) -> str:
    """El mismo guion por la CLI; devuelve el id del expediente."""
    store = LocalStore(tmp_path / "cli-ws")
    cli_script(ens, BASIC_LEVELS)
    ens(
        "risk",
        "adopt-methodology",
        "--name",
        METHODOLOGY["name"],
        "--reference",
        METHODOLOGY["reference"],
        "--adaptation",
        METHODOLOGY["adaptation"],
    )
    ens(
        "risk",
        "add",
        "--id",
        RISK["id"],
        "--asset",
        RISK["asset"],
        "--service",
        RISK["service"],
        "--threat",
        RISK["threat"],
        "--dimension",
        RISK["dimension"],
        "--valuation",
        f"{RISK['dimension']}={RISK['level']}",
        "--likelihood",
        RISK["likelihood"],
        "--impact",
        RISK["impact"],
        "--inherent",
        RISK["inherent"],
        "--safeguard",
        RISK["safeguard"],
        "--residual",
        RISK["residual"],
        "--treatment",
        RISK["treatment"],
        "--owner",
        RISK["owner"],
    )
    ens(
        "risk",
        "add-action",
        "--id",
        ACTION["id"],
        "--risk",
        RISK["id"],
        "--description",
        ACTION["description"],
        "--owner",
        ADMIN,
        "--due",
        ACTION["due"],
    )
    ens("risk", "approve", "--by", RISK_APPROVER)
    ens("controls", "init")
    ens("applicability", "propose")
    for decision in pending_decisions(store):
        ens(
            "applicability",
            "decide",
            decision.measure,
            "--justification",
            decision.justification,
            "--applicability",
            "APPLICABLE" if decision.applies else "NOT_APPLICABLE",
            *(a for r in decision.reinforcements for a in ("--reinforcement", r)),
        )
    ens("applicability", "approve", "--by", RISK_APPROVER)
    for block in BLOCKS:
        for question in block.question_ids:
            answer = ANSWERS.get(question)
            if answer is None:
                continue
            as_json = isinstance(answer, dict) or (answer and isinstance(answer[0], dict))
            values = (
                ["--json", json.dumps(answer)]
                if as_json
                else [
                    a
                    for v in ([answer] if isinstance(answer, str) else answer)
                    for a in ("--value", v)
                ]
            )
            ens("docs", "answer", question, *values)
    ens("docs", "build")
    for name, role in APPROVER.items():
        ens("docs", "approve", name, "--by", approver_names()[name], "--role", role.value)
    ens("matrix", "init")
    evidence_file = tmp_path / "acta.txt"
    evidence_file.write_bytes(EVIDENCE_TEXT)
    ens(
        "evidence",
        "import",
        str(evidence_file),
        "--source",
        "manual",
        "--asset",
        "lap-ana",
        "--by",
        ADMIN,
    )
    acta = evidence_id(store)
    for plan in review_plan(store):
        incident = (lambda: close_incident(ens)) if plan.measure == REGISTER_MEASURE else None
        cli_review(ens, store, plan, acta, incident)
    return cli_dossier(ens)


def cli_review(ens, store, plan: Review, acta: str, before_links=None) -> None:
    ens(
        "matrix",
        "set",
        plan.measure,
        "--owner",
        ADMIN,
        "--owner-role",
        "system_administrator",
        "--every",
        "90",
    )
    if before_links:
        before_links()
    for name, covers in plan.documents:
        ens("matrix", "link", plan.measure, "--document", name, "--covers", ",".join(covers))
    if plan.evidence:
        target = evidence_target(store, plan, acta)
        ens(
            "matrix",
            "link",
            plan.measure,
            "--evidence",
            target,
            "--covers",
            ",".join(plan.evidence),
        )
    ens(
        "matrix",
        "review",
        plan.measure,
        "--by",
        REVIEW_FORM["by"],
        "--role",
        REVIEW_FORM["role"],
        "--conclusion",
        REVIEW_FORM["conclusion"],
        *(("--assessment", plan.assessment) if plan.assessment else ()),
    )


def cli_dossier(ens) -> str:
    dossier = re.search(
        r"Expediente (\S+) generado", ens("assessment", "dossier", "generate")
    ).group(1)
    ens(
        "assessment",
        "dossier",
        "self-assess",
        dossier,
        "--by",
        SELF_ASSESS["by"],
        "--role",
        SELF_ASSESS["role"],
    )
    ens(
        "assessment",
        "dossier",
        "analyze",
        dossier,
        "--by",
        ANALYSIS["by"],
        "--conclusion",
        ANALYSIS["conclusion"],
        "--elevated-to",
        ANALYSIS["elevated_to"],
        "--elevated",
        "--manual-judgment-review",
    )
    ens("assessment", "dossier", "declare", dossier, "--by", DECLARATION["by"])
    return dossier


@dataclass
class Milestone:
    """Lo que la página de estado ofrecía en un punto del flujo y lo que respondió el enlace."""

    name: str
    url: str | None
    label: str
    status: int | None


@dataclass
class FlowRun:
    store: LocalStore
    dossier: str
    milestones: list[Milestone] = field(default_factory=list)


def sequential_evidence_ids(patch: pytest.MonkeyPatch) -> None:
    """Los ids de evidencia llevan 8 hex al azar; con una secuencia, dos flujos son comparables."""
    counter = itertools.count(1)
    stub = SimpleNamespace(token_hex=lambda _: f"{next(counter):08x}")
    patch.setattr(evidence_layout, "secrets", stub)


@pytest.fixture(scope="session")
def web_flow(tmp_path_factory, key) -> FlowRun:
    """El flujo completo por la web, una vez por sesión de pruebas (tarda)."""
    from .conftest import AUD, ORIGIN, TEAM

    root = tmp_path_factory.mktemp("flujo-web")
    store = seed_example_scope(LocalStore(root / "ui-ws"))
    config = UiConfig(origin=ORIGIN, access_aud=AUD, team_domain=TEAM, jwks_file=root / "jwks.json")
    client, token = ui_client(config, store, key), mint(key)
    session, milestones = Session(client, token), []

    def guide(name: str) -> None:
        url, label = next_step_of(session.get("/").text)
        status = session.get(url).status_code if url else None
        milestones.append(Milestone(name, url, label, status))

    with pytest.MonkeyPatch.context() as patch:
        sequential_evidence_ids(patch)
        ens = make_ens(root / "ui-ws", patch)
        dossier = web_full_flow(session, client, token, lambda: close_incident(ens), guide)
    return FlowRun(store, dossier, milestones)


@pytest.fixture(scope="session")
def cli_flow(tmp_path_factory) -> FlowRun:
    root = tmp_path_factory.mktemp("flujo-cli")
    store = seed_example_scope(LocalStore(root / "cli-ws"))
    with pytest.MonkeyPatch.context() as patch:
        sequential_evidence_ids(patch)
        dossier = cli_full_script(make_ens(root / "cli-ws", patch), root)
    return FlowRun(store, dossier)


def test_el_flujo_completo_deja_los_mismos_ficheros_que_la_cli(web_flow, cli_flow):
    web, cli_files = snapshot_of(web_flow.store), snapshot_of(cli_flow.store)

    assert set(web) == set(cli_files)
    assert [k for k in web if web[k] != cli_files[k]] == []
    assert web_flow.dossier == cli_flow.dossier


AREAS = {
    "alcance y categorización": ("scope/",),
    "riesgos": ("risk/",),
    "aplicabilidad y Declaración": (
        "controls/measures.yaml",
        "controls/applicability.yaml",
        "controls/declarations/",
    ),
    "documentos": ("org/",),
    "matriz y revisiones": (
        "controls/matrix.yaml",
        "controls/matrix-history/",
        "controls/reviews/",
    ),
    "evidencias": ("evidence/manifests/", "evidence/raw/"),
    "expediente": ("evidence/assessments/",),
}


@pytest.mark.parametrize("area", AREAS)
def test_cada_area_del_flujo_deja_los_mismos_bytes_que_la_cli(web_flow, cli_flow, area):
    def of(run: FlowRun) -> dict[str, bytes]:
        return {k: v for k, v in snapshot_of(run.store).items() if k.startswith(AREAS[area])}

    web, cli_files = of(web_flow), of(cli_flow)

    assert web, f"el flujo no escribió nada de «{area}»"
    assert web == cli_files


def test_el_flujo_termina_con_el_expediente_declarado_en_los_dos(web_flow, cli_flow):
    from ens_evidence.assessment.dossier import scan_transitions
    from ens_evidence.models.enums import DossierState

    for run in (web_flow, cli_flow):
        scan = scan_transitions(run.store, run.dossier)
        assert scan.dossier.state is DossierState.DECLARED
        assert [t.to_state for t in scan.dossier.transitions][-3:] == [
            DossierState.SELF_ASSESSED,
            DossierState.ANALYZED,
            DossierState.DECLARED,
        ]
    assert fingerprints(web_flow.store) == fingerprints(cli_flow.store)
