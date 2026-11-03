from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import pytest

from ens_evidence.assessment.render import HUMAN_REVIEW_PENDING, NO_CONFORMITY, NO_PRE_AUDIT
from ens_evidence.incidents.model import RecordKind
from ens_evidence.models.enums import CheckStatus, CollectionStatus, Readiness
from ens_evidence.orgdocs.approval import DocumentState
from ens_evidence.reports.labels import REPORT_NOT_A_REVIEW
from ens_evidence.reports.model import (
    Aggregate,
    AssetsSummary,
    BackupStageResult,
    ChangeItem,
    Changes,
    CheckCounts,
    ConnectorAttempts,
    ConnectorEntry,
    Coverage,
    CycleInfo,
    DocumentEntry,
    Entry,
    ExpiryItem,
    IncidentEntry,
    IncidentRecordRef,
    InterruptedCycle,
    ReadinessValue,
    Sourced,
    SourceRef,
    Unavailable,
)
from ens_evidence.reports.render_html import (
    CSP,
    CSS,
    render_daily_html,
    render_monthly_html,
)

from .helpers import NOON, SHA, daily, monthly

HOSTILE = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "</style><script>x</script>",
    "{{ 7*7 }}",
    "&#x3c;script&#x3e;",
    "javascript:alert(1)",
    "' onmouseover='alert(1)",
]
ALLOWED_TAGS = {
    "html", "head", "meta", "title", "style", "body", "h1", "h2", "p", "table", "thead",
    "tbody", "tfoot", "tr", "th", "td", "section", "span", "br",
}  # fmt: skip
ALLOWED_ATTRS = {
    ("html", "lang"),
    ("meta", "charset"),
    ("meta", "http-equiv"),
    ("meta", "content"),
    ("p", "class"),
    ("span", "class"),
}


class Probe(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str, str | None]] = []
        self.text: list[str] = []
        self.style: list[str] = []
        self.rows: list[list[str]] = []
        self._in_style = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.meta: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]
        if tag == "meta":
            self.meta.append(dict(attrs))
        self._in_style = tag == "style"
        if tag == "tr":
            self._row = []
        if tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_style:
            self.style.append(data)
        else:
            self.text.append(data)
            if self._cell is not None:
                self._cell.append(data)


def probe(document: str) -> Probe:
    parser = Probe()
    parser.feed(document)
    parser.close()
    return parser


def src(key: str) -> SourceRef:
    return SourceRef(kind="evidence", key=key, sha256=SHA, at=NOON)


def hostile_daily():
    h = HOSTILE
    refs = [src(f"evidence/{h[0]}")]
    return daily(
        assets=Sourced[AssetsSummary](
            value=AssetsSummary(total=2, by_type={h[1]: 1, h[2]: 1}), sources=refs
        ),
        assets_without_agent=Sourced[list[str]](value=[h[3], h[4]], sources=refs),
        connectors=[
            ConnectorEntry(
                connector=h[5],
                collection=Unavailable(reason=h[0]),
                checks=Sourced[CheckCounts](
                    value=CheckCounts(counts={CheckStatus.ERROR: 1}), sources=refs
                ),
            ),
            ConnectorEntry(
                connector=h[1],
                collection=Sourced[CollectionStatus](value=CollectionStatus.ERROR, sources=refs),
                checks=Unavailable(reason=h[2]),
            ),
        ],
        readiness=Sourced[ReadinessValue](
            value=ReadinessValue(readiness=Readiness.NOT_READY, blockers=h), sources=refs
        ),
        expiring=Sourced[list[ExpiryItem]](
            value=[
                ExpiryItem(kind=h[6], subject=h[1], expires_at=NOON, days_left=-3, sources=refs)
            ],
            sources=refs,
        ),
        critical_alerts=Sourced[list[str]](value=h, sources=refs),
        open_incidents=Sourced[list[IncidentEntry]](
            value=[
                IncidentEntry(
                    id="INC-2026-001",
                    title=h[0],
                    severity=h[1],
                    detected_at=NOON,
                    simulated=False,
                    last_record=IncidentRecordRef(seq=1, kind=RecordKind.OPENED, recorded_at=NOON),
                ),
                IncidentEntry(id="SIM-2026-001", problems=[h[2], h[4]]),
            ],
            sources=refs,
        ),
        changes=Sourced[Changes](
            value=Changes(
                base_period="2026-10-31",
                items=[ChangeItem(subject=h[0], change="added")],
            ),
            sources=refs,
        ),
        interrupted_cycles=Sourced[list[InterruptedCycle]](
            value=[InterruptedCycle(run_id="r1", last_stage=h[2], started_at=NOON)],
            sources=refs,
        ),
        backup=Sourced[BackupStageResult](
            value=BackupStageResult(
                run_id="r1", outcome="backup_failed", code=h[2], finished_at=NOON
            ),
            sources=refs,
        ),
        cycle=CycleInfo(
            run_id="r1",
            trigger="scheduled",
            period="2026-11-01",
            due_at=NOON,
            started_at=NOON,
            data_cutoff=NOON,
            connector_attempts=[
                ConnectorAttempts(connector=h[1], attempts=2, last_status=CollectionStatus.ERROR)
            ],
        ),
    )


