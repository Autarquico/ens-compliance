"""`build_snapshot`: precondiciones, determinismo, secretos y proyección de deriva."""

from datetime import timedelta

import pytest
from checks.acceptance_helpers import CHECK_MEASURE, COLLECTED
from matrix.evaluate_helpers import NOW
from risk.helpers import REGISTER, edit

from ens_evidence.assessment.dossier import (
    DossierRejected,
    build_snapshot,
    current_inputs,
    diff_projection,
    drift_projection,
)
from ens_evidence.assessment.dossier.model import INTERNAL_REVIEW_NOTICE, SectionState
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.models.enums import Category, Readiness

from .worlds import INPUTS, PASSWORD, add_risk_analysis, basic_store, edit_answers

# --- precondiciones ---------------------------------------------------------------------------


def test_no_preparada_se_genera_igualmente(basic):
    snapshot = build_snapshot(basic, INPUTS)

    matrix = snapshot.sections[6].content["matrix"]
    assert matrix["readiness"] == Readiness.NOT_READY
    assert snapshot.sections[6].state is SectionState.INCOMPLETE
    assert snapshot.category is Category.BASICA
    assert snapshot.notice is None


def test_sin_categorizacion_vigente_se_rechaza(basic):
    year_later = current_inputs(NOW + timedelta(days=400))

    with pytest.raises(CategorizationNotCurrent):
        build_snapshot(basic, year_later)


def test_sin_declaracion_legible_se_rechaza(tmp_path):
    store = basic_store(tmp_path / "ws", declared=False)

    with pytest.raises(DossierRejected, match="Declaración"):
        build_snapshot(store, INPUTS)


def test_una_declaracion_ilegible_se_rechaza(basic):
    basic.write_text("controls/applicability.yaml", "no: [valido")

    with pytest.raises(DossierRejected, match="Declaración"):
        build_snapshot(basic, INPUTS)


def test_unas_entradas_de_otra_version_no_se_reproducen(basic):
    other = INPUTS.model_copy(update={"product_version": "0.0.0-otra"})

    with pytest.raises(DossierRejected, match="inputs.product_version"):
        build_snapshot(basic, other)


def test_media_lleva_la_etiqueta_de_revision_interna(ready):
    snapshot = build_snapshot(ready.store, INPUTS)

    assert snapshot.category is Category.MEDIA
    assert snapshot.notice == INTERNAL_REVIEW_NOTICE


# --- determinismo -----------------------------------------------------------------------------


def test_con_entradas_congeladas_los_bytes_son_identicos(ready):
    first = build_snapshot(ready.store, INPUTS)
    ready.at(NOW + timedelta(days=3))  # el reloj de la CLI no interviene
    second = build_snapshot(ready.store, INPUTS)

    assert first.model_dump_json() == second.model_dump_json()


def test_la_instantanea_en_basica_tambien_es_determinista(basic):
    assert build_snapshot(basic, INPUTS).model_dump_json() == (
        build_snapshot(basic, INPUTS).model_dump_json()
    )


def test_cambiar_solo_el_reloj_de_referencia_solo_cambia_la_marca(ready):
    other = INPUTS.model_copy(update={"reference_now": NOW + timedelta(hours=1)})

    a, b = build_snapshot(ready.store, INPUTS), build_snapshot(ready.store, other)

    assert a.inputs != b.inputs
    assert a.sections == b.sections
    assert diff_projection(a, b) == {}


# --- secretos ---------------------------------------------------------------------------------


def test_un_secreto_en_el_registro_de_riesgos_impide_generar(ready):
    add_risk_analysis(ready.store)
    edit(ready.store, REGISTER, "Acceso no autorizado a facturas", f"password={PASSWORD}")

    with pytest.raises(DossierRejected) as raised:
        build_snapshot(ready.store, INPUTS)

    assert PASSWORD not in str(raised.value)
    assert any("risk/register.yaml" in e and "credencial" in e for e in raised.value.errors)


