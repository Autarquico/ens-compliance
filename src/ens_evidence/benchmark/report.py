"""Informe de una ejecución del banco: puntuación por proveedor y modelo, puerta y fuentes.

Siempre lleva el aviso de la visión (punto 5) y el alcance del 100 % determinista. Mide la
pre-auditoría; nunca dictamina sobre un expediente.
"""

import hashlib
from collections.abc import Collection, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from ens_evidence import __version__
from ens_evidence.benchmark.model import (
    TEMPERATURE_NOTE,
    Baseline,
    BenchmarkResult,
    DefectLayer,
    Partition,
    ProviderResult,
    Rate,
    Retired,
)
from ens_evidence.benchmark.runner import Pair, PartitionRun, recording_sha256
from ens_evidence.benchmark.score import (
    NONEMPTY,
    Score,
    baseline_gate,
    entry_of,
    score,
    threshold_problems,
)
from ens_evidence.catalog import catalog_sha256
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import canonical_json

Gate = Literal["baseline", "threshold", "target"]
RESULT_FORMAT = "ens-benchmark-result/1"
TARGET_NOTE = (
    "puerta target: objetivo de ajuste sobre la partición de desarrollo, con los umbrales de v0.1 "
    "sin la muestra mínima; no cierra v0.1, que solo mide la puerta threshold en la reservada."
)


def _sha256(data: object) -> str:
    return hashlib.sha256(canonical_json(data)).hexdigest()


def assemble(
    root: Path,
    partition: Partition,
    run: PartitionRun,
    *,
    now: datetime,
    gate: Gate | None = None,
    gating: Collection[Pair] = (),
    baseline: Baseline | None = None,
    baseline_source: str | None = None,
    inventory: dict[str, str] | None = None,
    retired: Sequence[Retired] = (),
) -> BenchmarkResult:
    """Puntúa cada par y aplica la puerta. Con `threshold` y `target`, solo los pares de `gating`
    deciden y deben estar todos; los demás se informan. Con `baseline`, decide la línea base de
    todos los pares."""
    pack = load_pack()
    scores = {pair: score(run.runs[pair]) for pair in run.pairs}
    results = [_provider_result(root, run, pair, scores[pair]) for pair in run.pairs]
    general: list[str] = []
    passed: bool | None = None
    if gate in ("threshold", "target"):
        minimum = NONEMPTY if gate == "target" else None
        for result in results:
            result.gate_problems = threshold_problems(result.totals, minimum)
            result.gate_passed = not result.gate_problems
        general = [f"falta {p}, que es puerta" for p in sorted(gating) if p not in run.pairs]
        passed = not general and all(r.gate_passed for p, r in _pairs(run, results) if p in gating)
    elif gate == "baseline":
        assert baseline is not None and inventory is not None, "la puerta baseline los necesita"
        entries = [entry_of(p.provider, p.model, scores[p]) for p in run.pairs]
        verdict = baseline_gate(entries, baseline, inventory, retired)
        for pair, result in _pairs(run, results):
            result.gate_problems = verdict.by_pair.get(str(pair), [])
            result.gate_passed = not result.gate_problems
        general = verdict.general
        passed = verdict.passed
    rules = {
        d.expected_rule
        for b in run.cases
        for d in b.case.defects
        if d.layer is DefectLayer.DETERMINISTIC and d.expected_rule is not None
    }
    return BenchmarkResult(
        format=RESULT_FORMAT,
        mode="replay",
        partition=partition,
        gate=gate,
        run_at=now,
        tool_version=__version__,
        pack_version=pack.version,
        pack_sha256=pack.sha256,
        catalog_sha256=catalog_sha256(),
        cases_sha256=_sha256(sorted([b.case.key, b.sha256] for b in run.cases)),
        deterministic_rules=sorted(rules),
        baseline_source=baseline_source if gate == "baseline" else None,
        results=results,
        gate_passed=passed,
        gate_problems=general,
    )


def _pairs(run: PartitionRun, results: list[ProviderResult]) -> list[tuple[Pair, ProviderResult]]:
    return list(zip(run.pairs, results, strict=True))


def _provider_result(root: Path, run: PartitionRun, pair: Pair, scored: Score) -> ProviderResult:
    recordings = sorted(
        (r for (_, p), r in run.recordings.items() if p == pair), key=lambda r: r.case
    )
    destinations = sorted({r.destination for r in recordings})
    return ProviderResult(
        provider=pair.provider,
        model=pair.model,
        destination=" | ".join(destinations) or "sin grabaciones (solo casos deterministas)",
        recordings_sha256=_sha256([[r.case, recording_sha256(root, r)] for r in recordings]),
        recorded_at=sorted({r.recorded_at for r in recordings}),
        recorded_by=sorted({r.recorded_by for r in recordings}),
        cases=scored.cases,
        totals=scored.totals,
    )


