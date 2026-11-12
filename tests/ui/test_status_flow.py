"""El estado dice cuál es el siguiente paso de todo el flujo y cuenta los bloqueos como la CLI."""

import re
from dataclasses import replace

import pytest

from ens_evidence.applicability import DeclarationState
from ens_evidence.assessment import AssessmentStatus, render_status_text
from ens_evidence.matrix import AssessmentResult
from ens_evidence.matrix.messages import BLOCKER_TEXTS, _fill
from ens_evidence.models.enums import Readiness, Role
from ens_evidence.models.readiness import Blocker, BlockerKind, ReadinessResult
from ens_evidence.scope import approve_scope
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import BLOCKER_LABELS
from ens_evidence.ui.views import status as status_view
from ens_evidence.ui.views.status import FLOW_COMPLETE, Phase, plain_text
from ens_evidence.ui.wizard import BY_SLUG

from .conftest import NOW

METODOLOGIA_TITLE = BY_SLUG["riesgos-metodologia"].title


def fake_status(*blockers: Blocker, errors: tuple[str, ...] = ()) -> AssessmentStatus:
    readiness = ReadinessResult(
        readiness=Readiness.NOT_READY if blockers else Readiness.READY,
        blockers=list(blockers),
        pending_human_review=True,
    )
    result = AssessmentResult(
        evaluated_at=NOW,
        declaration_state=DeclarationState.CURRENT,
        rows=(),
        blockers=(),
        readiness=readiness,
        errors=errors,
    )
    return AssessmentStatus(result, ())


def missing(measure: str, detail: str = "") -> Blocker:
    return Blocker(kind=BlockerKind.MISSING_EVIDENCE, measure_id=measure, detail=detail)


def next_step_of(page: str) -> tuple[str | None, str]:
    section = page.split('id="h-sig"')[1].split("</section>")[0]
    link = re.search(r'href="([^"]+)">([^<]+)<', section)
    if link:
        return link.group(1), link.group(2)
    return None, re.sub(r"<[^>]+>", "", section.split("</h2>")[1]).strip()


# --- el siguiente paso sale del estado real ------------------------------------------------------


def test_siguiente_paso_sin_aprobar_el_alcance_es_el_primer_paso_del_asistente_que_falta(
    session, example_store
):
    step = BY_SLUG["aprobacion-alcance"]

    assert next_step_of(session.get("/").text) == (f"/asistente/{step.slug}", step.title)


def test_siguiente_paso_tras_el_alcance_es_la_categorizacion(session, example_store):
    approve_scope(example_store, "Ana", Role.SYSTEM_OWNER, NOW)

    url, _ = next_step_of(session.get("/").text)

    assert url == "/asistente/informacion"


def test_siguiente_paso_tras_la_categorizacion_son_los_riesgos(completed):
    page = completed.get("/").text

    assert next_step_of(page) == (
        "/asistente/riesgos-metodologia",
        METODOLOGIA_TITLE,
    )


def test_con_la_metodologia_adoptada_toca_registrar_riesgos(completed):
    completed.post(
        "/asistente/riesgos-metodologia",
        {"name": "MAGERIT v3", "reference": "Guía MAGERIT", "adaptation": "Tres niveles"},
    )

    url, _ = next_step_of(completed.get("/").text)

    assert url == "/asistente/riesgos"


def test_el_recorrido_marca_lo_hecho_lo_que_toca_y_lo_pendiente(completed):
    page = completed.get("/").text
    phases = re.findall(
        r"<li>\s*(?:<svg.*?</svg>)?\s*(?:<strong>|<span>)([^<]+)<.*?· (\w+)<", page, re.S
    )

    assert phases[:4] == [
        ("Alcance", "Hecho"),
        ("Categorización", "Hecho"),
        ("Riesgos", "Ahora"),
        ("Aplicabilidad", "Pendiente"),
    ]
    assert [name for name, _ in phases][4:] == [
        "Documentos",
        "Matriz",
        "Expediente",
        "Pre-auditoría",
    ]


def stub(key: str, done: bool, calls: list[str]):
    def compute(store, now, status):
        calls.append(key)
        return Phase(key, key.title(), done, f"/{key}", f"Hacer {key}")

    return compute


def test_las_fases_posteriores_no_se_calculan_si_una_falta(monkeypatch, session):
    calls: list[str] = []
    monkeypatch.setattr(
        status_view,
        "PHASES",
        (stub("a", True, calls), stub("b", False, calls), stub("c", True, calls)),
    )
    monkeypatch.setattr(status_view, "PHASE_LABELS", (("a", "A"), ("b", "B"), ("c", "C")))

    page = session.get("/").text

    assert calls == ["a", "b"]
    assert next_step_of(page) == ("/b", "Hacer b")


def test_con_todo_hecho_no_hay_enlace_y_lo_dice(monkeypatch, session):
    calls: list[str] = []
    monkeypatch.setattr(status_view, "PHASES", (stub("a", True, calls),))
    monkeypatch.setattr(status_view, "PHASE_LABELS", (("a", "A"),))

    assert next_step_of(session.get("/").text) == (None, FLOW_COMPLETE)


