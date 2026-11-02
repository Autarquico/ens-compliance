import hashlib
import json
from datetime import timedelta

import pytest
from checks.acceptance_helpers import CHECKED_AT, collect, make_world
from checks.test_inputs import endpoint_result, import_package
from matrix.evaluate_helpers import NOW
from orgdocs.orgdocs_helpers import (
    APPROVED_AT,
    EXAMPLE_SCOPE,
    SCOPE_FILES,
    SIGNATORY,
    fixture_answers,
    write_answers,
)

from ens_evidence.assessment.render import HUMAN_REVIEW_PENDING, NO_CONFORMITY
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.models.enums import CheckStatus, CollectionStatus, Readiness, Role
from ens_evidence.orgdocs import DocumentState, approve_document, build_documents
from ens_evidence.reports.model import (
    ConnectorAttempts,
    CycleInfo,
    Sourced,
    Unavailable,
    render_json,
)
from ens_evidence.reports.store import write_stage
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

from .helpers import put_daily, source_sha256
from .world import (
    ASSETS_LAPTOP,
    CUTOFF,
    PERIOD,
    check_result,
    collection_error_result,
    daily_at,
    source_of,
    static_run,
)


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def ready(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "ready")


def entry(report, connector):
    return next(c for c in report.connectors if c.connector == connector)


def real_source(instant=NOW):
    return StoredCheckResults(reference_now=instant)


# --- workspace vacío, config ausente, historial, evidencia posterior ---------------------------


def test_workspace_vacio_nada_se_presenta_como_cero(store):
    report = daily_at(store, source_of())

    assert isinstance(report.checks, Unavailable)
    assert all(isinstance(c.checks, Unavailable) for c in report.connectors)
    assert all(isinstance(c.collection, Unavailable) for c in report.connectors)
    assert isinstance(report.assets, Unavailable)
    assert isinstance(report.readiness, Unavailable)
    assert isinstance(report.changes, Unavailable)
    assert NO_CONFORMITY in report.notices
    assert "PASS" not in render_json(report).decode().replace("NOT_READY", "")


def test_config_ausente_se_distingue_de_sin_ejecucion(store):
    store.write_text(connector_config_key("github"), "asset_id: gh\n")

    report = daily_at(store, source_of())

    assert "configuración ausente" in entry(report, "cloudflare").checks.reason
    assert entry(report, "github").checks.reason == "sin ejecución de checks al corte"


def test_historial_ilegible_es_unavailable_con_su_motivo(store):
    store.write_bytes("evidence/checks/github/000001-roto.json", b"{")

    report = daily_at(store, real_source(CUTOFF))

    github = entry(report, "github")
    assert isinstance(github.checks, Unavailable)
    assert "historial de checks ilegible" in github.checks.reason
    assert isinstance(github.collection, Unavailable)


def test_historial_ilegible_de_un_conector_no_tapa_a_los_demas(store):
    good = static_run(
        store, "restic", 1, CUTOFF - timedelta(hours=1), check_result(CheckStatus.FAIL)
    )

    report = daily_at(store, source_of(good, problems={"github": ["check_history_unreadable:k"]}))

    assert isinstance(entry(report, "github").checks, Unavailable)
    assert entry(report, "restic").checks.value.counts[CheckStatus.FAIL] == 1


def test_evidencia_posterior_al_ultimo_check_es_unavailable(ready):
    ready.at(NOW)
    before = daily_at(ready.store, real_source(), generated_at=NOW)
    assert entry(before, "github").checks.value.counts[CheckStatus.PASS] > 0

    collect(ready.store, at=CHECKED_AT + timedelta(minutes=10), secret_scanning_off=True)
    after = daily_at(ready.store, real_source(), generated_at=NOW)

    github = entry(after, "github")
    assert isinstance(github.checks, Unavailable)
    assert "evidencia posterior al último check" in github.checks.reason
    assert isinstance(after.checks, Unavailable)  # ningún otro conector tiene ejecución


# --- recuentos por conector --------------------------------------------------------------------


def test_los_recuentos_son_del_ultimo_intento_no_de_uno_anterior(store):
    old = static_run(store, "github", 1, CUTOFF - timedelta(days=1), check_result(CheckStatus.PASS))
    new = static_run(
        store, "github", 2, CUTOFF - timedelta(hours=1), check_result(CheckStatus.FAIL)
    )

    report = daily_at(store, source_of(old, new))

    github = entry(report, "github")
    assert github.checks.value.counts == {
        CheckStatus.PASS: 0,
        CheckStatus.FAIL: 1,
        CheckStatus.UNKNOWN: 0,
        CheckStatus.ERROR: 0,
    }
    assert [s.key for s in github.checks.sources] == [new.run_id]


