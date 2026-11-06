"""Aceptación del banco de defectos sembrados (spec audit-benchmark): los escenarios «umbral» y
«regresión», de punta a punta por la CLI, con un banco mínimo (`fixtures/bench`) y grabaciones
sintéticas. Sin red ni claves: construir un cliente real o abrir una conexión hace fallar."""

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from ens_evidence.benchmark import runner as runner_module
from ens_evidence.benchmark import score as score_module
from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.baseline import BASELINE_FILE
from ens_evidence.benchmark.cases import load_case
from ens_evidence.benchmark.model import Recording, Repetition, RepetitionOutcome
from ens_evidence.benchmark.recordings import recording_path, write_recording
from ens_evidence.benchmark.runner import REQUIRED_PAIRS, Pair
from ens_evidence.benchmark.score import HOLDOUT_MINIMUM, Sample
from ens_evidence.catalog import catalog_sha256
from ens_evidence.cli import app
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import pack as pack_module
from ens_evidence.preaudit import service
from ens_evidence.preaudit.clients.anthropic import AnthropicProvider
from ens_evidence.preaudit.clients.claude_code import ClaudeCodeProvider
from ens_evidence.preaudit.clients.codex import CodexProvider
from ens_evidence.preaudit.clients.openai import OpenAIProvider
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload

from .test_runner import Integrity, Memo

FIXTURES = Path(__file__).parent / "fixtures"
VERDICT_WORDS = re.compile(r"certific|garantiz|\bconforme\b", re.IGNORECASE)
NOTICE = "Superar el banco no aprueba un expediente real."
AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CLAUDE, CODEX = REQUIRED_PAIRS
DESTINATION = {
    CLAUDE.provider: "claude-code 2.1.0, sesión del usuario; ejecutable claude (sha256 "
    + "1" * 64
    + ")",
    CODEX.provider: "codex 0.160.1, sesión del usuario; ejecutable codex (sha256 " + "0" * 64 + ")",
}
EMPTY = {"findings": []}

DEV_CORRECT = "dev/correcto"
DEV_JUDGED = "dev/normativa-incoherente"
DEV_DETERMINISTIC = "dev/inventario-caducado"
HOLDOUT_CORRECT = "holdout/correcto-reservado"
HOLDOUT_JUDGED = "holdout/procedimientos-sin-responsable"
HOLDOUT_DETERMINISTIC = "holdout/inventario-vencido"
DEFECT_MEASURE = {DEV_JUDGED: "org.2", HOLDOUT_JUDGED: "org.3"}
FALSE_ALARM_MEASURE = "org.1"
"""Medida sin defecto sembrado en ningún caso: un hallazgo sobre ella es una falsa alarma."""

MINI_SAMPLE = Sample(judgment=1, deterministic=1, correct=1)

cli = CliRunner()


# --- sin red ni claves ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("se ha intentado usar un destino real")

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(service, "default_client", refuse)
    for client in (AnthropicProvider, OpenAIProvider, ClaudeCodeProvider, CodexProvider):
        monkeypatch.setattr(client, "__init__", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)


# --- banco mínimo -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    """El banco de `fixtures/bench` con el perfil `mini` que comparten los tests del banco."""
    root = tmp_path_factory.mktemp("acceptance") / "bench"
    shutil.copytree(FIXTURES / "bench", root)
    shutil.copytree(FIXTURES / "profiles" / "dev" / "mini", root / "profiles" / "dev" / "mini")
    return root


@pytest.fixture(scope="module")
def memo(tmp_path_factory, template) -> Memo:
    memo = Memo(tmp_path_factory.mktemp("acceptance-memo"))
    for path in sorted((template / "cases").glob("*/*.yaml")):
        memo(template, load_case(path, template), memo.scratch / "warm" / path.stem)
    return memo


@pytest.fixture(scope="module")
def integrity() -> Integrity:
    return Integrity()


