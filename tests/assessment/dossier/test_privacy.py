"""Política de texto exportable: texto libre frente a mensajes de la herramienta."""

from datetime import timedelta

from checks.acceptance_helpers import COLLECTED
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier import build_snapshot
from ens_evidence.assessment.dossier.privacy import export_problems, free_text_problems
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.models.enums import Readiness

from .worlds import INPUTS

FUTURE_RUN = "check_run_in_future:github:2026-06-01 00h30 UTC"


def test_un_mensaje_de_la_herramienta_con_fecha_no_es_un_dato_personal():
    content = {"matrix": {"blockers": [{"detail": FUTURE_RUN}], "errors": [FUTURE_RUN]}}

    assert export_problems("", content) == []


def test_el_texto_libre_con_un_telefono_se_rechaza_nombrando_la_ruta():
    problems = export_problems(
        "sección 4: ", {"register": {"risks": [{"threat": "llama al 612345678"}]}}
    )

    assert problems == ["sección 4: register.risks[0].threat: contiene un teléfono"]
    assert "612345678" not in " ".join(problems)


def test_una_credencial_en_un_mensaje_o_en_una_clave_se_rechaza():
    content = {
        "detail": "password: Hunter2xyz",
        "manifest_key": "x/password=abc123",
        "errors": ["ok"],
    }

    assert sorted(p.split(":")[0] for p in export_problems("", content)) == [
        "detail",
        "manifest_key",
    ]


def test_una_huella_sha256_no_se_revisa():
    assert export_problems("", {"sha256": "6" * 64}) == []


def test_una_ejecucion_con_fecha_futura_genera_un_expediente_not_ready(ready):
    ready.collect(at=COLLECTED + timedelta(hours=3))
    ready.check_all(NOW + timedelta(hours=2))

    snapshot = build_snapshot(
        ready.store, INPUTS, checks=StoredCheckResults(reference_now=INPUTS.reference_now)
    )

    assert snapshot.sections[6].content["matrix"]["readiness"] == Readiness.NOT_READY


def test_un_texto_suelto_con_datos_se_rechaza_con_un_solo_separador():
    problems = free_text_problems("conclusiones", "escribe a ana@example.com")

    assert problems == ["conclusiones: contiene un correo electrónico"]


def test_con_una_ejecucion_futura_al_generar_la_reproduccion_conserva_el_bloqueo(ready):
    from ens_evidence.assessment.dossier.build import diff_projection

    ready.collect(at=COLLECTED + timedelta(hours=3))
    ready.check_all(NOW + timedelta(hours=2))
    generated = build_snapshot(
        ready.store, INPUTS, checks=StoredCheckResults(reference_now=INPUTS.reference_now)
    )

    replayed = build_snapshot(ready.store, generated.inputs)

    assert replayed == generated
    assert replayed.sections[6].content["matrix"]["readiness"] == Readiness.NOT_READY
    assert "check_run_in_future:github:" in str(replayed.sections[6].content["matrix"])
    assert diff_projection(generated, replayed) == {}


def test_una_ejecucion_posterior_a_generar_no_cuenta_al_reproducir(ready):
    generated = build_snapshot(ready.store, INPUTS)
    ready.check_all(NOW + timedelta(hours=2))

    assert build_snapshot(ready.store, generated.inputs) == generated
