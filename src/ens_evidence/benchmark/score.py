"""Puntuación del banco: funciones puras sobre hallazgos ya extraídos, sin I/O.

Casado (plan, «Puntuación» y crítica 2, 5, 6, 7, 12):
- juicio: misma medida, mismo `expected_criterion` y la ref del objetivo entre las del hallazgo;
- determinista: misma medida, `criterion == expected_rule` y la ref de evidencia del objetivo;
  una ausencia (sin objetivo) casa por medida, regla y una ref de sección.
Cada hallazgo casa con un solo defecto; varios hallazgos sobre un defecto cuentan una vez.
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from ens_evidence.benchmark.model import (
    Baseline,
    BaselineEntry,
    Case,
    CaseKind,
    CaseResult,
    DefectLayer,
    DefectResult,
    Rate,
    RepetitionOutcome,
    RepetitionResult,
    Retired,
    Totals,
)
from ens_evidence.models.preaudit import Finding, Layer, Provider, Ref, RefKind
from ens_evidence.preaudit.payload import canonical_json

DETERMINISTIC_FLOOR = Fraction(1)
JUDGMENT_FLOOR = Fraction(4, 5)
FALSE_ALARM_CEILING = Fraction(1, 5)


@dataclass(frozen=True)
class Sample:
    """Tamaño mínimo de la reservada para que la puerta threshold mida algo."""

    judgment: int
    deterministic: int
    correct: int


HOLDOUT_MINIMUM = Sample(judgment=20, deterministic=10, correct=3)
"""D7: la reservada de BÁSICA, con defectos de juicio, deterministas y expedientes correctos."""

NONEMPTY = Sample(judgment=1, deterministic=1, correct=1)
"""La puerta target de desarrollo: sin los mínimos de D7, pero ningún denominador vacío."""

RefKey = tuple[RefKind, str]
_LAYER = {DefectLayer.DETERMINISTIC: Layer.DETERMINISTIC, DefectLayer.JUDGMENT: Layer.JUDGMENT}


@dataclass(frozen=True)
class Observed:
    """Un hallazgo emitido, reducido a lo que se casa. Dos iguales son duplicados."""

    layer: Layer
    measure: str
    criterion: str
    refs: frozenset[RefKey] = frozenset()

    @classmethod
    def of(cls, finding: Finding) -> "Observed":
        refs = frozenset((ref.kind, ref.id) for ref in finding.refs)
        return cls(finding.layer, finding.measure, finding.criterion, refs)

    def sort_key(self) -> tuple[str, str, str, list[RefKey]]:
        return (self.layer, self.measure, self.criterion, sorted(self.refs))


@dataclass(frozen=True)
class ExpectedDefect:
    """Un defecto sembrado con su objetivo ya resuelto al id real (`None`: ausencia)."""

    key: str
    layer: DefectLayer
    measure: str
    expected: str
    target: Ref | None = None

    def matches(self, found: Observed) -> bool:
        if (found.layer, found.measure, found.criterion) != (
            _LAYER[self.layer],
            self.measure,
            self.expected,
        ):
            return False
        if self.target is not None:
            return (self.target.kind, self.target.id) in found.refs
        if self.layer is DefectLayer.DETERMINISTIC:
            return any(kind is RefKind.SECTION for kind, _ in found.refs)
        return True


@dataclass(frozen=True)
class RepetitionRun:
    n: int
    findings: Sequence[Observed]
    outcome: RepetitionOutcome | None = None


@dataclass(frozen=True)
class CaseRun:
    case: str
    kind: CaseKind
    case_sha256: str
    repetitions: Sequence[RepetitionRun]
    defects: Sequence[ExpectedDefect] = ()
    judgment: bool = True
    payload_sha256: str | None = None


@dataclass(frozen=True)
class Score:
    cases: list[CaseResult]
    totals: Totals


@dataclass(frozen=True)
class _Matched:
    detected: frozenset[str]
    unmatched: dict[Layer, int]


@dataclass
class BaselineVerdict:
    by_pair: dict[str, list[str]] = field(default_factory=dict)
    general: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.general and not any(self.by_pair.values())


# --- casado ----------------------------------------------------------------------------------


def match(defects: Sequence[ExpectedDefect], findings: Iterable[Observed]) -> _Matched:
    """Emparejamiento máximo defecto ↔ hallazgo (Kuhn, orden fijo). Un hallazgo que casa con
    algún defecto nunca es falsa alarma: si su defecto ya lo cubre otro, es un duplicado."""
    unique = sorted(set(findings), key=Observed.sort_key)
    candidates = {
        d.key: [i for i, f in enumerate(unique) if d.matches(f)]
        for d in sorted(defects, key=lambda d: d.key)
    }
    owner: dict[int, str] = {}

    def assign(key: str, seen: set[int]) -> bool:
        for i in candidates[key]:
            if i not in seen:
                seen.add(i)
                if i not in owner or assign(owner[i], seen):
                    owner[i] = key
                    return True
        return False

    detected = frozenset(key for key in candidates if assign(key, set()))
    matchable = {i for ids in candidates.values() for i in ids}
    unmatched = {layer: 0 for layer in Layer}
    for i, found in enumerate(unique):
        if i not in matchable:
            unmatched[found.layer] += 1
    return _Matched(detected, unmatched)


def _majority(hits: Sequence[bool]) -> bool:
    return 2 * sum(hits) > len(hits)


# --- por caso --------------------------------------------------------------------------------


def score_case(run: CaseRun) -> CaseResult:
    matched = [match(run.defects, rep.findings) for rep in run.repetitions]
    repetitions = [
        RepetitionResult(
            n=rep.n,
            outcome=rep.outcome,
            judgment_emitted=_judgment_detected(run, m) + m.unmatched[Layer.JUDGMENT],
            judgment_false_alarms=m.unmatched[Layer.JUDGMENT],
            deterministic_unmatched=m.unmatched[Layer.DETERMINISTIC],
        )
        for rep, m in zip(run.repetitions, matched, strict=True)
    ]
    defects = []
    for defect in sorted(run.defects, key=lambda d: d.key):
        hits = [defect.key in m.detected for m in matched]
        defects.append(
            DefectResult(
                defect=defect.key,
                layer=defect.layer,
                detected_by_repetition=hits,
                detected=_majority(hits),
            )
        )
    return CaseResult(
        case=run.case,
        kind=run.kind,
        judgment=run.judgment,
        case_sha256=run.case_sha256,
        payload_sha256=run.payload_sha256,
        repetitions=repetitions,
        defects=defects,
    )


def _judgment_detected(run: CaseRun, matched: _Matched) -> int:
    judged = {d.key for d in run.defects if d.layer is DefectLayer.JUDGMENT}
    return len(judged & matched.detected)


# --- agregados -------------------------------------------------------------------------------


def rate(numerator: int, denominator: int) -> Rate:
    value = float(Fraction(numerator, denominator)) if denominator else 0.0
    return Rate(numerator=numerator, denominator=denominator, value=value)


def _fraction(r: Rate) -> Fraction:
    return Fraction(r.numerator, r.denominator) if r.denominator else Fraction(0)


def _median(rates: Sequence[Rate]) -> Rate:
    """Mediana baja: siempre una repetición real, así numerador y denominador cuadran."""
    if not rates:
        return rate(0, 0)
    ranked = sorted(enumerate(rates), key=lambda pair: (_fraction(pair[1]), pair[0]))
    return ranked[(len(ranked) - 1) // 2][1]


def _detection(cases: Sequence[CaseResult], layer: DefectLayer) -> Rate:
    results = [d for c in cases for d in c.defects if d.layer is layer]
    return rate(sum(d.detected for d in results), len(results))


def _by_repetition(cases: Sequence[CaseResult]) -> list[list[RepetitionResult]]:
    judged = [c for c in cases if c.judgment]
    lengths = {len(c.repetitions) for c in judged}
    if len(lengths) > 1:
        raise ValueError("los casos de juicio no tienen el mismo número de repeticiones")
    count = lengths.pop() if lengths else 0
    return [[c.repetitions[i] for c in judged] for i in range(count)]


def totals(cases: Sequence[CaseResult]) -> Totals:
    rounds = _by_repetition(cases)
    correct = [c.kind is CaseKind.CORRECT for c in cases if c.judgment]
    a = [
        rate(sum(r.judgment_false_alarms for r in reps), sum(r.judgment_emitted for r in reps))
        for reps in rounds
    ]
    b = [
        rate(
            sum(r.judgment_false_alarms > 0 for r, ok in zip(reps, correct, strict=True) if ok),
            sum(correct),
        )
        for reps in rounds
    ]
    return Totals(
        deterministic=_detection(cases, DefectLayer.DETERMINISTIC),
        judgment=_detection(cases, DefectLayer.JUDGMENT),
        false_alarms=_median(a),
        correct_with_false_alarms=_median(b),
        false_alarms_by_repetition=a,
        correct_with_false_alarms_by_repetition=b,
        zero_emitted_repetitions=[
            reps[0].n for reps in rounds if not sum(r.judgment_emitted for r in reps)
        ],
        deterministic_unmatched=sum(
            r.deterministic_unmatched for c in cases for r in c.repetitions
        ),
    )


def score(runs: Sequence[CaseRun]) -> Score:
    """Puntuación de un proveedor y modelo sobre todos los casos de una partición."""
    cases = [score_case(run) for run in sorted(runs, key=lambda r: r.case)]
    return Score(cases=cases, totals=totals(cases))


def entry_of(provider: Provider, model: str, scored: Score) -> BaselineEntry:
    detected = sorted(d.defect for c in scored.cases for d in c.defects if d.detected)
    return BaselineEntry(
        provider=provider,
        model=model,
        detected=detected,
        totals=scored.totals,
        cases=scored.cases,
    )


# --- puertas ---------------------------------------------------------------------------------


def _percent(value: Fraction) -> str:
    return f"{float(value) * 100:.1f} %"


def _deterministic_problems(t: Totals) -> list[str]:
    problems = []
    if t.deterministic.numerator < t.deterministic.denominator:
        problems.append(
            f"deterministas detectados {t.deterministic.numerator}/{t.deterministic.denominator}"
            " (se exige el 100 %)"
        )
    if t.deterministic_unmatched:
        problems.append(f"{t.deterministic_unmatched} hallazgos deterministas sin casar")
    return problems


def sample_of(cases: Sequence[Case]) -> Sample:
    """Los denominadores de D7 que declaran estos casos, antes de medir nada."""
    layers = [d.layer for c in cases for d in c.defects]
    return Sample(
        judgment=layers.count(DefectLayer.JUDGMENT),
        deterministic=layers.count(DefectLayer.DETERMINISTIC),
        correct=sum(c.kind is CaseKind.CORRECT and c.judgment for c in cases),
    )


def sample_problems(sample: Sample, minimum: Sample) -> list[str]:
    problems = []
    for name, size, required in (
        ("defectos deterministas", sample.deterministic, minimum.deterministic),
        ("defectos de juicio", sample.judgment, minimum.judgment),
        ("expedientes correctos", sample.correct, minimum.correct),
    ):
        if not size:
            problems.append(f"no hay {name}")
        elif size < required:
            problems.append(f"{name}: {size} (la muestra exige al menos {required})")
    return problems


def _sample_problems(t: Totals, minimum: Sample) -> list[str]:
    observed = Sample(
        judgment=t.judgment.denominator,
        deterministic=t.deterministic.denominator,
        correct=t.correct_with_false_alarms.denominator,
    )
    return sample_problems(observed, minimum)


def threshold_problems(t: Totals, minimum: Sample | None = None) -> list[str]:
    """Umbral de v0.1 por proveedor y modelo. Vacía si se cumple. Sin `minimum`, la muestra de
    `HOLDOUT_MINIMUM` (puerta threshold); la puerta target pasa `NONEMPTY`."""
    problems = _deterministic_problems(t) + _sample_problems(t, minimum or HOLDOUT_MINIMUM)
    if t.judgment.denominator and _fraction(t.judgment) < JUDGMENT_FLOOR:
        problems.append(f"detección de juicio {_percent(_fraction(t.judgment))} (mínimo 80 %)")
    for name, r in (
        ("falsas alarmas sobre emitidos (A)", t.false_alarms),
        ("expedientes correctos con falsas alarmas (B)", t.correct_with_false_alarms),
    ):
        if _fraction(r) > FALSE_ALARM_CEILING:
            problems.append(f"{name} {_percent(_fraction(r))} (máximo 20 %)")
    return problems


def _covers(id: str, key: str) -> bool:
    return key == id or key.startswith(f"{id}#")


def inventory_sha256(inventory: Mapping[str, str], id: str) -> str:
    """La huella de `id` en `inventory`, con sus defectos si es un caso. Una retirada la anota y
    solo cubre esa versión: tras reescribir la línea base, otro cambio exige otra retirada."""
    covered = {key: sha for key, sha in inventory.items() if _covers(id, key)}
    return hashlib.sha256(canonical_json(covered)).hexdigest()


def inventory_problems(
    current: Mapping[str, str], previous: Mapping[str, str], retired: Sequence[Retired]
) -> list[str]:
    """Casos y defectos de la línea base que faltan o cambian de huella sin una retirada de su
    versión (`inventory_sha256`). Las altas no necesitan anotación."""
    valid = [r.id for r in retired if r.sha256 == inventory_sha256(previous, r.id)]
    problems = []
    for key, sha in sorted(previous.items()):
        if any(_covers(id, key) for id in valid):
            continue
        if key not in current:
            change = "sale del inventario"
        elif current[key] != sha:
            change = "cambia de huella"
        else:
            continue
        problems.append(
            f"{key} {change} sin una entrada `retired` (sha256 en la línea base: "
            f"{inventory_sha256(previous, key)})"
        )
    return problems


def _unchanged(
    current: Mapping[str, str], previous: Mapping[str, str]
) -> tuple[set[str], set[str]]:
    """Lo que se compara con la línea base: los defectos con su huella y la de su caso intactas,
    y los casos con todo su inventario intacto. Altas, bajas y cambios quedan fuera."""
    same = {key for key, sha in previous.items() if current.get(key) == sha}
    defects = {key for key in same if "#" in key and key.split("#", 1)[0] in same}
    cases = {
        key
        for key in same
        if "#" not in key and inventory_sha256(current, key) == inventory_sha256(previous, key)
    }
    return defects, cases


def regression_problems(
    current: BaselineEntry,
    previous: BaselineEntry,
    inventory: Mapping[str, str],
    previous_inventory: Mapping[str, str],
) -> list[str]:
    """Regresiones de un proveedor y modelo frente a su entrada en la línea base, sobre lo que
    no ha cambiado: perder un defecto o subir A o B."""
    defects, cases = _unchanged(inventory, previous_inventory)
    was, now = defects & set(previous.detected), defects & set(current.detected)
    problems = [f"{d} se detectaba y ya no" for d in sorted(was - now)]
    base = totals([c for c in previous.cases if c.case in cases])
    head = totals([c for c in current.cases if c.case in cases])
    for name, attr in (("A", "false_alarms"), ("B", "correct_with_false_alarms")):
        before, after = _fraction(getattr(base, attr)), _fraction(getattr(head, attr))
        if after > before:
            problems.append(
                f"la tasa {name} de falsas alarmas sube de {_percent(before)} a {_percent(after)}"
            )
    return problems


def _pair(entry: BaselineEntry) -> str:
    return f"{entry.provider}/{entry.model}"


def baseline_gate(
    current: Sequence[BaselineEntry],
    baseline: Baseline,
    inventory: Mapping[str, str],
    retired: Sequence[Retired],
) -> BaselineVerdict:
    """Puerta de la partición de desarrollo frente a la línea base de la rama base."""
    previous = {_pair(e): e for e in baseline.entries}
    verdict = BaselineVerdict(general=inventory_problems(inventory, baseline.inventory, retired))
    present = {_pair(e) for e in current}
    verdict.general += [
        f"falta {pair}, que la línea base tenía" for pair in sorted(previous) if pair not in present
    ]
    for entry in current:
        problems = _deterministic_problems(entry.totals)
        if (before := previous.get(_pair(entry))) is not None:
            problems += regression_problems(entry, before, inventory, baseline.inventory)
        verdict.by_pair[_pair(entry)] = problems
    return verdict
