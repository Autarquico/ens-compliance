from datetime import timedelta

import pytest
from applicability.helpers import ABSENT_REASON, LATER, absent, decide

from ens_evidence.applicability import DeclarationState, approve_declaration
from ens_evidence.catalog import load_catalog
from ens_evidence.matrix import (
    ClauseRef,
    MatrixRowSpec,
    Obligation,
    RowKind,
    expand_covers,
    plan_obligations,
    rows_from_declaration,
)
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import Compensation
from ens_evidence.orgdocs.clauses import measure_clause_ids
from ens_evidence.scope import revoke_approval

from .helpers import SIGNED_AT


def sign(workspace):
    return approve_declaration(workspace, "Luis Pardo", SIGNED_AT)


def rows_by_id(declaration):
    return {r.measure_id: r for r in rows_from_declaration(declaration)}


def obligation_ids(row):
    return [o.id for o in row.obligations]


def test_las_filas_son_exactamente_la_declaracion(ready):
    decide(
        ready,
        "op.acc.5",
        Applicability.NOT_APPLICABLE,
        ABSENT_REASON,
        absent_assets=absent("usuarios externos"),
    )
    declaration = sign(ready)

    rows = rows_from_declaration(declaration)

    ids = [r.measure_id for r in rows]
    assert len(ids) == len(set(ids))
    assert set(ids) == {
        *(s.id for s in declaration.selected),
        *(n.id for n in declaration.not_selected),
        *(a.id for a in declaration.required_but_absent_assets),
    }
    assert len(ids) == len(load_catalog().measures)
    kinds = {r.measure_id: r.kind for r in rows}
    assert kinds["op.acc.5"] is RowKind.ABSENT_ASSETS
    assert kinds["op.pl.1"] is RowKind.SELECTED
    assert RowKind.NOT_SELECTED in kinds.values()


def test_las_medidas_no_aplicables_de_los_dos_tipos_no_tienen_obligaciones(ready):
    decide(
        ready,
        "op.acc.5",
        Applicability.NOT_APPLICABLE,
        ABSENT_REASON,
        absent_assets=absent("usuarios externos"),
    )
    rows = rows_by_id(sign(ready))
    not_selected = next(r for r in rows.values() if r.kind is RowKind.NOT_SELECTED)

    assert rows["op.acc.5"].obligations == ()
    assert not_selected.obligations == ()


def test_una_medida_seleccionada_tiene_base_refuerzo_exigido_y_clausulas_del_catalogo(declaration):
    row = rows_by_id(declaration)["op.cont.2"]
    card = load_catalog().get("op.cont.2")

    assert row.kind is RowKind.SELECTED
    assert obligation_ids(row) == ["base"]
    base = row.obligation("base")
    assert [(c.id, c.label, c.text) for c in base.clauses] == [
        (c.id, c.label or c.id, c.text) for c in card.base
    ]


def test_el_refuerzo_alternativo_elegido_es_una_obligacion(ready):
    default = rows_by_id(sign(ready))["op.acc.5"]
    decide(ready, "op.acc.5", Applicability.APPLICABLE, chosen_reinforcements=["R4"])
    chosen = rows_by_id(sign(ready))["op.acc.5"]

    assert obligation_ids(default) == ["base", "R2", "R5"]
    assert obligation_ids(chosen) == ["base", "R4", "R5"]
    assert chosen.obligation("R4").clause_ids == ("op.acc.5.r4.1", "op.acc.5.r4.2", "op.acc.5.r4.3")


def test_la_compensacion_sustituye_la_base_por_una_obligacion_compensation(declaration):
    row = rows_by_id(declaration)["op.pl.1"]
    card = load_catalog().get("op.pl.1")

    assert obligation_ids(row) == ["R1", "compensation"]
    compensation = row.obligation("compensation")
    assert compensation.replaces == ("base",)
    assert compensation.compensating_measures == (
        "Segregación de red adicional con monitorización reforzada",
    )
    assert [c.text for c in compensation.clauses] == [c.text for c in card.base]
    assert row.obligation("base") is None


def test_la_compensacion_de_un_refuerzo_alternativo_sustituye_la_eleccion_del_grupo(ready):
    decide(
        ready,
        "op.acc.5",
        Applicability.APPLICABLE,
        compensation=Compensation(
            replaces=["R3"], measures=["Control equivalente"], justification="Equivalente."
        ),
        chosen_reinforcements=[],
    )
    row = rows_by_id(sign(ready))["op.acc.5"]

    assert obligation_ids(row) == ["base", "R5", "compensation"]
    assert row.obligation("compensation").replaces == ("R3",)
    assert row.obligation("compensation").clause_ids == (
        "op.acc.5.r3.1",
        "op.acc.5.r3.2",
        "op.acc.5.r3.3",
    )