def test_documentos_sin_respuestas_llevan_al_cuestionario(store):
    phase = status_view._documents(UiStore(store), NOW, None)

    assert not phase.done
    assert phase.url.startswith("/asistente/documentos-")
    assert phase.url != "/asistente/documentos-generar"


def test_matriz_lleva_a_la_primera_medida_con_bloqueo(store):
    status = fake_status(missing("op.acc.5"), missing("mp.if.1"))

    phase = status_view._matrix(UiStore(store), NOW, status)

    assert (phase.done, phase.url) == (False, "/matriz/op.acc.5")


def test_matriz_sin_bloqueos_esta_hecha(store):
    assert status_view._matrix(UiStore(store), NOW, fake_status()).done


def test_un_hallazgo_abierto_no_manda_a_la_matriz_sino_a_la_preauditoria(store):
    finding = Blocker(
        kind=BlockerKind.OPEN_FINDING, measure_id="op.acc.5", detail="preaudit_finding:1"
    )
    status = fake_status(finding)

    assert status_view._matrix(UiStore(store), NOW, status).done
    phase = status_view._preaudit(UiStore(store), NOW, status)
    assert (phase.done, phase.url) == (False, "/preauditoria")
    assert "hallazgos" in phase.action


def test_sin_expediente_el_paso_es_generarlo(store):
    phase = status_view._dossier(UiStore(store), NOW, None)

    assert (phase.done, phase.url) == (False, "/expediente")


# --- el recuento coincide con `assessment status` ------------------------------------------------


def test_el_recuento_de_bloqueos_es_el_de_la_cli(monkeypatch, session):
    blockers = [
        missing("op.acc.5"),
        missing("op.acc.5", "check_no_results:edr"),
        missing("op.acc.5", "clause:a"),
        missing("op.acc.5", "clause:b"),
        missing("mp.if.1"),
    ]
    status = fake_status(*blockers)
    monkeypatch.setattr(status_view, "assessment_status", lambda *args: status)
    cli_line = render_status_text(status).splitlines()[2]

    page = session.get("/").text

    assert re.search(r"(\d+) bloqueos? en (\d+) medidas?", cli_line)
    assert cli_line.removeprefix("Preparación: no preparada, ").rstrip(".") in page
    assert len(blockers) == 5
    assert "5 bloqueos" not in page


def test_los_errores_de_lectura_se_muestran(monkeypatch, session):
    monkeypatch.setattr(
        status_view, "assessment_status", lambda *args: fake_status(errors=("risk/register.yaml",))
    )

    assert "risk/register.yaml" in session.get("/").text


# --- lenguaje llano ------------------------------------------------------------------------------


def test_lo_pendiente_de_revision_no_se_llama_desfavorable(session):
    assert BLOCKER_LABELS[BlockerKind.NOT_COMPLIANT] != "Medida desfavorable"
    assert "Medida desfavorable" not in session.get("/").text


def test_el_bloqueo_de_lo_no_revisado_dice_sin_revisar():
    blocker = Blocker(kind=BlockerKind.NOT_COMPLIANT, measure_id="op.acc.5", detail="NOT_ASSESSED")

    [group] = status_view.blocker_groups([blocker])

    assert group["texts"] == ["Sin revisar: nadie ha valorado esta medida todavía."]


@pytest.mark.parametrize("key", sorted(BLOCKER_TEXTS, key=str))
def test_ningun_texto_deja_ordenes_de_la_cli(key):
    text = plain_text(_fill(BLOCKER_TEXTS[key], "op.acc.5", ["EV-1", "b"]))

    assert not re.search(r"--|\bmatrix \w|\bdocs \w|ens-evidence|evidence import|incident \w", text)
    assert "Siguiente paso" not in text
    assert text.endswith(".") and ".." not in text


# --- una sola fuente de verdad con el asistente ---------------------------------------------------


def test_el_estado_pregunta_a_los_pasos_del_asistente(monkeypatch, session, example_store):
    sistema = BY_SLUG["sistema"]
    monkeypatch.setattr(status_view, "STEPS", (replace(sistema, done=lambda *_: False),))
    assert next_step_of(session.get("/").text)[0] == "/asistente/sistema"

    monkeypatch.setattr(status_view, "STEPS", (replace(sistema, done=lambda *_: True),))
    assert status_view._scope(UiStore(example_store), NOW, None).done


# --- el resumen y el recorrido ------------------------------------------------------------


def test_el_resumen_no_cuenta_general_como_medida(monkeypatch, session):
    general = Blocker(kind=BlockerKind.UNDETERMINED_APPLICABILITY, measure_id=None)
    monkeypatch.setattr(status_view, "assessment_status", lambda *a: fake_status(general))

    page = session.get("/").text

    assert "1 bloqueo" in page
    assert "medida." not in page.split("Preparación:")[1].split("</p>")[0]
    assert " en 0 medidas" not in page


def test_con_una_fase_previa_pendiente_los_bloqueos_remiten_al_recorrido(completed):
    page = completed.get("/").text

    assert "Estos bloqueos se resuelven siguiendo el recorrido de arriba" in page
    recorrido, falta = page.split('id="h-bloq"')
    assert "es el documento que dice qué medidas del ENS te tocan" in recorrido
    assert "es el documento que dice qué medidas del ENS te tocan" not in falta
