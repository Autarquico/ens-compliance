"""Última pre-auditoría: hallazgos como propuestas, nunca como conclusiones. Solo lee."""

from starlette.requests import Request
from starlette.responses import Response

from ens_evidence.models.preaudit import Layer
from ens_evidence.preaudit.deterministic import RULE_LABELS
from ens_evidence.preaudit.findings import findings_view
from ens_evidence.preaudit.records import scan_runs
from ens_evidence.ui.app import render
from ens_evidence.ui.views import NO_DATE, format_instant, page_context

LAYER_LABELS = {Layer.DETERMINISTIC: "Capa determinista", Layer.JUDGMENT: "Capa de juicio"}
NOT_DISPOSED = "No se dispone"


def _criterion(finding) -> str:
    if finding.layer is Layer.DETERMINISTIC:
        return RULE_LABELS.get(finding.criterion, finding.criterion)
    return finding.criterion


def preauditoria(request: Request) -> Response:
    store, now = request.app.state.store, request.app.state.clock()
    scan = scan_runs(store, now=now)
    view = findings_view(store, now)
    generated_at, layers, findings = NO_DATE, {}, []
    if scan.problems or view.problems:
        layers = {"Registro de pre-auditorías": "ERROR"}
    elif scan.records:
        record = scan.records[-1].record
        generated_at = format_instant(record.run.at)
        layers = {
            LAYER_LABELS[Layer.DETERMINISTIC]: record.run.deterministic_status.value,
            LAYER_LABELS[Layer.JUDGMENT]: record.run.judgment_status.value,
        }
        for finding in record.findings:
            state = view.state_of(finding.id)
            findings.append(
                {
                    "id": finding.id,
                    "measure_id": finding.measure,
                    "classification": finding.proposal.value,
                    "disposition": state.state.value if state else NOT_DISPOSED,
                    "text": finding.reason,
                    "criterion": _criterion(finding),
                    "layer_label": LAYER_LABELS[finding.layer],
                }
            )
    return render(
        request,
        "views/preauditoria.html",
        **page_context(request, "preauditoria", "Pre-auditoría"),
        generated_at=generated_at,
        layer_status=layers,
        findings=findings,
    )