def test_sin_firmar_no_hay_filas_y_se_devuelve_el_estado(ready):
    plan = plan_obligations(ready, SIGNED_AT)

    assert plan.state is DeclarationState.NOT_APPROVED
    assert not plan.current
    assert plan.rows == ()


def test_con_la_declaracion_vigente_el_plan_trae_las_filas(declaration, ready):
    plan = plan_obligations(ready, SIGNED_AT)

    assert plan.current
    assert plan.rows == rows_from_declaration(declaration)
    assert plan.row("op.pl.1") is not None
    assert plan.row("no.existe") is None


def test_una_declaracion_obsoleta_devuelve_su_estado(declaration, ready):
    revoke_approval(ready, "cambio de alcance", LATER + timedelta(hours=2))

    plan = plan_obligations(ready, SIGNED_AT)

    assert plan.state is DeclarationState.STALE
    assert not plan.current
    assert plan.errors


class TestExpandCovers:
    @pytest.fixture
    def rows(self, declaration):
        return rows_by_id(declaration)

    def test_base_se_expande_a_sus_clausulas_con_id(self, rows):
        row = rows["op.cont.2"]

        expansion = expand_covers(["base"], row)

        assert expansion.errors == ()
        assert expansion.units == set(row.obligation("base").clause_ids)
        assert None not in expansion.units

    def test_un_refuerzo_y_una_clausula_suelta_se_suman(self, rows):
        row = rows["op.pl.1"]

        expansion = expand_covers(["R1", "op.pl.1.r2.1"], row)

        assert expansion.units == set(row.obligation("R1").clause_ids)
        assert expansion.errors  # op.pl.1.r2.1 es de R2, que no está en juego

    def test_compensation_se_expande_a_las_clausulas_sustituidas(self, rows):
        row = rows["op.pl.1"]

        expansion = expand_covers(["compensation"], row)

        assert expansion.errors == ()
        assert expansion.units == set(row.obligation("compensation").clause_ids)

    def test_la_base_sustituida_por_compensacion_se_rechaza_como_atajo(self, rows):
        expansion = expand_covers(["base"], rows["op.pl.1"])

        assert any("«base»" in e for e in expansion.errors)
        assert expansion.units == frozenset()

    @pytest.mark.parametrize("token", ["op.acc.5.1", "R2", "compensation", "op.cont.2.r2.1"])
    def test_se_rechaza_lo_que_no_es_de_la_medida(self, rows, token):
        expansion = expand_covers([token], rows["op.cont.2"])

        assert len(expansion.errors) == 1
        assert token in expansion.errors[0]
        assert "op.cont.2" in expansion.errors[0]

    def test_una_clausula_de_la_medida_se_acepta_tal_cual(self, rows):
        expansion = expand_covers(["op.cont.2.3"], rows["op.cont.2"])

        assert expansion.units == {"op.cont.2.3"}
        assert expansion.errors == ()

    def test_la_introduccion_se_acepta_y_no_es_una_unidad_a_cubrir(self, rows):
        row = rows["op.cont.2"]

        expansion = expand_covers(["op.cont.2.intro", "op.cont.2.3"], row)

        assert expansion.errors == ()
        assert expansion.units == {"op.cont.2.3"}

    def test_la_introduccion_de_una_medida_que_no_la_tiene_se_rechaza(self):
        row = MatrixRowSpec(
            "x.1",
            RowKind.SELECTED,
            (Obligation("base", (ClauseRef("x.1.1", None, "texto"),)),),
            "a" * 64,
        )

        assert expand_covers(["x.1.intro"], row).errors

    def test_las_clausulas_que_listan_los_documentos_se_enlazan_tal_cual(self, rows):
        for measure_id, row in rows.items():
            if row.kind is not RowKind.SELECTED:
                continue
            covered = measure_clause_ids(measure_id)
            assert expand_covers(covered, row).errors == (), measure_id

    def test_una_obligacion_sin_ids_de_clausula_es_una_sola_unidad(self):
        row = MatrixRowSpec(
            "x.1",
            RowKind.SELECTED,
            (Obligation("base", (ClauseRef(None, None, "texto"),)),),
            "a" * 64,
        )

        assert expand_covers(["base"], row).units == {"base"}

    def test_una_medida_no_aplicable_no_admite_cobertura(self, rows):
        row = next(r for r in rows.values() if r.kind is RowKind.NOT_SELECTED)

        assert expand_covers(["base"], row).errors