@pytest.fixture(scope="module")
def recorded_dev(tmp_path_factory, template, memo, integrity) -> Path:
    """La partición de desarrollo grabada por los dos pares de la puerta, todo detectado y sin
    falsas alarmas, con su línea base escrita por `benchmark baseline --write`."""
    root = tmp_path_factory.mktemp("acceptance-dev") / "bench"
    shutil.copytree(template, root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner_module, "ProfileCache", lambda scratch: memo)
        patch.setattr(service, "dossier_problems", integrity)
        for pair in REQUIRED_PAIRS:
            record(root, memo, DEV_CORRECT, pair, [EMPTY] * 3)
            record(root, memo, DEV_DETERMINISTIC, pair, [EMPTY] * 3)
            record(root, memo, DEV_JUDGED, pair, [detecting(memo, DEV_JUDGED)] * 3)
        written = cli.invoke(app, ["benchmark", "baseline", "--cases", str(root), "--write"])
    assert written.exit_code == 0, written.output
    return root


def bench_from(source: Path, tmp_path: Path, memo, integrity, monkeypatch) -> Path:
    root = tmp_path / "bench"
    shutil.copytree(source, root)
    monkeypatch.setattr(runner_module, "ProfileCache", lambda scratch: memo)
    monkeypatch.setattr(service, "dossier_problems", integrity)
    return root


@pytest.fixture
def holdout(tmp_path, template, memo, integrity, monkeypatch) -> Path:
    """La reservada mínima (un defecto de cada capa y un correcto), medida con esa muestra."""
    monkeypatch.setattr(score_module, "HOLDOUT_MINIMUM", MINI_SAMPLE)
    return bench_from(template, tmp_path, memo, integrity, monkeypatch)


@pytest.fixture
def dev(tmp_path, recorded_dev, memo, integrity, monkeypatch) -> Path:
    return bench_from(recorded_dev, tmp_path, memo, integrity, monkeypatch)


# --- grabaciones sintéticas -------------------------------------------------------------------


def finding(memo: Memo, key: str, measure_id: str, alias: str = "revision-anual") -> dict:
    """Un hallazgo de juicio válido para `validate_findings`: cita literalmente un documento y
    una evidencia enlazados a la medida en el payload del caso."""
    build = memo.done[key]
    assert build.dossier is not None
    data = build_payload(build.store, build.dossier, load_pack()).data["dossier"]
    measure = next(m for m in data["measures"] if m["id"] == measure_id)
    evidence = build.evidence_ids[alias]
    assert evidence in measure["evidence"]
    texts = {e["ref"]: e["text"] for e in data["evidence"]} | {
        d["ref"]: d["text"] for d in data["documents"]
    }

    def cite(ref: str) -> dict:
        line = next(line for line in texts[ref].splitlines() if len(line.split()) >= 8)
        return {"ref": ref, "quote": line[:100].rsplit(" ", 1)[0].strip()}

    return {
        "criterion": "coherencia-politica-evidencia",
        "measure": measure_id,
        "proposal": "OBSERVACION",
        "documents": [cite(measure["documents"][0])],
        "evidence": [cite(evidence)],
        "justifications": [],
        "reason": "El acta de revisión no recoge lo que exige el documento aprobado.",
    }


def detecting(memo: Memo, key: str) -> dict:
    return {"findings": [finding(memo, key, DEFECT_MEASURE[key])]}


def detecting_with_false_alarm(memo: Memo, key: str) -> dict:
    return {
        "findings": [
            finding(memo, key, DEFECT_MEASURE[key]),
            finding(memo, key, FALSE_ALARM_MEASURE),
        ]
    }


def false_alarm(memo: Memo, key: str) -> dict:
    return {"findings": [finding(memo, key, FALSE_ALARM_MEASURE)]}


