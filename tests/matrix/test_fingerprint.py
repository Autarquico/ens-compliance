import pytest

from ens_evidence.applicability.declaration import (
    AbsentAssetsRecord,
    CompensationRecord,
    NotSelected,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.model import Clause
from ens_evidence.matrix import (
    CheckLink,
    DocumentLink,
    ManualEvidenceLink,
    MatrixEntry,
    links_sha256,
    measure_context_sha256,
)

EVIDENCE = "EV-20260601-0a1b2c3d"
OTHER = "EV-20260602-1a2b3c4d"


def entry(*links) -> MatrixEntry:
    return MatrixEntry(id="org.1", links=list(links))


def evidence(covers=("base",), evidence_id=EVIDENCE) -> ManualEvidenceLink:
    return ManualEvidenceLink(evidence_id=evidence_id, covers=list(covers))


class TestLinks:
    def test_es_estable_y_no_depende_del_orden(self):
        a, b = evidence(), CheckLink(check_id="HOST-001", covers=["base"])

        assert links_sha256(entry(a, b)) == links_sha256(entry(b, a))
        assert len(links_sha256(entry(a, b))) == 64

    def test_no_depende_del_orden_de_covers(self):
        assert links_sha256(entry(evidence(["base", "R1"]))) == links_sha256(
            entry(evidence(["R1", "base"]))
        )

    def test_cambia_al_sustituir_una_evidencia(self):
        assert links_sha256(entry(evidence())) != links_sha256(entry(evidence(evidence_id=OTHER)))

    def test_cambia_al_desvincular(self):
        assert links_sha256(entry(evidence())) != links_sha256(entry())

    def test_cambia_al_cambiar_covers(self):
        assert links_sha256(entry(evidence(["base"]))) != links_sha256(entry(evidence(["R1"])))

    def test_distingue_el_tipo_de_enlace(self):
        check = CheckLink(check_id="x", covers=["base"])
        document = DocumentLink(document="x", covers=["base"])

        assert links_sha256(entry(check)) != links_sha256(entry(document))

    def test_ignora_responsable_periodicidad_y_notas(self):
        plain = entry(evidence())
        decorated = plain.model_copy(update={"review_every_days": 90, "notes": "nota"})

        assert links_sha256(plain) == links_sha256(decorated)


def replace_selected(declaration, measure_id, **update):
    selected = [
        s.model_copy(update=update) if s.id == measure_id else s for s in declaration.selected
    ]
    return declaration.model_copy(update={"selected": selected})


def context(declaration, measure_id="op.acc.5", catalog=None):
    return measure_context_sha256(declaration, measure_id, catalog)


def with_card(measure_id, change):
    catalog = load_catalog()
    cards = [change(c) if c.id == measure_id else c for c in catalog.measures]
    return catalog.model_copy(update={"measures": cards})


def edit_base_text(card):
    base = [card.base[0].model_copy(update={"text": card.base[0].text + " (cambio)"})]
    return card.model_copy(update={"base": [*base, *card.base[1:]]})


def edit_reinforcement_text(reinforcement_id):
    def change(card):
        reinforcements = [
            r.model_copy(update={"clauses": [Clause(id=r.clauses[0].id, text="otro texto")]})
            if r.id == reinforcement_id
            else r
            for r in card.reinforcements
        ]
        return card.model_copy(update={"reinforcements": reinforcements})

    return change


class TestMeasureContext:
    """Cada componente que la huella cubre invalida la revisión; lo ajeno no."""

    def test_es_estable(self, declaration):
        assert context(declaration) == context(declaration)
        assert len(context(declaration)) == 64

    def test_cambia_con_el_tipo_de_entrada(self, declaration):
        moved = declaration.model_copy(
            update={
                "selected": [s for s in declaration.selected if s.id != "op.acc.5"],
                "not_selected": [
                    *declaration.not_selected,
                    NotSelected(id="op.acc.5", justification="Asumido."),
                ],
            }
        )

        assert context(moved) != context(declaration)

    def test_cambia_con_la_justificacion_de_una_no_seleccionada(self, declaration):
        measure_id = declaration.not_selected[0].id
        changed = declaration.model_copy(
            update={
                "not_selected": [
                    n.model_copy(update={"justification": "Otra razón."})
                    if n.id == measure_id
                    else n
                    for n in declaration.not_selected
                ]
            }
        )

        assert context(changed, measure_id) != context(declaration, measure_id)

    @pytest.mark.parametrize("field", ["asset_type", "justification", "required_by"])
    def test_cambia_con_la_ausencia_de_activo(self, declaration, field):
        record = AbsentAssetsRecord(
            id="op.acc.5", required_by="la categoría MEDIA", asset_type="a", justification="j"
        )
        base = declaration.model_copy(
            update={
                "selected": [s for s in declaration.selected if s.id != "op.acc.5"],
                "required_but_absent_assets": [record],
            }
        )
        changed = base.model_copy(
            update={"required_but_absent_assets": [record.model_copy(update={field: "otro"})]}
        )

        assert context(changed) != context(base)

    def test_cambia_con_el_motivo_de_exigencia(self, declaration):
        changed = replace_selected(declaration, "op.acc.5", required_by="la categoría ALTA")

        assert context(changed) != context(declaration)

    def test_cambia_con_los_refuerzos_exigidos(self, declaration):
        changed = replace_selected(declaration, "op.acc.5", required_reinforcements=["R5", "R6"])

        assert context(changed) != context(declaration)

    def test_cambia_con_los_refuerzos_elegidos(self, declaration):
        changed = replace_selected(declaration, "op.acc.5", chosen_reinforcements=["R4"])

        assert context(changed) != context(declaration)

    def test_cambia_con_los_grupos_alternativos(self, declaration):
        changed = replace_selected(declaration, "op.acc.5", alternatives=[["R2", "R3"]])

        assert context(changed) != context(declaration)

    def test_cambia_al_anadir_una_compensacion(self, declaration):
        record = CompensationRecord(
            replaces="op.acc.5", obligations=["base"], measures=["m"], justification="j"
        )
        changed = declaration.model_copy(
            update={"compensations": [*declaration.compensations, record]}
        )

        assert context(changed) != context(declaration)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("obligations", ["R5"]), ("measures", ["otra"]), ("justification", "otra")],
    )
    def test_cambia_con_el_contenido_de_la_compensacion(self, declaration, field, value):
        changed = declaration.model_copy(
            update={
                "compensations": [
                    c.model_copy(update={field: value}) if c.replaces == "op.pl.1" else c
                    for c in declaration.compensations
                ]
            }
        )

        assert context(changed, "op.pl.1") != context(declaration, "op.pl.1")

    def test_cambia_con_el_texto_de_la_base(self, declaration):
        changed = with_card("op.acc.5", edit_base_text)

        assert context(declaration, catalog=changed) != context(declaration)

    def test_cambia_con_el_texto_de_un_refuerzo_en_juego(self, declaration):
        changed = with_card("op.acc.5", edit_reinforcement_text("R5"))

        assert context(declaration, catalog=changed) != context(declaration)

    def test_cambia_con_el_texto_de_un_refuerzo_sustituido_por_compensacion(self, declaration):
        record = CompensationRecord(
            replaces="op.acc.5", obligations=["R3"], measures=["m"], justification="j"
        )
        compensated = declaration.model_copy(update={"compensations": [record]})
        changed = with_card("op.acc.5", edit_reinforcement_text("R3"))

        assert context(compensated, catalog=changed) != context(compensated)

    def test_no_cambia_con_el_texto_de_un_refuerzo_fuera_de_juego(self, declaration):
        changed = with_card("op.acc.5", edit_reinforcement_text("R7"))

        assert context(declaration, catalog=changed) == context(declaration)

    def test_un_cambio_en_otra_medida_no_la_cambia(self, declaration):
        before = context(declaration)
        other = replace_selected(
            declaration, "op.pl.1", required_by="otra", chosen_reinforcements=["R2"]
        )
        other = other.model_copy(
            update={
                "compensations": [],
                "not_selected": [
                    *other.not_selected,
                    NotSelected(id="zz.9", justification="Nueva."),
                ],
            }
        )

        assert context(other) == before
        assert context(other, "op.pl.1") != context(declaration, "op.pl.1")

    def test_un_cambio_de_texto_en_otra_ficha_no_la_cambia(self, declaration):
        changed = with_card("op.cont.2", edit_base_text)

        assert context(declaration, catalog=changed) == context(declaration)

    def test_no_depende_de_la_firma_ni_de_la_huella_del_alcance(self, declaration):
        resigned = declaration.model_copy(
            update={
                "scope_fingerprint": "f" * 64,
                "approved_by": "Otra persona",
                "approved_at": declaration.approved_at.replace(year=2027),
            }
        )

        assert context(resigned) == context(declaration)

    def test_una_medida_que_no_esta_en_la_declaracion_es_un_error(self, declaration):
        with pytest.raises(LookupError):
            context(declaration, "no.existe")
