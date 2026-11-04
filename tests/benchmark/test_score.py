from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from ens_evidence.benchmark.model import (
    Baseline,
    BaselineEntry,
    CaseKind,
    DefectLayer,
    Retired,
    Totals,
)
from ens_evidence.benchmark.score import (
    CaseRun,
    ExpectedDefect,
    Observed,
    RepetitionRun,
    baseline_gate,
    entry_of,
    inventory_problems,
    match,
    rate,
    score,
    threshold_problems,
)
from ens_evidence.models.preaudit import (
    Finding,
    Layer,
    Provider,
    Ref,
    RefKind,
    finding_id,
)

SHA = "a" * 64
EV = Ref(kind=RefKind.EVIDENCE, id="ev-1")
EV2 = Ref(kind=RefKind.EVIDENCE, id="ev-2")
DOC = Ref(kind=RefKind.DOCUMENT, id="politica-seguridad")
SECTION = Ref(kind=RefKind.SECTION, id="resultados:missing:op.exp.1")


def judged(measure: str = "op.exp.1", criterion: str = "coherencia", *refs: Ref) -> Observed:
    return Observed(Layer.JUDGMENT, measure, criterion, frozenset((r.kind, r.id) for r in refs))


def determined(measure: str, rule: str, *refs: Ref) -> Observed:
    return Observed(Layer.DETERMINISTIC, measure, rule, frozenset((r.kind, r.id) for r in refs))


def j_defect(key: str, target: Ref | None = DOC, criterion: str = "coherencia") -> ExpectedDefect:
    return ExpectedDefect(f"dev/c#{key}", DefectLayer.JUDGMENT, "op.exp.1", criterion, target)


def d_defect(key: str, rule: str, target: Ref | None = None) -> ExpectedDefect:
    return ExpectedDefect(f"dev/c#{key}", DefectLayer.DETERMINISTIC, "op.exp.1", rule, target)


def case(
    name: str,
    reps: Sequence[Sequence[Observed]],
    defects: Sequence[ExpectedDefect] = (),
    kind: CaseKind = CaseKind.DEFECTIVE,
    judgment: bool = True,
) -> CaseRun:
    return CaseRun(
        case=f"dev/{name}",
        kind=kind,
        case_sha256=SHA,
        repetitions=[RepetitionRun(n=i, findings=f) for i, f in enumerate(reps, 1)],
        defects=defects,
        judgment=judgment,
    )


# --- casado ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("defect", "found", "matches"),
    [
        (j_defect("x"), judged("op.exp.1", "coherencia", DOC), True),
        (j_defect("x"), judged("op.exp.1", "coherencia", DOC, EV), True),
        (j_defect("x"), judged("op.exp.2", "coherencia", DOC), False),
        (j_defect("x"), judged("op.exp.1", "vigencia", DOC), False),
        (j_defect("x"), judged("op.exp.1", "coherencia", EV), False),
        (j_defect("x", target=None), judged("op.exp.1", "coherencia", EV), True),
        (
            d_defect("x", "EVIDENCE_EXPIRED", EV),
            determined("op.exp.1", "EVIDENCE_EXPIRED", EV),
            True,
        ),
        (
            d_defect("x", "EVIDENCE_EXPIRED", EV),
            determined("op.exp.1", "EVIDENCE_EXPIRED", EV2),
            False,
        ),
        (d_defect("x", "EVIDENCE_EXPIRED", EV), determined("op.exp.1", "CHECK_FAIL", EV), False),
        (
            d_defect("x", "EVIDENCE_MISSING"),
            determined("op.exp.1", "EVIDENCE_MISSING", SECTION),
            True,
        ),
        (d_defect("x", "EVIDENCE_MISSING"), determined("op.exp.1", "EVIDENCE_MISSING"), False),
        (
            d_defect("x", "EVIDENCE_MISSING"),
            determined("op.exp.2", "EVIDENCE_MISSING", SECTION),
            False,
        ),
        (
            j_defect("x", criterion="EVIDENCE_MISSING"),
            determined("op.exp.1", "EVIDENCE_MISSING", DOC),
            False,
        ),
    ],
)
def test_casado_por_medida_ref_y_criterio(
    defect: ExpectedDefect, found: Observed, matches: bool
) -> None:
    assert defect.matches(found) is matches


def test_observed_de_un_hallazgo_real() -> None:
    refs = [DOC]
    finding = Finding(
        id=finding_id("coherencia", "op.exp.1", refs),
        layer=Layer.JUDGMENT,
        proposal="NC_MENOR",
        measure="op.exp.1",
        refs=refs,
        criterion="coherencia",
        reason="r",
        quote="q",
        source="s",
        classification_source="c",
    )
    assert Observed.of(finding) == judged("op.exp.1", "coherencia", DOC)