def record(root: Path, memo: Memo, key: str, pair: Pair, replies: list[dict]) -> Path:
    """Lo que habría escrito `benchmark record`, ligado al pack y al payload actuales."""
    build = memo.done[key]
    assert build.dossier is not None
    pack = load_pack()
    recording = Recording(
        format="ens-benchmark-recording/1",
        case=key,
        case_sha256="a" * 64,
        provider=pair.provider,
        model=pair.model,
        destination=DESTINATION[pair.provider],
        pack_version=pack.version,
        pack_sha256=pack.sha256,
        payload_sha256=build_payload(build.store, build.dossier, pack).sha256,
        tool_version="0.1.0",
        catalog_sha256=catalog_sha256(),
        recorded_at=AT,
        recorded_by="Ana Prueba",
        repetitions=[
            Repetition(
                n=n,
                at=AT,
                outcome=RepetitionOutcome.RESPONSE,
                response=json.dumps(reply, ensure_ascii=False),
            )
            for n, reply in enumerate(replies, start=1)
        ],
    )
    return write_recording(root, recording, replace=True)


def record_holdout(root: Path, memo: Memo, pair: Pair, judged: list[dict], correct=None) -> None:
    record(root, memo, HOLDOUT_CORRECT, pair, correct or [EMPTY] * 3)
    record(root, memo, HOLDOUT_DETERMINISTIC, pair, [EMPTY] * 3)
    record(root, memo, HOLDOUT_JUDGED, pair, judged)


def run(root: Path, *args: str):
    return cli.invoke(app, ["benchmark", "run", "--cases", str(root), *args])


def measure_holdout(root: Path, *args: str):
    return run(root, "--partition", "holdout", "--gate", "threshold", "--measurement", *args)


def by_pair(pair: Pair) -> list[str]:
    return ["--provider", pair.provider.value, "--model", pair.model]


def ledger(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / MEASUREMENTS_FILE).read_text().splitlines()]


def assert_report(output: str) -> None:
    assert NOTICE in output, output
    assert not VERDICT_WORDS.search(output), output


# --- scenario: umbral -------------------------------------------------------------------------


def test_umbral_sale_con_0_para_el_proveedor_que_llega_y_con_1_para_el_que_no(holdout, memo):
    """Claude detecta el defecto de juicio en 2 de 3 repeticiones (mayoría); Codex en ninguna."""
    judged = detecting(memo, HOLDOUT_JUDGED)
    record_holdout(holdout, memo, CLAUDE, [judged, EMPTY, judged])
    record_holdout(holdout, memo, CODEX, [EMPTY] * 3)

    passing = measure_holdout(holdout, *by_pair(CLAUDE))
    failing = measure_holdout(holdout, *by_pair(CODEX))

    assert passing.exit_code == 0, passing.output
    assert "juicio detectados (2 de 3): 1/1 (100.0 %)" in passing.output
    assert "deterministas detectados: 1/1 (100.0 %)" in passing.output
    assert "resultado: la puerta threshold pasa" in passing.output
    assert failing.exit_code == 1, failing.output
    assert "detección de juicio 0.0 % (mínimo 80 %)" in failing.output
    assert "v0.1 no cierra" in failing.output
    assert_report(passing.output)
    assert_report(failing.output)


@pytest.mark.parametrize("failing", [CLAUDE, CODEX], ids=["claude-code", "codex"])
def test_si_una_familia_de_proveedor_no_llega_v01_no_cierra_y_queda_anotado(holdout, memo, failing):
    for pair in REQUIRED_PAIRS:
        judged = [EMPTY] * 3 if pair == failing else [detecting(memo, HOLDOUT_JUDGED)] * 3
        record_holdout(holdout, memo, pair, judged)

    result = measure_holdout(holdout, "--all-recorded")

    assert result.exit_code == 1, result.output
    assert "resultado: la puerta threshold no pasa; v0.1 no cierra" in result.output
    assert_report(result.output)
    lines = ledger(holdout)
    assert [line["action"] for line in lines] == ["run", "run-result", "run-result"]
    assert all(line["pack_sha256"] == load_pack().sha256 for line in lines)
    measured = {
        Pair(Provider(line["provider"]), line["model"]): line["result"]
        for line in lines
        if "result" in line
    }
    assert {pair: m["gate_passed"] for pair, m in measured.items()} == {
        pair: pair != failing for pair in REQUIRED_PAIRS
    }
    assert all(m["gate"] == "threshold" for m in measured.values())


