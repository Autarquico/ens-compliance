import re
from datetime import timedelta

import pytest
from applicability.helpers import (
    ABSENT_REASON,
    LATER,
    absent,
    decide,
    decide_all_undetermined,
    prepared_workspace,
    uniform,
)

from ens_evidence.applicability import (
    approve_declaration,
    declaration_status,
    propose_applicability,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import current_catalog_version
from ens_evidence.matrix import (
    ManualEvidenceLink,
    assess,
    render_declaration,
)
from ens_evidence.models.enums import Applicability

from .evaluate_helpers import NOW, ONE_CLAUSE, FakeDocuments, configure, entry, record
from .helpers import COMPENSATION, SIGNED_AT
from .source_helpers import manual_evidence

SIGNED_BY = "Luis Pardo"
NAMES = {m.id: m.name for m in load_catalog().measures}


def view(store, **kwargs):
    return render_declaration(store, NOW, **kwargs)


def section(text: str, title: str) -> str:
    start = text.index(f"## {title}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


@pytest.fixture
def with_absent(ready):
    decide(
        ready,
        "op.acc.5",
        Applicability.NOT_APPLICABLE,
        ABSENT_REASON,
        absent_assets=absent("usuarios externos"),
    )
    return approve_declaration(ready, SIGNED_BY, SIGNED_AT) and ready


# --- cabecera ---------------------------------------------------------------------------------


def test_la_cabecera_lleva_firmante_rol_fecha_categoria_catalogo_huellas_y_estado(signed):
    declaration = declaration_status(signed, NOW).declaration

    text = view(signed)

    version = current_catalog_version(load_catalog())
    assert text.startswith("# Declaración de Aplicabilidad")
    assert f"Firmante: {SIGNED_BY} (responsable de la seguridad)" in text
    assert declaration.approved_at.isoformat() in text
    assert f"Categoría del sistema: {declaration.category.value}" in text
    assert (
        f"consolidado el {version.consolidation_date}, sha256 `{version.snapshot_sha256}`" in text
    )
    for fingerprint in (
        declaration.measures_sha256,
        declaration.categorization_sha256,
        declaration.scope_fingerprint,
    ):
        assert f"`{fingerprint}`" in text
    assert "Estado: vigente" in text
    assert NOW.isoformat() in text
    assert "no se autentica" in text


def test_cita_el_articulo_28_2_y_28_3_literales_y_ningun_otro(signed):
    text = view(signed)

    assert (
        "«La relación de medidas de seguridad seleccionadas se formalizará en un documento "
        "denominado Declaración de Aplicabilidad, firmado por el responsable de la seguridad.»"
    ) in text
    assert (
        "«Como parte integral de la Declaración de Aplicabilidad se indicará, de forma "
        "detallada, la correspondencia entre las medidas compensatorias implantadas y las "
        "medidas del anexo II que compensan.»"
    ) in text
    cited = {c.capitalize() for c in re.findall(r"[Aa]rt(?:ículo|\.)\s*\d+(?:\.\d+)?", text)}
    assert cited == {"Art. 28.2", "Art. 28.3"}


def test_con_la_declaracion_obsoleta_el_estado_lleva_sus_motivos(signed):
    key = "controls/measures.yaml"
    signed.write_text(key, signed.read_text(key) + "\n")
    status = declaration_status(signed, NOW)

    text = view(signed)

    assert "Estado: no vigente (algo cambió después de firmarla)" in text
    assert status.errors
    assert all(f"  - {error}" in text for error in status.errors)


@pytest.mark.parametrize("prepare", ["ready"])
def test_sin_declaracion_firmada_solo_muestra_el_estado(request, prepare):
    store = request.getfixturevalue(prepare)

    text = view(store)

    assert "Estado: sin firmar" in text
    assert "No hay una Declaración firmada y legible que mostrar." in text
    assert "## Medidas" not in text


# --- contenido --------------------------------------------------------------------------------


def test_las_seleccionadas_llevan_nombre_del_catalogo_origen_y_refuerzos(signed):
    declaration = declaration_status(signed, NOW).declaration
    text = view(signed)

    exigidas = section(text, "Medidas seleccionadas exigidas por el anexo II")
    adicionales = section(text, "Medidas seleccionadas adicionales")
    required = next(m for m in declaration.selected if m.required_by and m.required_reinforcements)
    assert f"**{required.id} — {NAMES[required.id]}**" in exigidas
    assert f"exigida por el anexo II: {required.required_by}" in exigidas
    assert f"Refuerzos exigidos: {', '.join(required.required_reinforcements)}" in exigidas
    assert f"**op.cont.2 — {NAMES['op.cont.2']}** · adicional (no la exige el anexo II)" in (
        adicionales
    )
    assert "Refuerzos elegidos:" in exigidas


def test_los_refuerzos_elegidos_y_sus_grupos_alternativos_se_muestran(signed):
    declaration = declaration_status(signed, NOW).declaration
    chosen = next(m for m in declaration.selected if m.alternatives and m.chosen_reinforcements)

    text = view(signed)

    line = next(
        line
        for line in text.splitlines()
        if line.startswith("  - Refuerzos elegidos")
        and any(c in line for c in chosen.chosen_reinforcements)
    )
    group = next(g for g in chosen.alternatives if chosen.chosen_reinforcements[0] in g)
    assert f"grupos alternativos [{' o '.join(group)}" in line


def test_las_compensaciones_muestran_la_correspondencia_y_su_justificacion(signed):
    text = view(signed)

    compensations = section(text, "Compensaciones (1)")
    assert f"### op.pl.1 — {NAMES['op.pl.1']}" in compensations
    assert "Obligaciones del anexo II que se compensan: base" in compensations
    assert COMPENSATION.measures[0] in compensations
    assert f"Justificación: {COMPENSATION.justification}" in compensations
    assert "Compensada: ver «Compensaciones»." in section(text, "Medidas seleccionadas exigidas")


def test_las_no_seleccionadas_llevan_su_justificacion(signed):
    declaration = declaration_status(signed, NOW).declaration
    first = declaration.not_selected[0]

    text = view(signed)

    block = section(text, f"Medidas no seleccionadas ({len(declaration.not_selected)})")
    assert f"**{first.id} — {NAMES[first.id]}**: {first.justification}" in block


def test_las_exigidas_sin_activo_llevan_tipo_de_activo_y_justificacion(with_absent):
    text = view(with_absent)

    block = section(text, "Exigidas y no aplicables por ausencia de activo (1)")
    assert f"**op.acc.5 — {NAMES['op.acc.5']}**" in block
    assert "tipo de activo ausente: usuarios externos" in block
    assert ABSENT_REASON in block


def test_sin_compensaciones_ni_ausencias_lo_dice(tmp_path):
    store = prepared_workspace(tmp_path / "ws", uniform("MEDIO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    approve_declaration(store, SIGNED_BY, SIGNED_AT)

    text = view(store)

    assert "## Compensaciones (0)" in text
    assert section(text, "Compensaciones (0)").rstrip().endswith("Ninguna.")
    assert (
        section(text, "Exigidas y no aplicables por ausencia de activo (0)")
        .rstrip()
        .endswith("Ninguna.")
    )


# --- regla 12 ---------------------------------------------------------------------------------


def test_la_vista_nunca_dice_cumple(signed):
    evidence_id = manual_evidence(signed, valid_until=NOW + timedelta(days=365))
    matrix_entry = entry(ONE_CLAUSE, ManualEvidenceLink(evidence_id=evidence_id, covers=["base"]))
    configure(signed, matrix_entry)
    record(signed, matrix_entry, evidence_ids=[evidence_id])
    evaluation = assess(signed, NOW, documents=FakeDocuments())

    for text in (view(signed), view(signed, evaluation=evaluation)):
        assert not re.search(r"cumpl", text, re.IGNORECASE)


def test_el_markdown_de_los_textos_libres_va_escapado(ready):
    decide(
        ready,
        "op.cont.3",
        Applicability.NOT_APPLICABLE,
        "No aplica | *seguro* [x](http://e) <i>y</i>\n# otra línea",
    )
    approve_declaration(ready, SIGNED_BY, SIGNED_AT)

    text = view(ready)

    assert r"No aplica \| \*seguro\* \[x\](http://e) \<i\>y\</i\> \# otra línea" in text
    assert "<i>" not in text


# --- anotada con la matriz --------------------------------------------------------------------


def test_sin_evaluacion_no_hay_anotaciones(signed):
    assert "En la matriz" not in view(signed)


def test_anotada_dice_implantada_segun_la_revision_de_quien_y_cuando(signed):
    evidence_id = manual_evidence(signed, valid_until=NOW + timedelta(days=365))
    matrix_entry = entry(ONE_CLAUSE, ManualEvidenceLink(evidence_id=evidence_id, covers=["base"]))
    configure(signed, matrix_entry)
    reviewed = record(signed, matrix_entry, evidence_ids=[evidence_id])
    evaluation = assess(signed, NOW, documents=FakeDocuments())

    text = view(signed, evaluation=evaluation)

    block = text[text.index(f"**{ONE_CLAUSE} — ") :]
    block = block[: block.index("\n- **")]
    day = reviewed.review.reviewed_at.date()
    assert f"En la matriz: implantada según la revisión de Ana el {day}" in block
    assert "Responsable: Ana (administrador del sistema); periodicidad: 30 días" in block
    assert (
        f"Última revisión: Ana (administrador del sistema), {day} "
        "· conforme según la persona que revisa" in block
    )
    assert f"Evidencias vigentes: {evidence_id}" in block
    assert "Bloqueos: —" in block


def test_anotada_una_medida_sin_revision_no_consta_como_implantada_y_lista_bloqueos(signed):
    evaluation = assess(signed, NOW, documents=FakeDocuments())

    text = view(signed, evaluation=evaluation)

    block = text[text.index(f"**{ONE_CLAUSE} — ") :]
    block = block[: block.index("\n- **")]
    assert "En la matriz: sin revisar" in block
    assert "Responsable: sin responsable; periodicidad: sin periodicidad" in block
    assert "Última revisión: sin revisar" in block
    assert "Evidencias vigentes: ninguna" in block
    assert "sin revisar: nadie ha valorado esta medida todavía" in block
    assert "MISSING\\_REVIEW" not in block


def test_anotada_las_no_seleccionadas_muestran_el_estado_de_su_decision(signed):
    evaluation = assess(signed, NOW, documents=FakeDocuments())

    text = view(signed, evaluation=evaluation)

    block = section(text, "Medidas no seleccionadas")
    assert "decisión de no aplicabilidad sin revisar" in block


# --- ronda 1: la compensación nunca se presenta como la medida literal -------------------------


def implanted_compensation(store):
    evidence_id = manual_evidence(store, valid_until=NOW + timedelta(days=365))
    matrix_entry = entry(
        "op.pl.1", ManualEvidenceLink(evidence_id=evidence_id, covers=["compensation", "R1"])
    )
    configure(store, matrix_entry)
    record(store, matrix_entry, evidence_ids=[evidence_id])
    return assess(store, NOW, documents=FakeDocuments())


def test_anotada_una_compensacion_implantada_dice_con_que_medidas_se_sustituye(signed):
    text = view(signed, evaluation=implanted_compensation(signed))

    block = text[text.index("**op.pl.1 — ") :]
    block = block[: block.index("\n- **")]
    verdict = next(line for line in block.splitlines() if "En la matriz:" in line)
    assert "implantada mediante las medidas compensatorias" in verdict
    assert COMPENSATION.measures[0] in verdict


def test_anotada_la_seccion_de_compensaciones_no_las_llama_implantadas_y_anota_la_medida(signed):
    text = view(signed, evaluation=implanted_compensation(signed))

    compensations = section(text, "Compensaciones (1)")
    assert "Medidas compensatorias (art. 28.3):" in compensations
    assert "Medidas compensatorias implantadas" not in text
    assert "- En la matriz: implantada mediante las medidas compensatorias" in compensations


def test_una_compensacion_sin_revisar_no_figura_como_implantada(signed):
    evaluation = assess(signed, NOW, documents=FakeDocuments())

    text = view(signed, evaluation=evaluation)

    block = text[text.index("**op.pl.1 — ") :]
    block = block[: block.index("\n- **")]
    assert "En la matriz: sin revisar" in block
    assert "implantada" not in block