def test_un_intento_posterior_al_corte_no_cuenta(store):
    early = static_run(
        store, "github", 1, CUTOFF - timedelta(hours=1), check_result(CheckStatus.PASS)
    )
    late = static_run(
        store, "github", 2, CUTOFF + timedelta(hours=1), check_result(CheckStatus.FAIL)
    )

    report = daily_at(store, source_of(early, late))

    assert entry(report, "github").checks.value.counts[CheckStatus.PASS] == 1


def test_error_de_recogida_se_informa_como_estado_de_recogida_y_como_check(store):
    run = static_run(store, "github", 1, CUTOFF - timedelta(hours=1), collection_error_result())

    report = daily_at(store, source_of(run))

    github = entry(report, "github")
    assert github.collection.value is CollectionStatus.ERROR
    assert github.checks.value.counts[CheckStatus.ERROR] == 1


def test_ejecucion_sin_resultados_no_inventa_estado_de_recogida(store):
    run = static_run(store, "github", 1, CUTOFF - timedelta(hours=1))

    report = daily_at(store, source_of(run))

    github = entry(report, "github")
    assert isinstance(github.collection, Unavailable)
    assert isinstance(github.checks, Sourced)


def test_totales_suman_los_conectores_utilizables_y_avisan_de_los_demas(store):
    one = static_run(
        store, "github", 1, CUTOFF - timedelta(hours=1), check_result(CheckStatus.PASS)
    )
    two = static_run(
        store, "restic", 1, CUTOFF - timedelta(hours=1), check_result(CheckStatus.FAIL)
    )

    report = daily_at(store, source_of(one, two))

    assert report.checks.value.counts[CheckStatus.PASS] == 1
    assert report.checks.value.counts[CheckStatus.FAIL] == 1
    assert {s.key for s in report.checks.sources} == {one.run_id, two.run_id}
    assert any(n.startswith("Recuentos parciales") and "cloudflare" in n for n in report.notices)


# --- preparación -------------------------------------------------------------------------------


def status_json(world, instant=NOW):
    world.at(instant)
    return json.loads(world.status(instant, "--format", "json").output)


def status_lines(world, instant=NOW):
    """Del texto de `assessment status`, lo que va entre «Preparación» y las fuentes técnicas."""
    lines = world.status(instant).output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Preparación:"))
    end = next(i for i, line in enumerate(lines) if line.startswith("Fuentes técnicas"))
    return lines[start:end]


@pytest.mark.parametrize("kind", ["linked", "ready"])
def test_readiness_coincide_con_assessment_status_en_el_mismo_instante(kind, tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch, kind)
    expected = status_json(world)

    report = daily_at(world.store, real_source(), generated_at=NOW)

    assert isinstance(report.readiness, Sourced)
    assert report.readiness.value.readiness.value == expected["readiness"]
    assert (report.readiness.value.readiness is Readiness.NOT_READY) == (kind == "linked")
    shown = [line.strip() for line in status_lines(world) if line.startswith("  ")]
    assert report.readiness.value.blockers == shown
    blockers = [line for line in shown if not line.startswith("error: ")]
    assert f"{len(blockers)} bloqueo" in next(
        line for line in status_lines(world) if line.startswith("Preparación:")
    )
    github = next(c for c in expected["checks"] if c["connector"] == "github")
    ours = (
        {k.value: n for k, n in entry(report, "github").checks.value.counts.items()}
        if kind == "ready"
        else None
    )
    assert ours is None or ours == github["counts"]


def test_avisos_de_revision_humana_y_no_conformidad(ready):
    report = daily_at(ready.store, real_source(), generated_at=NOW)

    assert HUMAN_REVIEW_PENDING in report.notices
    assert report.notices[-1] == NO_CONFORMITY


# --- alertas e incidentes ----------------------------------------------------------------------


def test_alertas_criticas_son_unavailable(store):
    report = daily_at(store, source_of())

    assert isinstance(report.critical_alerts, Unavailable)


# --- inventario --------------------------------------------------------------------------------


def test_activo_endpoint_sin_evidencia_de_agente_se_lista(store):
    store.write_text("scope/assets.yaml", ASSETS_LAPTOP)

    report = daily_at(store, source_of())

    assert report.assets.value.total == 1
    assert report.assets.value.by_type == {"laptop": 1}
    assert report.assets_without_agent.value == ["lap-ana"]
    assert report.assets_without_agent.sources[0].key == "scope/assets.yaml"