# --- texto -----------------------------------------------------------------------------------


def _rate(r: Rate) -> str:
    return f"{r.numerator}/{r.denominator} ({r.value * 100:.1f} %)"


def _marks(hits: list[bool]) -> str:
    return " ".join("sí" if hit else "no" for hit in hits)


def render_text(result: BenchmarkResult, gating: Collection[Pair] = ()) -> str:
    lines = [
        f"Banco de defectos sembrados: partición {result.partition}, modo {result.mode}",
        result.notice,
        result.deterministic_scope,
        f"reglas deterministas en el banco: {', '.join(result.deterministic_rules) or 'ninguna'}",
        f"ejecutado: {result.run_at.isoformat()} · herramienta {result.tool_version}",
        f"pack: {result.pack_version} (sha256 {result.pack_sha256})",
        f"catálogo: sha256 {result.catalog_sha256}",
        f"casos: sha256 {result.cases_sha256}",
        f"puerta: {result.gate or 'ninguna'}"
        + (f" (línea base: {result.baseline_source})" if result.baseline_source else ""),
    ]
    if result.gate == "target":
        lines.append(TARGET_NOTE)
    for provider in result.results:
        pair = Pair(provider.provider, provider.model)
        lines += _provider_lines(provider, result.gate, not gating or pair in gating)
    if result.gate_problems:
        lines.append("")
        lines.append("problemas generales:")
        lines += [f"  - {p}" for p in result.gate_problems]
    lines.append("")
    if result.gate_passed is None:
        lines.append("resultado: sin puerta")
    elif result.gate_passed:
        lines.append(f"resultado: la puerta {result.gate} pasa")
    else:
        closing = "; v0.1 no cierra" if result.gate == "threshold" else ""
        lines.append(f"resultado: la puerta {result.gate} no pasa{closing}")
    if result.gate == "target":
        lines.append(TARGET_NOTE)
    lines.append(result.notice)
    return "\n".join(lines) + "\n"


def _provider_lines(provider: ProviderResult, gate: str | None, decides: bool) -> list[str]:
    t = provider.totals
    lines = [
        "",
        f"== {provider.provider.value}/{provider.model} ==",
        f"destino: {provider.destination}",
        f"temperatura: no registrada; {TEMPERATURE_NOTE}",
        f"grabaciones: sha256 {provider.recordings_sha256}",
        "grabado el: " + (", ".join(a.isoformat() for a in provider.recorded_at) or "-"),
        "grabado por: " + (", ".join(provider.recorded_by) or "-"),
        f"deterministas detectados: {_rate(t.deterministic)}",
        f"juicio detectados (2 de 3): {_rate(t.judgment)}",
        f"tasa A, falsas alarmas / emitidos (mediana): {_rate(t.false_alarms)}; por repetición: "
        + (", ".join(_rate(r) for r in t.false_alarms_by_repetition) or "-"),
        f"tasa B, correctos con falsas alarmas (mediana): {_rate(t.correct_with_false_alarms)}",
        f"hallazgos deterministas sin casar: {t.deterministic_unmatched}",
    ]
    if t.zero_emitted_repetitions:
        shown = ", ".join(map(str, t.zero_emitted_repetitions))
        lines.append(f"repeticiones sin hallazgos de juicio emitidos (tasa A 0): {shown}")
    for case in provider.cases:
        kind = "solo determinista" if not case.judgment else case.kind.value
        lines.append(f"  {case.case} ({kind}; payload {case.payload_sha256 or 'no aplica'})")
        for rep in case.repetitions:
            outcome = rep.outcome.value if rep.outcome else "sin juicio"
            lines.append(
                f"    repetición {rep.n} ({outcome}): emitidos {rep.judgment_emitted}, "
                f"falsas alarmas {rep.judgment_false_alarms}, "
                f"deterministas sin casar {rep.deterministic_unmatched}"
            )
        for defect in case.defects:
            seen = "detectado" if defect.detected else "no detectado"
            lines.append(
                f"    {defect.defect} ({defect.layer.value}): "
                f"{_marks(defect.detected_by_repetition)} → {seen}"
            )
    if provider.gate_passed is not None:
        role = "" if decides else " (informativo: este par no es puerta)"
        lines.append(f"puerta {gate}: {'pasa' if provider.gate_passed else 'no pasa'}{role}")
        lines += [f"  - {p}" for p in provider.gate_problems]
    return lines