def test_duplicados_cuentan_una_vez() -> None:
    found = judged("op.exp.1", "coherencia", DOC)
    twin = judged("op.exp.1", "coherencia", DOC, EV)
    m = match([j_defect("x")], [found, found, twin])
    assert m.detected == {"dev/c#x"}
    assert m.unmatched[Layer.JUDGMENT] == 0
    scored = score([case("c", [[found, found, twin]] * 3, [j_defect("x")])])
    assert [r.judgment_emitted for r in scored.cases[0].repetitions] == [1, 1, 1]
    assert scored.totals.false_alarms.value == 0


def test_un_hallazgo_casa_con_un_solo_defecto() -> None:
    both = judged("op.exp.1", "coherencia", EV, EV2)
    defects = [j_defect("a", EV), j_defect("b", EV2)]
    assert len(match(defects, [both]).detected) == 1
    other = judged("op.exp.1", "coherencia", EV2)
    assert match(defects, [both, other]).detected == {"dev/c#a", "dev/c#b"}


def test_casado_maximo_no_depende_del_orden() -> None:
    both = judged("op.exp.1", "coherencia", EV, EV2)
    only_a = judged("op.exp.1", "coherencia", EV)
    defects = [j_defect("a", EV), j_defect("b", EV2)]
    assert match(defects, [only_a, both]).detected == {"dev/c#a", "dev/c#b"}
    assert match(list(reversed(defects)), [both, only_a]).detected == {"dev/c#a", "dev/c#b"}


# --- repeticiones ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hits", "detected"),
    [
        ((1, 0, 0), False),
        ((1, 1, 0), True),
        ((0, 1, 1), True),
        ((1, 1, 1), True),
        ((0, 0, 0), False),
    ],
)
def test_deteccion_por_mayoria_de_tres(hits: tuple[int, ...], detected: bool) -> None:
    found = judged("op.exp.1", "coherencia", DOC)
    reps = [[found] if hit else [] for hit in hits]
    result = score([case("c", reps, [j_defect("x")])]).cases[0].defects[0]
    assert result.detected_by_repetition == [bool(h) for h in hits]
    assert result.detected is detected


def test_cero_emitidos_da_tasa_cero_e_informada_aparte() -> None:
    scored = score([case("c", [[], [], []], [j_defect("x")])])
    assert scored.totals.false_alarms == rate(0, 0)
    assert scored.totals.zero_emitted_repetitions == [1, 2, 3]
    assert scored.totals.judgment == rate(0, 1)


def test_sesgo_tasa_por_repeticion_y_mediana() -> None:
    noise = [
        judged("op.exp.1", "coherencia", Ref(kind=RefKind.EVIDENCE, id=f"x{i}")) for i in range(10)
    ]
    hits = [judged(f"op.exp.{i}", "coherencia", DOC) for i in range(2, 12)]
    defects = [
        ExpectedDefect(f"dev/c#d{i}", DefectLayer.JUDGMENT, f"op.exp.{i}", "coherencia", DOC)
        for i in range(2, 12)
    ]
    scored = score([case("c", [[], hits, noise], defects)])
    t = scored.totals
    assert [r.judgment_emitted for r in scored.cases[0].repetitions] == [0, 10, 10]
    assert [r.judgment_false_alarms for r in scored.cases[0].repetitions] == [0, 0, 10]
    assert [r.value for r in t.false_alarms_by_repetition] == [0, 0, 1]
    assert t.false_alarms == rate(0, 10)
    assert t.zero_emitted_repetitions == [1]


def test_tasa_a_suma_todos_los_expedientes_por_repeticion() -> None:
    hit = judged("op.exp.1", "coherencia", DOC)
    noise = judged("op.exp.9", "coherencia", DOC)
    runs = [
        case("defectuoso", [[hit]] * 3, [j_defect("x")]),
        case("correcto", [[noise], [], [noise]], kind=CaseKind.CORRECT),
    ]
    t = score(runs).totals
    assert [(r.numerator, r.denominator) for r in t.false_alarms_by_repetition] == [
        (1, 2),
        (0, 1),
        (1, 2),
    ]
    assert t.false_alarms == rate(1, 2)