def test_activo_con_evidencia_de_agente_no_se_lista_y_cita_la_evidencia(store):
    store.write_text("scope/assets.yaml", ASSETS_LAPTOP)
    import_package(store, endpoint_result("lap-ana"))

    report = daily_at(store, source_of(), generated_at=NOW)

    assert report.assets_without_agent.value == []
    assert {s.kind for s in report.assets_without_agent.sources} == {"inventory", "evidence"}


def test_inventario_ilegible_es_unavailable(store):
    store.write_text("scope/assets.yaml", "assets: [\n")

    report = daily_at(store, source_of())

    assert isinstance(report.assets, Unavailable)
    assert isinstance(report.assets_without_agent, Unavailable)


# --- ciclos interrumpidos ----------------------------------------------------------------------


def cycle_info(run_id: str) -> CycleInfo:
    return CycleInfo(
        run_id=run_id,
        trigger="scheduled",
        period=PERIOD,
        due_at=CUTOFF,
        started_at=CUTOFF,
        data_cutoff=CUTOFF,
    )


def test_ciclo_sin_etapa_final_se_lista_como_interrumpido(store):
    write_stage(store, "viejo", "started", at=CUTOFF - timedelta(days=1), period="2026-11-01")
    write_stage(store, "viejo", "collected", at=CUTOFF - timedelta(days=1), period="2026-11-01")

    report = daily_at(store, source_of())

    (interrupted,) = report.interrupted_cycles.value
    assert (interrupted.run_id, interrupted.last_stage) == ("viejo", "collected")


def test_ciclo_terminado_y_ciclo_en_curso_no_son_interrumpidos(store):
    write_stage(store, "hecho", "started", at=CUTOFF, period=PERIOD)
    write_stage(store, "hecho", "backup_skipped", at=CUTOFF, period=PERIOD)
    write_stage(store, "actual", "started", at=CUTOFF, period=PERIOD)

    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert report.interrupted_cycles.value == []
    assert {s.kind for s in report.interrupted_cycles.sources} == {"cycle"}
    assert report.cycle.run_id == "actual"


def test_un_informe_manual_ignora_los_ciclos_posteriores_a_su_corte(store):
    write_stage(store, "futuro", "started", at=CUTOFF + timedelta(days=1), period=PERIOD)

    report = daily_at(store, source_of())

    assert isinstance(report.interrupted_cycles, Sourced)
    assert report.interrupted_cycles.value == []


def test_con_ciclo_real_un_marcador_del_futuro_es_ilegible_no_ignorado(store):
    write_stage(store, "falso", "started", at=CUTOFF + timedelta(days=1), period=PERIOD)
    write_stage(store, "actual", "started", at=CUTOFF, period=PERIOD)

    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert isinstance(report.interrupted_cycles, Unavailable)
    assert "falso" in report.interrupted_cycles.reason


def test_los_ciclos_interrumpidos_citan_solo_sus_propios_marcadores(store):
    write_stage(store, "hecho", "started", at=CUTOFF - timedelta(days=2), period="2026-10-30")
    write_stage(
        store, "hecho", "backup_skipped", at=CUTOFF - timedelta(days=2), period="2026-10-30"
    )
    write_stage(store, "viejo", "started", at=CUTOFF - timedelta(days=1), period="2026-10-31")
    write_stage(store, "viejo", "collected", at=CUTOFF - timedelta(days=1), period="2026-10-31")

    report = daily_at(store, source_of())

    keys = {s.key for s in report.interrupted_cycles.sources}
    assert keys == {
        "evidence/reports/cycles/viejo.started.json",
        "evidence/reports/cycles/viejo.collected.json",
    }


def test_sin_interrumpidos_cita_el_started_del_ciclo_actual_que_no_cambia(store):
    write_stage(store, "actual", "started", at=CUTOFF, period=PERIOD)

    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert [s.key for s in report.interrupted_cycles.sources] == [
        "evidence/reports/cycles/actual.started.json"
    ]


def test_sin_ciclo_actual_ni_interrumpidos_cita_la_huella_del_listado(store):
    write_stage(store, "hecho", "started", at=CUTOFF - timedelta(days=2), period="2026-10-30")
    write_stage(
        store, "hecho", "backup_skipped", at=CUTOFF - timedelta(days=2), period="2026-10-30"
    )

    report = daily_at(store, source_of())

    (listing,) = report.interrupted_cycles.sources
    assert listing.sha256 == source_sha256(store, "evidence/reports/cycles/")