def test_los_dos_pares_de_la_puerta_llegan_y_v01_cierra_el_umbral(holdout, memo):
    for pair in REQUIRED_PAIRS:
        record_holdout(holdout, memo, pair, [detecting(memo, HOLDOUT_JUDGED)] * 3)

    result = measure_holdout(holdout, "--all-recorded")

    assert result.exit_code == 0, result.output
    assert "resultado: la puerta threshold pasa" in result.output
    assert "v0.1 no cierra" not in result.output
    assert_report(result.output)


def test_un_par_extra_no_decide_el_umbral(holdout, memo):
    extra = Pair(CLAUDE.provider, "otro-modelo")
    for pair in REQUIRED_PAIRS:
        record_holdout(holdout, memo, pair, [detecting(memo, HOLDOUT_JUDGED)] * 3)
    record_holdout(holdout, memo, extra, [EMPTY] * 3)

    result = measure_holdout(holdout, "--all-recorded")

    assert result.exit_code == 0, result.output
    assert "(informativo: este par no es puerta)" in result.output


def test_con_la_muestra_de_d7_la_reservada_minima_no_basta(holdout, memo, monkeypatch):
    monkeypatch.setattr(score_module, "HOLDOUT_MINIMUM", HOLDOUT_MINIMUM)
    record_holdout(holdout, memo, CLAUDE, [detecting(memo, HOLDOUT_JUDGED)] * 3)

    result = measure_holdout(holdout, *by_pair(CLAUDE))

    assert result.exit_code == 1, result.output
    assert "defectos de juicio: 1 (la muestra exige al menos 20)" in result.output
    assert "defectos deterministas: 1 (la muestra exige al menos 10)" in result.output
    assert "expedientes correctos: 1 (la muestra exige al menos 3)" in result.output
    assert "resultado: la puerta threshold no pasa" in result.output


def test_la_tasa_a_de_falsas_alarmas_por_encima_del_20_es_puerta(holdout, memo):
    """Detecta el defecto pero emite además una falsa alarma: A = 1/2 en cada repetición."""
    record_holdout(holdout, memo, CLAUDE, [detecting_with_false_alarm(memo, HOLDOUT_JUDGED)] * 3)

    result = measure_holdout(holdout, *by_pair(CLAUDE))

    assert result.exit_code == 1, result.output
    assert "juicio detectados (2 de 3): 1/1 (100.0 %)" in result.output
    assert "falsas alarmas sobre emitidos (A) 50.0 % (máximo 20 %)" in result.output
    assert "expedientes correctos con falsas alarmas (B)" not in result.output
    assert_report(result.output)


def test_la_tasa_b_de_expedientes_correctos_con_falsas_alarmas_es_puerta(holdout, memo):
    record_holdout(
        holdout,
        memo,
        CLAUDE,
        [detecting(memo, HOLDOUT_JUDGED)] * 3,
        correct=[false_alarm(memo, HOLDOUT_CORRECT)] * 3,
    )

    result = measure_holdout(holdout, *by_pair(CLAUDE))

    assert result.exit_code == 1, result.output
    assert "tasa B, correctos con falsas alarmas (mediana): 1/1 (100.0 %)" in result.output
    assert "expedientes correctos con falsas alarmas (B) 100.0 % (máximo 20 %)" in result.output
    assert_report(result.output)


def test_la_reservada_sin_measurement_no_se_mide_ni_se_anota(holdout, memo):
    record_holdout(holdout, memo, CLAUDE, [detecting(memo, HOLDOUT_JUDGED)] * 3)

    result = run(holdout, "--partition", "holdout", "--gate", "threshold", *by_pair(CLAUDE))

    assert result.exit_code == 2, result.output
    assert "--measurement" in result.output
    assert not (holdout / MEASUREMENTS_FILE).exists()