def hostile_monthly():
    h = HOSTILE
    refs = [src(f"evidence/{h[1]}")]
    aggregate = Sourced[Aggregate](
        value=Aggregate(
            coverage=Coverage.of(3, 31),
            counts={h[0]: 1},
            items=[Entry(subject=h[2], detail=h[5]), Entry(subject=h[4], detail=h[6])],
        ),
        sources=refs,
    )
    return monthly(
        inventory=aggregate,
        changes=aggregate,
        pending=Unavailable(reason=h[3]),
        corrective_actions=aggregate,
    )


def assert_inert(document: str) -> Probe:
    p = probe(document)
    assert set(p.tags) <= ALLOWED_TAGS
    for tag, name, _ in p.attrs:
        assert (tag, name) in ALLOWED_ATTRS, (tag, name)
        assert not name.startswith("on")
        assert name not in {"href", "src"}
    assert "".join(p.style) == CSS
    csp = [m for m in p.meta if m.get("http-equiv") == "Content-Security-Policy"]
    assert [m["content"] for m in csp] == [CSP]
    for tag, name, value in p.attrs:
        assert not any(bad in (value or "") for bad in HOSTILE), (tag, name)
    return p


RENDERERS = [
    pytest.param(hostile_daily, render_daily_html, id="daily"),
    pytest.param(hostile_monthly, render_monthly_html, id="monthly"),
]


@pytest.mark.parametrize(("build", "render"), RENDERERS)
def test_hostile_data_is_inert_text(build, render):
    document = render(build())
    p = assert_inert(document)
    text = "".join(p.text)
    for payload in HOSTILE:
        if escape(payload, quote=True) != payload:
            assert payload not in document
        assert payload in text
    assert "<script" not in document.lower()
    assert "<img" not in document.lower()
    assert "&lt;script&gt;" in document
    assert HOSTILE[0] in text
    assert HOSTILE[4] in text
    assert "&amp;#x3c;" in document


@pytest.mark.parametrize(("build", "render"), RENDERERS)
def test_no_external_resources_or_urls(build, render):
    document = render(build())
    for needle in ("http://", "https://", "//cdn", "url(", "@import", "<link", "<iframe"):
        assert needle not in document


@pytest.mark.parametrize(("build", "render"), RENDERERS)
def test_deterministic(build, render):
    assert render(build()) == render(build())


def test_clean_reports_pass_the_same_inertness_checks():
    assert_inert(render_daily_html(daily()))
    assert_inert(render_monthly_html(monthly()))


@pytest.mark.parametrize(("build", "render"), RENDERERS)
def test_every_table_cites_its_sources_once_or_states_it_is_unavailable(build, render):
    document = render(build())
    p = assert_inert(document)
    assert all(len(r) == 2 for r in p.rows)
    for section in document.split("<section>")[1:]:
        assert (
            "<tfoot>" in section
            and "sha256:" in section
            or "no disponible" in section
            or "calendario" in section
        ), section[:80]


def test_sources_go_once_per_section_not_once_per_row():
    refs = [src("evidence/a"), src("evidence/b")]
    counts = Sourced[CheckCounts](
        value=CheckCounts(counts={CheckStatus.PASS: 2, CheckStatus.FAIL: 1}), sources=refs
    )

    document = render_daily_html(daily(checks=counts))

    section = document.split("<h2>Checks</h2>")[1].split("</section>")[0]
    assert section.count("sha256:") == 2
    assert section.count("<tfoot>") == 1


def test_sourced_rows_show_source_key_hash_and_instant():
    document = render_daily_html(daily())
    assert "evidence/checks/run.json" in document
    assert f"sha256:{SHA}" in document
    assert NOON.astimezone(UTC).isoformat() in document


def test_unavailable_shows_reason_never_zero():
    document = render_daily_html(daily())
    p = probe(document)
    cells = ["".join(r) for r in p.rows if r and r[0] == "activos"]
    assert cells == ["activosno disponible: no disponible"]
    assert "no disponible: no disponible" in document