def test_un_conector_con_fallo_generado_por_el_planificador_lleva_fase_y_tipo(store):
    failed = ConnectorAttempts(
        connector="github",
        attempts=3,
        last_status=CollectionStatus.ERROR,
        phase="collect",
        error_type="RuntimeError",
    )
    info = cycle_info("actual").model_copy(update={"connector_attempts": [failed]})

    report = daily_at(store, source_of(), cycle=info)

    github = entry(report, "github")
    assert (github.attempts, github.phase, github.error_type) == (3, "collect", "RuntimeError")
    assert all(
        (c.attempts, c.phase, c.error_type) == (None, None, None)
        for c in report.connectors
        if c.connector != "github"
    )
    assert "RuntimeError" in render_json(report).decode()


def test_sin_ningun_ciclo_terminado_la_copia_dice_que_se_vera_en_el_siguiente_informe(store):
    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert isinstance(report.backup, Unavailable)
    assert "informe del siguiente" in report.backup.reason


def test_la_copia_es_la_del_ciclo_anterior_con_su_codigo_tipado(store):
    write_stage(store, "ayer", "started", at=CUTOFF - timedelta(days=1), period="2026-10-31")
    write_stage(
        store,
        "ayer",
        "backup_failed",
        at=CUTOFF - timedelta(days=1),
        period="2026-10-31",
        backup_code="not_configured",
    )

    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert report.backup.value.run_id == "ayer"
    assert (report.backup.value.code, report.backup.value.configuration_error) == (
        "not_configured",
        True,
    )


def _backed_up_yesterday(store, status):
    key = "evidence/backups/runs/ayer.json"
    finished = CUTOFF - timedelta(days=1)
    record = {"run_id": "ayer", "status": status, "started_at": finished.isoformat(),
              "finished_at": finished.isoformat(), "repository": "r"}  # fmt: skip
    if status == "completed":
        record |= {"snapshot_id": "abcd1234", "inventory": {}}
    else:
        record |= {"error": "boom"}
    store.write_bytes(key, json.dumps(record).encode())
    write_stage(store, "ayer", "started", at=finished, period="2026-10-31")
    write_stage(store, "ayer", "backed_up", at=finished, period="2026-10-31", reference=key)


def test_la_copia_hecha_se_contrasta_con_su_registro(store):
    _backed_up_yesterday(store, "completed")

    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert report.backup.value.outcome == "backed_up"


def test_marcador_de_copia_hecha_con_registro_fallido_es_unavailable(store):
    _backed_up_yesterday(store, "failed")

    report = daily_at(store, source_of(), cycle=cycle_info("actual"))

    assert isinstance(report.backup, Unavailable)
    assert "no coincide con su registro" in report.backup.reason


def test_marcador_de_ciclo_ilegible_es_unavailable(store):
    write_stage(store, "roto", "started", at=CUTOFF, period=PERIOD)
    store.write_bytes("evidence/reports/cycles/roto.collected.json", b"{")

    report = daily_at(store, source_of())

    assert isinstance(report.interrupted_cycles, Unavailable)
    assert "roto" in report.interrupted_cycles.reason


# --- cambios y caducidades en el informe -------------------------------------------------------


def test_primer_informe_no_dice_sin_cambios(store):
    report = daily_at(store, source_of())

    assert isinstance(report.changes, Unavailable)
    assert "no hay informe diario anterior" in report.changes.reason


def test_con_base_los_cambios_citan_el_informe_anterior(store):
    previous = daily_at(
        store, source_of(), generated_at=CUTOFF - timedelta(days=1), period="2026-11-01"
    )
    put_daily(store, previous, CUTOFF - timedelta(days=1))

    report = daily_at(store, source_of())

    assert isinstance(report.changes, Sourced)
    assert report.changes.value.base_period == "2026-11-01"
    assert report.changes.sources[0].kind == "report"


def test_base_corrupta_deja_los_cambios_en_unavailable(store):
    previous = daily_at(
        store, source_of(), generated_at=CUTOFF - timedelta(days=1), period="2026-11-01"
    )
    pair = put_daily(store, previous, CUTOFF - timedelta(days=1))
    (store._root / pair.json_key).write_bytes(b"{")

    report = daily_at(store, source_of())

    assert isinstance(report.changes, Unavailable)