def test_tasa_b_correctos_con_alguna_falsa_alarma() -> None:
    noise = judged("op.exp.9", "coherencia", DOC)
    runs = [
        case("limpio", [[]] * 3, kind=CaseKind.CORRECT),
        case("ruidoso", [[noise]] * 3, kind=CaseKind.CORRECT),
        case("a-veces", [[noise], [], []], kind=CaseKind.CORRECT),
        case("defectuoso", [[noise]] * 3, [j_defect("x")]),
    ]
    t = score(runs).totals
    assert [(r.numerator, r.denominator) for r in t.correct_with_false_alarms_by_repetition] == [
        (2, 3),
        (1, 3),
        (1, 3),
    ]
    assert t.correct_with_false_alarms == rate(1, 3)


def test_expediente_correcto_con_hallazgo_determinista_es_fallo() -> None:
    stray = determined("op.exp.1", "CHECK_FAIL", SECTION)
    t = score([case("correcto", [[stray]] * 3, kind=CaseKind.CORRECT)]).totals
    assert t.deterministic_unmatched == 3
    assert "3 hallazgos deterministas sin casar" in threshold_problems(t)


def test_caso_solo_determinista_no_entra_en_las_tasas() -> None:
    hit = determined("op.exp.1", "HASH_MISMATCH", EV, SECTION)
    runs = [case("hash", [[hit]], [d_defect("h", "HASH_MISMATCH", EV)], judgment=False)]
    t = score(runs).totals
    assert t.deterministic == rate(1, 1)
    assert t.false_alarms_by_repetition == []


def test_repeticiones_desiguales_en_juicio_se_rechazan() -> None:
    with pytest.raises(ValueError, match="repeticiones"):
        score([case("a", [[]] * 3), case("b", [[]] * 2)])


# --- puerta threshold ------------------------------------------------------------------------


def totals(
    det: tuple[int, int] = (10, 10),
    jud: tuple[int, int] = (16, 20),
    a: tuple[int, int] = (2, 10),
    b: tuple[int, int] = (0, 3),
    unmatched: int = 0,
) -> Totals:
    return Totals(
        deterministic=rate(*det),
        judgment=rate(*jud),
        false_alarms=rate(*a),
        correct_with_false_alarms=rate(*b),
        deterministic_unmatched=unmatched,
    )


@pytest.mark.parametrize(
    ("t", "problem"),
    [
        (totals(), None),
        (totals(det=(9, 10)), "deterministas detectados 9/10"),
        (totals(unmatched=1), "sin casar"),
        (totals(det=(0, 0)), "no hay defectos deterministas"),
        (totals(jud=(15, 20)), "detección de juicio 75.0 %"),
        (totals(jud=(0, 0)), "no hay defectos de juicio"),
        (totals(a=(3, 10)), "(A) 30.0 %"),
        (totals(b=(1, 3)), "(B) 33.3 %"),
        (totals(a=(0, 0)), None),
    ],
)
def test_puerta_threshold(t: Totals, problem: str | None) -> None:
    problems = threshold_problems(t)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


# --- puerta baseline -------------------------------------------------------------------------

AT = datetime(2026, 11, 1, tzinfo=UTC)
INVENTORY = {"dev/c": SHA, "dev/c#x": SHA, "dev/c#y": SHA}


def entry(detected: list[str], t: Totals | None = None) -> BaselineEntry:
    return BaselineEntry(
        provider=Provider.CLAUDE_CODE,
        model="claude-opus-5-5",
        detected=detected,
        totals=t or totals(det=(0, 0), jud=(len(detected), 2), a=(0, 2), b=(0, 1)),
    )


def baseline(entries: list[BaselineEntry], inventory: dict[str, str] = INVENTORY) -> Baseline:
    return Baseline(
        format="ens-benchmark-baseline/1",
        written_at=AT,
        pack_sha256=SHA,
        inventory=inventory,
        entries=entries,
    )


def problems_of(
    current: list[BaselineEntry],
    base: Baseline,
    inventory: dict[str, str] = INVENTORY,
    retired: Sequence[Retired] = (),
) -> list[str]:
    verdict = baseline_gate(current, base, inventory, retired)
    return verdict.general + [p for ps in verdict.by_pair.values() for p in ps]


def test_baseline_igual_pasa() -> None:
    base = baseline([entry(["dev/c#x", "dev/c#y"])])
    verdict = baseline_gate([entry(["dev/c#x", "dev/c#y"])], base, INVENTORY, [])
    assert verdict.passed


def test_regresion_de_un_solo_defecto() -> None:
    base = baseline([entry(["dev/c#x", "dev/c#y"])])
    problems = problems_of([entry(["dev/c#x"])], base)
    assert "dev/c#y se detectaba y ya no" in problems
    assert any("detección agregada baja" in p for p in problems)


