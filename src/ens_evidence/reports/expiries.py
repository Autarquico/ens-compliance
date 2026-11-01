"""Caducidades próximas del informe diario (D4 de reports-scheduling).

Solo lee. Cada elemento lleva la fuente de la que sale; lo que no se pudo leer va a `problems`
(nunca se omite en silencio). Ventanas, en días antes de caducar:

- revisiones de medida (implantación y aplicabilidad), aprobaciones de categorización y de riesgos,
  restore tests y declaración offsite: 30;
- evidencia manual: 14; evidencia técnica (su límite es de 30 días): 7;
- acciones del plan de tratamiento: al vencer (0).

De la evidencia y de los restore tests cuenta solo la más reciente de cada (fuente, activo) o
sistema: una anterior sustituida ya no es la vigente. La evidencia sin `valid_until` no caduca.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, tzinfo

import yaml
from pydantic import ValidationError

from ens_evidence.approvals import read_record
from ens_evidence.backup.restore_test import RESTORE_TESTS_PREFIX, RestoreTest
from ens_evidence.categorization.approval import (
    APPROVAL_KEY as CATEGORIZATION_APPROVAL_KEY,
)
from ens_evidence.categorization.approval import CategorizationApproval
from ens_evidence.connectors.config import (
    ConnectorConfigError,
    connector_config_key,
    load_connector_config,
)
from ens_evidence.connectors.restic.config import ResticConfig
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.matrix.store import MATRIX_KEY, effective_reviews, read_matrix
from ens_evidence.matrix.technical import technical_valid_until
from ens_evidence.models.contracts import EvidenceManifest, ProvenanceKind
from ens_evidence.reports.model import ExpiryItem, SourceKind, SourceRef
from ens_evidence.risk.models import (
    APPROVAL_KEY as RISK_APPROVAL_KEY,
)
from ens_evidence.risk.models import (
    TREATMENT_PLAN_KEY,
    ActionStatus,
    RiskApproval,
    TreatmentPlanFile,
)
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

WINDOW_DAYS = {
    "measure_review": 30,
    "applicability_review": 30,
    "categorization_approval": 30,
    "risk_approval": 30,
    "restore_test": 30,
    "offsite_declaration": 30,
    "manual_evidence": 14,
    "technical_evidence": 7,
    "treatment_action": 0,
}
OPEN_ACTIONS = frozenset({ActionStatus.PENDING, ActionStatus.IN_PROGRESS})


@dataclass(frozen=True)
class ExpiryScan:
    """`consulted`: toda fuente leída, aunque no caduque pronto (respalda un «no caduca nada»)."""

    items: tuple[ExpiryItem, ...]
    consulted: tuple[SourceRef, ...]
    problems: tuple[str, ...]


@dataclass
class _Scan:
    store: WorkspaceStore
    cutoff: datetime
    today: date
    tz: tzinfo
    items: list[ExpiryItem] = field(default_factory=list)
    consulted: list[SourceRef] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def ref(self, kind: SourceKind, key: str, at: datetime | None = None) -> SourceRef | None:
        """La fuente con la huella de los bytes actuales; None (y un problema) si no se lee."""
        try:
            data = self.store.read_bytes(key)
        except WorkspaceError:
            self.problems.append(f"{key}: no se puede leer")
            return None
        found = SourceRef(
            kind=kind, key=key, sha256=hashlib.sha256(data).hexdigest(), at=at or self.cutoff
        )
        self.consulted.append(found)
        return found

    def add(self, kind: str, subject: str, expires_at: datetime, source: SourceRef | None) -> None:
        if source is None:
            return
        days_left = (expires_at.astimezone(self.tz).date() - self.today).days
        if days_left <= WINDOW_DAYS[kind]:
            self.items.append(
                ExpiryItem(
                    kind=kind,
                    subject=subject,
                    expires_at=expires_at,
                    days_left=days_left,
                    sources=[source],
                )
            )


def scan_expiries(
    store: WorkspaceStore, *, cutoff: datetime, today: date, tz: tzinfo
) -> ExpiryScan:
    """`today`: el día del informe; `days_left` se cuenta en fechas locales de `tz`."""
    scan = _Scan(store, cutoff, today, tz)
    _measure_reviews(scan)
    _approvals(scan)
    restore_tests = _read_restore_tests(scan)
    _evidence(scan, {t.sha256 for t in restore_tests})
    _restore_tests(scan, restore_tests)
    _offsite(scan)
    _treatment_actions(scan)
    scan.items.sort(key=lambda i: (i.expires_at, i.kind, i.subject))
    return ExpiryScan(tuple(scan.items), tuple(scan.consulted), tuple(scan.problems))


def _measure_reviews(scan: _Scan) -> None:
    matrix, errors = read_matrix(scan.store)
    if errors:
        scan.problems.append(f"{MATRIX_KEY}: ilegible")
    if matrix is None:
        return
    scan.ref("document", MATRIX_KEY)
    for entry in matrix.entries:
        reviews = effective_reviews(scan.store, entry.id, scan.cutoff)
        if reviews.errors or reviews.unreadable:
            scan.problems.append(f"revisiones de {entry.id}: ilegibles")
        for kind, stored in (
            ("measure_review", reviews.implementation),
            ("applicability_review", reviews.applicability),
        ):
            if stored is not None:
                source = scan.ref("review", stored.key, stored.record.recorded_at)
                scan.add(kind, entry.id, stored.record.review.valid_until, source)


def _approvals(scan: _Scan) -> None:
    categorization, errors = read_record(
        scan.store, CATEGORIZATION_APPROVAL_KEY, CategorizationApproval, ""
    )
    if errors:
        scan.problems.append(f"{CATEGORIZATION_APPROVAL_KEY}: ilegible")
    if categorization is not None:
        source = scan.ref("approval", CATEGORIZATION_APPROVAL_KEY, categorization.approved_at)
        scan.add("categorization_approval", "categorización", categorization.valid_until, source)
    risk, errors = read_record(scan.store, RISK_APPROVAL_KEY, RiskApproval, "")
    if errors:
        scan.problems.append(f"{RISK_APPROVAL_KEY}: ilegible")
    if risk is not None:
        source = scan.ref("approval", RISK_APPROVAL_KEY, risk.approved_at)
        scan.add("risk_approval", "análisis de riesgos", risk.valid_until, source)


def _latest_per_source_asset(manifests: list[EvidenceManifest]) -> list[EvidenceManifest]:
    latest: dict[tuple[str, str], EvidenceManifest] = {}
    for manifest in sorted(manifests, key=lambda m: (m.collected_at, m.id)):
        latest[(manifest.source, manifest.asset_id)] = manifest
    return list(latest.values())


def _evidence(scan: _Scan, restore_test_artifacts: set[str]) -> None:
    listing = list_manifests(scan.store)
    if listing.errors:
        scan.problems.append(f"evidencia: {len(listing.errors)} manifiestos ilegibles")
    visible = [
        m
        for m in listing.manifests
        if m.collected_at <= scan.cutoff and m.artifact.sha256 not in restore_test_artifacts
    ]
    for manifest in _latest_per_source_asset(visible):
        label = f"{manifest.source} {manifest.asset_id} ({manifest.id})"
        source = scan.ref("evidence", manifest_key(manifest.id), manifest.collected_at)
        if manifest.provenance.kind is ProvenanceKind.COLLECTOR:
            expires = technical_valid_until(manifest.collected_at, manifest.valid_until)
            scan.add("technical_evidence", label, expires, source)
        elif manifest.valid_until is not None:
            scan.add("manual_evidence", label, manifest.valid_until, source)


@dataclass(frozen=True)
class _StoredTest:
    key: str
    sha256: str
    test: RestoreTest


def _read_restore_tests(scan: _Scan) -> list[_StoredTest]:
    """Los restore tests legibles hasta el corte; los demás, a `problems`."""
    found: list[_StoredTest] = []
    for key in scan.store.list(RESTORE_TESTS_PREFIX):
        try:
            data = scan.store.read_bytes(key)
            test = RestoreTest.model_validate(safe_load_unique(data.decode("utf-8")))
        except (WorkspaceError, UnicodeDecodeError, yaml.YAMLError, ValidationError):
            scan.problems.append(f"{key}: ilegible")
            continue
        if test.performed_at <= scan.cutoff:
            found.append(_StoredTest(key, hashlib.sha256(data).hexdigest(), test))
    return found


def _restore_tests(scan: _Scan, stored: list[_StoredTest]) -> None:
    latest: dict[str, _StoredTest] = {}
    for item in sorted(stored, key=lambda s: (s.test.performed_at, s.key)):
        latest[item.test.system] = item
    for item in latest.values():
        test = item.test
        source = scan.ref("restore_test", item.key, test.review.reviewed_at)
        scan.add("restore_test", f"{test.test_id} ({test.system})", test.review.valid_until, source)


def _offsite(scan: _Scan) -> None:
    key = connector_config_key("restic")
    if not scan.store.exists(key):
        return
    try:
        config = load_connector_config(scan.store, "restic", ResticConfig)
    except (ConnectorConfigError, WorkspaceError):
        scan.problems.append(f"{key}: ilegible")
        return
    declaration = config.offsite
    if declaration is not None:
        source = scan.ref("config", key, declaration.reviewed_at)
        scan.add("offsite_declaration", "copia externa", declaration.valid_until, source)


def _treatment_actions(scan: _Scan) -> None:
    if not scan.store.exists(TREATMENT_PLAN_KEY):
        return
    try:
        plan = TreatmentPlanFile.model_validate(
            safe_load_unique(scan.store.read_text(TREATMENT_PLAN_KEY))
        )
    except (WorkspaceError, yaml.YAMLError, ValidationError):
        scan.problems.append(f"{TREATMENT_PLAN_KEY}: ilegible")
        return
    source = scan.ref("risk", TREATMENT_PLAN_KEY)
    for action in plan.actions:
        if action.status in OPEN_ACTIONS:
            due = datetime.combine(action.due, time.min, tzinfo=scan.tz)
            scan.add("treatment_action", action.id, due, source)