def test_un_secreto_en_las_respuestas_organizativas_impide_generar(ready):
    edit_answers(ready.store, "mision", f"Llevamos la contabilidad. password={PASSWORD}")

    with pytest.raises(DossierRejected) as raised:
        build_snapshot(ready.store, INPUTS)

    assert PASSWORD not in str(raised.value)
    assert any("org/answers.yaml.mision" in e for e in raised.value.errors)


def test_el_error_nombra_la_ruta_y_nunca_el_valor_en_ningun_mensaje(ready):
    add_risk_analysis(ready.store)
    edit(ready.store, REGISTER, "Ana Ruiz", f"'api_key: {PASSWORD}'")

    with pytest.raises(DossierRejected) as raised:
        build_snapshot(ready.store, INPUTS)

    assert raised.value.errors
    assert not any(PASSWORD in e for e in raised.value.errors)


def test_el_mundo_sin_secretos_no_da_falsos_positivos(ready):
    add_risk_analysis(ready.store)

    assert build_snapshot(ready.store, INPUTS)


# --- proyección de deriva ---------------------------------------------------------------------


def after_equivalent_check_run(world):
    """Una recogida nueva con el mismo estado y su `check --all`: ejecución y evidencia nuevas."""
    world.collect(at=COLLECTED + timedelta(hours=3))
    world.check_all(NOW - timedelta(minutes=45))


def test_una_ejecucion_nueva_y_equivalente_no_deriva(ready):
    before = build_snapshot(ready.store, INPUTS)
    runs = len(ready.runs())

    after_equivalent_check_run(ready)
    after = build_snapshot(ready.store, INPUTS)

    assert len(ready.runs()) == runs + 1
    assert before.model_dump_json() != after.model_dump_json()
    assert diff_projection(before, after) == {}
    assert drift_projection(before) == drift_projection(after)


def test_una_ejecucion_fallida_no_deriva_pero_la_evaluacion_la_refleja(ready):
    before = build_snapshot(ready.store, INPUTS)

    ready.collect(secret_scanning_off=True, at=COLLECTED + timedelta(hours=3))
    ready.check_all(NOW - timedelta(minutes=45))
    after = build_snapshot(ready.store, INPUTS)

    assert diff_projection(before, after) == {}
    assert before.sections[6].content["matrix"]["readiness"] == Readiness.READY
    assert after.sections[6].content["matrix"]["readiness"] == Readiness.NOT_READY


def test_editar_la_matriz_deriva_en_su_seccion(ready):
    before = build_snapshot(ready.store, INPUTS)

    ready.at(NOW - timedelta(minutes=10))
    assert ready.run("matrix", "set", CHECK_MEASURE, "--every", "9").exit_code == 0
    after = build_snapshot(ready.store, INPUTS)

    drift = diff_projection(before, after)
    assert list(drift) == ["7. Resultado por medida"]
    assert any("controls/matrix.yaml" in line for line in drift["7. Resultado por medida"])
    assert any("review_every_days" in line for line in drift["7. Resultado por medida"])


def test_editar_el_registro_de_riesgos_deriva_en_su_seccion(ready):
    add_risk_analysis(ready.store)
    before = build_snapshot(ready.store, INPUTS)

    edit(ready.store, REGISTER, "Acceso no autorizado a facturas", "Pérdida de facturas")
    after = build_snapshot(ready.store, INPUTS)

    drift = diff_projection(before, after)
    assert "4. Riesgos" in drift
    assert any("risk/register.yaml" in line for line in drift["4. Riesgos"])
    assert any("Pérdida de facturas" in line for line in drift["4. Riesgos"])


def test_la_proyeccion_conserva_las_decisiones_y_quita_lo_temporal(ready):
    projection = drift_projection(build_snapshot(ready.store, INPUTS))

    assert set(projection["inputs"]) == {
        "methodology_version",
        "product_version",
        "catalog_version",
    }
    matrix = projection["sections"][6]["content"]["matrix"]
    assert "generated_at" not in matrix and "readiness" not in matrix
    assert all("blockers" not in row for row in matrix["rows"])
    assert all(
        "timestamps" not in source for s in projection["sections"] for source in s["sources"]
    )
    assert all(
        source["kind"] != "check-run" for s in projection["sections"] for source in s["sources"]
    )
    assert matrix["rows"][0]["last_review"]["record_sha256"]