def test_notices_and_no_compliance_claim():
    notices = [NO_CONFORMITY, REPORT_NOT_A_REVIEW]
    reports = (
        render_daily_html(daily(notices=notices)),
        render_monthly_html(monthly(notices=notices)),
    )
    for document in reports:
        assert NO_CONFORMITY in document
        assert "no sustituye la revisión de una persona" in document
        assert "cumple" not in document.lower()


def test_monthly_partial_coverage_is_flagged():
    document = render_monthly_html(monthly())
    assert "30/31 días" in document
    assert "PARCIAL" in document


def test_cycle_missing_is_stated():
    assert "fuera de un ciclo" in render_daily_html(daily())


def test_naive_equivalent_instants_render_in_utc():
    other = datetime(2026, 11, 1, 13, tzinfo=UTC)
    ref = SourceRef(kind="evidence", key="k", sha256=SHA, at=other)
    document = render_daily_html(
        daily(checks=Sourced[CheckCounts](value=CheckCounts(counts={}), sources=[ref]))
    )
    assert "2026-11-01T13:00:00+00:00" in document


def test_daily_documents_section_shows_state_sources_and_reasons():
    doc = DocumentEntry(
        name="politica", state=DocumentState.APPROVED, approved_at=NOON, reasons=["revisar"]
    )
    document = render_daily_html(
        daily(documents=Sourced[list[DocumentEntry]](value=[doc], sources=[src("docs/politica")]))
    )
    row = next(r for r in probe(document).rows if r[0] == "politica")
    assert "aprobado y vigente" in row[1] and "revisar" in row[1]
    assert "APPROVED" not in document
    assert "docs/politica" in document and f"sha256:{SHA}" in document


def test_daily_backup_shows_outcome_code_and_flags_a_configuration_failure():
    value = BackupStageResult(
        run_id="r1",
        outcome="backup_failed",
        code="not_configured",
        configuration_error=True,
        finished_at=NOON,
    )
    document = render_daily_html(
        daily(backup=Sourced[BackupStageResult](value=value, sources=[src("cycles/r1")]))
    )
    rows = probe(document).rows
    assert any("copia fallida: sin ajustes de copia" in r[1] for r in rows)
    assert "backup_failed" not in document
    assert any(r[0] == "fallo de configuración" for r in rows)


def test_daily_backup_unavailable_states_the_reason():
    document = render_daily_html(daily(backup=Unavailable(reason="ningún ciclo")))
    assert "<h2>Copia de seguridad</h2>" in document
    assert "no disponible: ningún ciclo" in document


def test_daily_documents_unavailable_states_the_reason():
    document = render_daily_html(daily(documents=Unavailable(reason="sin inventario")))
    assert "<h2>Documentos</h2>" in document
    assert "no disponible: sin inventario" in document


def test_monthly_window_in_report_zone_and_utc():
    document = render_monthly_html(monthly(), tz=ZoneInfo("Europe/Madrid"))
    assert "2026-10-01T00:00:00+02:00 / 2026-09-30T22:00:00+00:00 UTC" in document
    assert "2026-10-31T23:00:00+00:00 UTC" in document
    assert "2026-09-30T22:00:00+00:00 UTC" in render_monthly_html(monthly())


def test_monthly_label_and_partial_are_shown():
    aggregate = Sourced[Aggregate](
        value=Aggregate(label="Altas del mes", coverage=Coverage.of(30, 31)), sources=[src("k")]
    )
    p = probe(render_monthly_html(monthly(changes=aggregate)))
    assert ["rótulo", "Altas del mes"] == [
        c for c in next(r for r in p.rows if r[0] == "rótulo")[:2]
    ]
    assert any("PARCIAL" in r[1] for r in p.rows if r[0] == "cobertura")


def test_hostile_document_name_and_label_stay_inert():
    h = HOSTILE
    docs = Sourced[list[DocumentEntry]](
        value=[DocumentEntry(name=h[1], state=DocumentState.GENERATED, reasons=[h[0]])],
        sources=[src("k")],
    )
    d = assert_inert(render_daily_html(daily(documents=docs)))
    assert h[1] in "".join(d.text)
    aggregate = Sourced[Aggregate](
        value=Aggregate(label=h[1], coverage=Coverage.of(31, 31)), sources=[src("k")]
    )
    m = assert_inert(render_monthly_html(monthly(inventory=aggregate)))
    assert h[1] in "".join(m.text)


def test_open_incidents_show_values_sources_and_broken_ones_without_values():
    document = render_daily_html(hostile_daily())

    p = assert_inert(document)
    rows = {r[0]: r for r in p.rows if r and r[0] in {"INC-2026-001", "SIM-2026-001"}}
    assert HOSTILE[0] in rows["INC-2026-001"][1]
    assert "severidad " + HOSTILE[1] in rows["INC-2026-001"][1]
    assert "último registro 1 (opened)" in rows["INC-2026-001"][1]
    assert rows["SIM-2026-001"][1].startswith("NO VERIFICADO: " + HOSTILE[2])
    assert "título" not in rows["SIM-2026-001"][1] and "detectado" not in rows["SIM-2026-001"][1]
    assert "href" not in document