def test_cambiar_un_defecto_detectado_por_otro_sigue_fallando() -> None:
    base = baseline([entry(["dev/c#x"])])
    problems = problems_of([entry(["dev/c#y"])], base)
    assert problems == ["dev/c#x se detectaba y ya no"]


def test_baja_agregada_por_un_defecto_nuevo_no_detectado() -> None:
    base = baseline([entry(["dev/c#x"], totals(det=(0, 0), jud=(1, 1), a=(0, 1), b=(0, 1)))])
    current = entry(["dev/c#x"], totals(det=(0, 0), jud=(1, 2), a=(0, 1), b=(0, 1)))
    inventory = {**INVENTORY, "dev/c#z": SHA}
    problems = problems_of([current], base, inventory=inventory)
    assert problems == ["la detección agregada baja de 100.0 % a 50.0 %"]


@pytest.mark.parametrize(
    ("current", "problem"),
    [
        (totals(det=(0, 0), jud=(2, 2), a=(1, 2), b=(0, 1)), "tasa A de falsas alarmas sube"),
        (totals(det=(0, 0), jud=(2, 2), a=(0, 2), b=(1, 1)), "tasa B de falsas alarmas sube"),
    ],
)
def test_subida_de_a_o_b_es_regresion(current: Totals, problem: str) -> None:
    base = baseline([entry(["dev/c#x", "dev/c#y"])])
    problems = problems_of([entry(["dev/c#x", "dev/c#y"], current)], base)
    assert any(problem in p for p in problems), problems


def test_falta_un_proveedor_de_la_linea_base() -> None:
    codex = BaselineEntry(
        provider=Provider.CODEX, model="gpt-6.1-sol", detected=[], totals=totals(det=(0, 0))
    )
    base = baseline([entry(["dev/c#x", "dev/c#y"]), codex])
    problems = problems_of([entry(["dev/c#x", "dev/c#y"])], base)
    assert problems == ["falta codex/gpt-6.1-sol, que la línea base tenía"]


def test_determinista_por_debajo_del_cien_falla_incluso_sin_linea_base() -> None:
    current = entry([], totals(det=(5, 6), jud=(0, 0)))
    assert any("5/6" in p for p in problems_of([current], baseline([])))


def test_linea_base_vacia_solo_puntua_lo_determinista() -> None:
    assert problems_of([entry([], totals(det=(6, 6), jud=(0, 10)))], baseline([])) == []


@pytest.mark.parametrize(
    ("current", "retired", "expected"),
    [
        ({"dev/c": SHA, "dev/c#x": SHA}, [], ["dev/c#y sale del inventario"]),
        ({"dev/c": SHA, "dev/c#x": SHA}, [Retired(id="dev/c#y", reason="r")], []),
        ({**INVENTORY, "dev/c#y": "b" * 64}, [], ["dev/c#y cambia de huella"]),
        ({**INVENTORY, "dev/c#y": "b" * 64}, [Retired(id="dev/c#y", reason="r")], []),
        ({}, [Retired(id="dev/c", reason="caso retirado")], []),
        ({}, [Retired(id="dev/c#x", reason="r")], ["dev/c sale", "dev/c#y sale"]),
        ({**INVENTORY, "dev/nuevo": SHA}, [], []),
    ],
)
def test_inventario_retirado(
    current: dict[str, str], retired: list[Retired], expected: list[str]
) -> None:
    problems = inventory_problems(current, INVENTORY, retired)
    assert len(problems) == len(expected)
    for want, got in zip(expected, problems, strict=True):
        assert want in got


def test_defecto_retirado_no_cuenta_como_regresion_ni_baja_agregada() -> None:
    base = baseline([entry(["dev/c#x", "dev/c#y"])])
    retired = [Retired(id="dev/c#y", reason="cita corregida")]
    inventory = {"dev/c": SHA, "dev/c#x": SHA}
    current = entry(["dev/c#x"], totals(det=(0, 0), jud=(1, 1), a=(0, 2), b=(0, 1)))
    assert problems_of([current], base, inventory=inventory, retired=retired) == []


def test_entry_of_lista_los_detectados() -> None:
    hit = judged("op.exp.1", "coherencia", DOC)
    scored = score([case("c", [[hit]] * 3, [j_defect("x"), j_defect("y", EV)])])
    result = entry_of(Provider.CODEX, "gpt-6.1-sol", scored)
    assert result.detected == ["dev/c#x"]
    assert result.totals.judgment == rate(1, 2)