def test_el_umbral_sin_un_par_de_la_puerta_sale_con_3(holdout, memo):
    record_holdout(holdout, memo, CLAUDE, [detecting(memo, HOLDOUT_JUDGED)] * 3)

    result = measure_holdout(holdout, "--all-recorded")

    assert result.exit_code == 3, result.output
    assert f"falta la grabación de {CODEX}, que es puerta" in result.output


# --- scenario: regresión ----------------------------------------------------------------------


def ci_run(root: Path, *extra: str):
    """Lo que ejecuta el job de CI sobre la partición de desarrollo."""
    return run(root, "--all-recorded", "--gate", "baseline", *extra)


def test_sin_cambios_la_particion_de_desarrollo_pasa_contra_su_linea_base(dev):
    result = ci_run(dev)

    assert result.exit_code == 0, result.output
    assert f"línea base: {BASELINE_FILE}" in result.output
    assert_report(result.output)


@pytest.fixture
def altered_pack(tmp_path, monkeypatch) -> str:
    """Una copia del audit pack con el prompt cambiado y su manifiesto al día: íntegro, pero
    otro pack. Solo este test lo ve."""
    pack = tmp_path / "pack"
    shutil.copytree(Path(str(pack_module.CURRENT_PACK)), pack)
    system = pack / "system.md"
    system.write_text(system.read_text(encoding="utf-8") + "\nCambio de prueba.\n", "utf-8")
    manifest = json.loads((pack / "manifest.json").read_text())
    manifest["files"]["system.md"] = hashlib.sha256(system.read_bytes()).hexdigest()
    (pack / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    before = load_pack().sha256
    monkeypatch.setattr(pack_module, "CURRENT_PACK", pack)
    assert load_pack().sha256 != before
    return load_pack().sha256


def test_un_cambio_en_el_audit_pack_sin_regrabar_sale_con_3(dev, altered_pack):
    result = ci_run(dev)

    assert result.exit_code == 3, result.output
    assert "regraba" in result.output

    checked = cli.invoke(app, ["benchmark", "validate", "--cases", str(dev), "--recordings"])
    assert checked.exit_code == 3, checked.output
    assert "el audit pack ha cambiado; regraba" in checked.output


def test_una_grabacion_nueva_que_pierde_un_defecto_sale_con_1(dev, memo):
    record(dev, memo, DEV_JUDGED, CODEX, [EMPTY] * 3)

    result = ci_run(dev)

    assert result.exit_code == 1, result.output
    assert f"{DEV_JUDGED}#normativa-incoherente se detectaba y ya no" in result.output
    assert_report(result.output)


def test_una_subida_de_la_tasa_a_frente_a_la_linea_base_sale_con_1(dev, memo):
    record(dev, memo, DEV_JUDGED, CLAUDE, [detecting_with_false_alarm(memo, DEV_JUDGED)] * 3)

    result = ci_run(dev)

    assert result.exit_code == 1, result.output
    assert "la tasa A de falsas alarmas sube de 0.0 % a 50.0 %" in result.output
    assert "se detectaba y ya no" not in result.output
    assert_report(result.output)


def test_una_subida_de_la_tasa_b_frente_a_la_linea_base_sale_con_1(dev, memo):
    record(dev, memo, DEV_CORRECT, CODEX, [false_alarm(memo, DEV_CORRECT)] * 3)

    result = ci_run(dev)

    assert result.exit_code == 1, result.output
    assert "la tasa B de falsas alarmas sube de 0.0 % a 100.0 %" in result.output
    assert_report(result.output)


def test_si_falta_un_par_requerido_sale_con_3(dev):
    for key in (DEV_CORRECT, DEV_JUDGED, DEV_DETERMINISTIC):
        recording_path(dev, key, CODEX.provider, CODEX.model).unlink()

    result = ci_run(dev)

    assert result.exit_code == 3, result.output
    assert f"falta la grabación de {CODEX}, que es puerta" in result.output