def test_open_incidents_empty_list_still_cites_its_source():
    empty = Sourced[list[IncidentEntry]](value=[], sources=[src("incidents/")])

    document = render_daily_html(daily(open_incidents=empty))

    row = next(r for r in probe(document).rows if r[0] == "incidentes abiertos")
    assert "(sin elementos)" in row[1]
    assert "incidents/" in document and f"sha256:{SHA}" in document


def test_readiness_uses_the_plain_label_and_keeps_the_code_out():
    document = render_daily_html(daily())

    assert any(r == ["preparación", "no preparada"] for r in probe(document).rows)
    assert "NOT_READY" not in document


def test_internal_codes_are_shown_in_plain_spanish():
    refs = [src("k")]
    document = render_daily_html(
        daily(
            assets=Sourced[AssetsSummary](
                value=AssetsSummary(total=2, by_type={"code_hosting": 1, "password_manager": 1}),
                sources=refs,
            ),
            expiring=Sourced[list[ExpiryItem]](
                value=[
                    ExpiryItem(
                        kind="measure_review",
                        subject="op.exp.1",
                        expires_at=NOON,
                        days_left=3,
                        sources=refs,
                    )  # fmt: skip
                ],
                sources=refs,
            ),
            changes=Sourced[Changes](
                value=Changes(
                    base_period="2026-10-31", items=[ChangeItem(subject="x", change="added")]
                ),
                sources=refs,
            ),
        )
    )

    rows = probe(document).rows
    assert ["tipo alojamiento de código", "1"] in rows
    assert ["tipo gestor de contraseñas", "1"] in rows
    assert any(r[0] == "revisión de la medida: op.exp.1" for r in rows)
    assert ["alta", "x"] in rows
    for code in ("code_hosting", "measure_review", "password_manager"):
        assert code not in document


def failed_connector(**fields):
    refs = [src("k")]
    return ConnectorEntry(
        connector="github",
        collection=Sourced[CollectionStatus](value=CollectionStatus.ERROR, sources=refs),
        checks=Unavailable(reason="sin checks"),
        **fields,
    )


def collection_state(document: str) -> str:
    p = probe(document)
    return next(r[1] for r in p.rows if r[0] == "estado")


def test_a_failed_connector_shows_phase_and_error_type_in_plain_words():
    entry = failed_connector(phase="collect", error_type="RuntimeError")

    state = collection_state(render_daily_html(daily(connectors=[entry])))

    assert state == "ERROR · fase: recogida (excepción RuntimeError)"


def test_a_failed_connector_without_phase_shows_only_the_status():
    state = collection_state(render_daily_html(daily(connectors=[failed_connector()])))

    assert state == "ERROR"


def test_an_unknown_phase_is_shown_as_it_comes():
    entry = failed_connector(phase="nueva_fase")

    assert collection_state(render_daily_html(daily(connectors=[entry]))) == (
        "ERROR · fase: nueva_fase"
    )


def test_assets_without_agent_show_the_total_before_the_list():
    refs = [src("k")]
    listing = Sourced[list[str]](value=["lap-ana", "srv-app"], sources=refs)

    rows = probe(render_daily_html(daily(assets_without_agent=listing))).rows

    assert ["total", "2"] in rows
    assert rows.index(["total", "2"]) < rows.index(["sin agente", "lap-ana"])


def test_a_single_human_review_notice_and_capitalised_pre_audit():
    report = daily(notices=[HUMAN_REVIEW_PENDING, NO_PRE_AUDIT, NO_CONFORMITY])

    document = render_daily_html(report)

    assert document.count("Revisión humana pendiente") == 1
    assert "Pre-auditoría no ejecutada" in document
    assert document.count("no declara conformidad") == 1


def test_changes_without_items_say_so_and_interrupted_cycles_use_plain_stage():
    refs = [src("k")]
    document = render_daily_html(
        daily(
            changes=Sourced[Changes](
                value=Changes(base_period="2026-10-30", items=[]), sources=refs
            ),
            interrupted_cycles=Sourced[list[InterruptedCycle]](
                value=[InterruptedCycle(run_id="r1", last_stage="collected", started_at=NOON)],
                sources=refs,
            ),
        )
    )

    rows = probe(document).rows
    assert ["base", "2026-10-30"] in rows
    assert ["cambios", "sin cambios respecto a la base"] in rows
    assert any(r[0] == "r1" and "recogida de datos" in r[1] for r in rows)
    assert "collected" not in document