def test_caducidad_proxima_aparece_con_su_fuente(store):
    store.write_text(
        "risk/treatment-plan.yaml",
        "actions:\n"
        "  - {id: A1, risk: R1, description: d, owner: o, due: 2026-11-01, status: pending}\n",
    )

    report = daily_at(store, source_of())

    (item,) = report.expiring.value
    assert (item.kind, item.subject, item.days_left) == ("treatment_action", "A1", -1)
    assert item.sources[0].key == "risk/treatment-plan.yaml"


# --- determinismo y trazabilidad ----------------------------------------------------------------


def test_mismo_workspace_y_mismos_argumentos_dan_los_mismos_bytes(ready):
    first = render_json(daily_at(ready.store, real_source(), generated_at=NOW))
    second = render_json(daily_at(ready.store, real_source(), generated_at=NOW))

    assert first == second


def walk(node, path="$"):
    """Todo `Sourced`/`Unavailable` del documento con su ruta."""
    if isinstance(node, dict):
        if "value" in node and "sources" in node:
            yield path, node
        elif set(node) == {"reason"}:
            yield path, node
        else:
            for key, child in node.items():
                yield from walk(child, f"{path}.{key}")
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from walk(child, f"{path}[{i}]")


def test_cada_dato_tiene_fuentes_reales_o_es_unavailable(ready):
    report = daily_at(ready.store, real_source(), generated_at=NOW)
    document = json.loads(render_json(report))

    found = list(walk(document))
    assert found
    sourced = 0
    for path, node in found:
        if "reason" in node:
            assert node["reason"], path
            continue
        sourced += 1
        assert node["sources"], path
        for source in node["sources"]:
            real = source_sha256(ready.store, source["key"])
            assert real == source["sha256"], (path, source["key"])
    assert sourced >= 4


def test_el_informe_no_declara_cumplimiento(ready):
    text = render_json(daily_at(ready.store, real_source(), generated_at=NOW)).decode().lower()

    assert "cumple" not in text.replace("no declara conformidad", "")
    assert "compliant" not in text


# --- documentos organizativos (sin caducidad, ADR 0010) -------------------------------------------


@pytest.fixture
def documented(tmp_path):
    store = LocalStore(tmp_path / "docs-ws")
    for name in SCOPE_FILES:
        store.write_text(
            f"scope/{name}.yaml", (EXAMPLE_SCOPE / f"{name}.yaml").read_text(encoding="utf-8")
        )
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, APPROVED_AT)
    write_answers(store, fixture_answers())
    build_documents(store)
    return store


def test_documentos_sin_generar_son_unavailable(store):
    report = daily_at(store, source_of())

    assert isinstance(report.documents, Unavailable)
    assert "ningún documento" in report.documents.reason


def test_documentos_generados_citan_el_fichero_y_su_estado(documented):
    report = daily_at(documented, source_of())

    assert {e.state for e in report.documents.value} == {DocumentState.GENERATED}
    assert {s.kind for s in report.documents.sources} == {"document"}
    for source in report.documents.sources:
        data = documented.read_bytes(source.key)
        assert hashlib.sha256(data).hexdigest() == source.sha256


def test_documento_aprobado_cita_su_aprobacion_y_no_caduca(documented):
    approve_document(
        documented, "politica-seguridad", SIGNATORY[Role.MANAGEMENT], Role.MANAGEMENT, APPROVED_AT
    )

    report = daily_at(documented, source_of(), generated_at=CUTOFF + timedelta(days=900))

    entry = next(e for e in report.documents.value if e.name == "politica-seguridad")
    assert (entry.state, entry.approved_at) == (DocumentState.APPROVED, APPROVED_AT)
    assert any(s.kind == "approval" for s in report.documents.sources)
    expiring = report.expiring.value if isinstance(report.expiring, Sourced) else []
    assert not [i for i in expiring if "politica" in i.subject]


def test_documento_editado_a_mano_tras_aprobar_es_obsoleto(documented):
    approve_document(
        documented, "politica-seguridad", SIGNATORY[Role.MANAGEMENT], Role.MANAGEMENT, APPROVED_AT
    )
    key = "org/docs/politica-seguridad.md"
    documented.write_text(key, documented.read_text(key) + "\nedición\n")

    report = daily_at(documented, source_of())

    entry = next(e for e in report.documents.value if e.name == "politica-seguridad")
    assert entry.state is DocumentState.OBSOLETE
    assert entry.reasons
