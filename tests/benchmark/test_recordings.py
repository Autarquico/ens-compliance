"""Grabaciones del banco: `prepare`, `record` y su repetición. Sin red ni claves: la API de
Anthropic con `httpx.MockTransport` y un `codex` falso en el PATH."""

import json
import re
import shutil
import stat
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from ens_evidence.benchmark import recordings
from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.build import Build, build_bench_profile
from ens_evidence.benchmark.cases import load_recording, recording_problems
from ens_evidence.benchmark.model import Partition, Recording, RepetitionOutcome
from ens_evidence.benchmark.recordings import (
    RecordingRefused,
    load_judgment_cases,
    portable_destination,
    prepare_cases,
    record_case,
    recording_path,
    write_recording,
)
from ens_evidence.benchmark.replay import (
    UNPARSEABLE,
    Recorder,
    RecordingStale,
    replay_factory,
    replayed_error,
)
from ens_evidence.cli import app
from ens_evidence.models.preaudit import LayerStatus, Provider
from ens_evidence.preaudit import service
from ens_evidence.preaudit.adapter import ProviderError, ProviderErrorKind
from ens_evidence.preaudit.clients.anthropic import TOOL_NAME, AnthropicProvider
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.workspace import PreAuditSettings

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = "Modelo X.1"
SLUG = "modelo-x.1"
CORRECT = """format: ens-benchmark-case/1
id: correcto
partition: {partition}
profile: mini
kind: correct
"""
DETERMINISTIC_ONLY = """format: ens-benchmark-case/1
id: solo-determinista
partition: dev
profile: mini
kind: correct
judgment: false
"""
EMPTY = {"findings": []}
SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
FINGERPRINT = re.compile(r"huella combinada \(sha256\): ([0-9a-f]{64})")
runner = CliRunner()


def finding(reason: str) -> dict:
    return {
        "criterion": "coherencia-politica-evidencia",
        "measure": "op.exp.1",
        "proposal": "OBSERVACION",
        "documents": [],
        "evidence": [],
        "justifications": [],
        "reason": reason,
    }


# --- mundo -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mini(tmp_path_factory) -> Build:
    """El perfil mini construido una vez; cada caso es una copia suya."""
    return build_bench_profile(
        FIXTURES, Partition.DEV, "mini", tmp_path_factory.mktemp("mini") / "ws"
    )


def fork(mini: Build):
    def builder(root: Path, case, destination: Path) -> Build:
        return replace(mini.fork(destination), judgment=case.judgment)

    return builder


@pytest.fixture
def bench(tmp_path, mini, monkeypatch) -> Path:
    """Un banco con el perfil mini, un caso correcto de juicio y uno solo determinista. Los
    casos se construyen copiando `mini` (sin pagar la construcción en cada test)."""
    root = tmp_path / "bench"
    shutil.copytree(FIXTURES / "profiles" / "dev" / "mini", root / "profiles" / "dev" / "mini")
    for partition in Partition:
        directory = root / "cases" / partition.value
        directory.mkdir(parents=True)
        (directory / "correcto.yaml").write_text(CORRECT.format(partition=partition.value))
    (root / "cases" / "dev" / "solo-determinista.yaml").write_text(DETERMINISTIC_ONLY)
    monkeypatch.setattr(recordings, "build_case", fork(mini))
    return root


class FakeAnthropic:
    """La Messages API: cada petición devuelve la siguiente entrada de `replies`."""

    def __init__(self, monkeypatch, *replies: dict) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

        def client(provider, config):
            assert provider is Provider.ANTHROPIC
            return AnthropicProvider(
                config.base_url or service.DEFAULT_BASE_URL[provider],
                env={"ANTHROPIC_API_KEY": "clave-de-prueba"},
                transport=httpx.MockTransport(self.handler),
            )

        monkeypatch.setattr(service, "default_client", client)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        tool_input = self.replies[min(len(self.requests), len(self.replies)) - 1]
        return httpx.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "toolu_01", "name": TOOL_NAME, "input": tool_input}
                ],
            },
        )


FAKE_CODEX = """
import os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.160.1")
    sys.exit(0)
sys.stdin.buffer.read()
here = os.path.dirname(os.path.abspath(sys.argv[0]))
with open(os.path.join(here, "codex.log"), "a") as log:
    log.write("called\\n")
with open(os.path.join(here, "reply.json"), encoding="utf-8") as reply:
    text = reply.read()
with open(args[args.index("-o") + 1], "w", encoding="utf-8") as out:
    out.write(text)
"""


class FakeCodex:
    """`codex` falso en el PATH: escribe tal cual `reply` y anota cada envío."""

    def __init__(self, tmp_path: Path, monkeypatch, reply: str) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        script = self.bin / "codex"
        script.write_text(f"#!{sys.executable}\n{FAKE_CODEX}")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.bin / "reply.json").write_text(reply, encoding="utf-8")
        self.log = self.bin / "codex.log"
        monkeypatch.setenv("PATH", str(self.bin))

    @property
    def calls(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0


def invoke(*args: str):
    return runner.invoke(app, ["benchmark", *args])


def prepare(bench: Path, provider: str, *extra: str) -> str:
    result = invoke(
        "prepare", "--cases", str(bench), "--provider", provider, "--model", MODEL, *extra
    )
    assert result.exit_code == 0, result.output
    return FINGERPRINT.search(result.output).group(1)


def record(bench: Path, provider: str, consent: str, *extra: str):
    return invoke(
        "record",
        "--cases",
        str(bench),
        "--provider",
        provider,
        "--model",
        MODEL,
        "--consent",
        consent,
        "--by",
        "Responsable Ficticia",
        *extra,
    )


def strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [value] if isinstance(value, str) else []


# --- prepare y record ------------------------------------------------------------------------


def test_prepare_no_envia_e_imprime_cada_huella_y_la_combinada(bench, monkeypatch):
    fake = FakeAnthropic(monkeypatch, EMPTY)
    result = invoke("prepare", "--cases", str(bench), "--provider", "anthropic", "--model", MODEL)
    assert result.exit_code == 0, result.output
    assert re.search(r"dev/correcto  [0-9a-f]{64}", result.output)
    assert "solo deterministas, sin juicio que grabar: dev/solo-determinista" in result.output
    assert "destino: anthropic https://api.anthropic.com" in result.output
    assert FINGERPRINT.search(result.output)
    assert fake.requests == []
    assert prepare(bench, "anthropic") == FINGERPRINT.search(result.output).group(1)


def test_record_graba_tres_repeticiones_ligadas_al_pack_y_al_payload(bench, monkeypatch):
    fake = FakeAnthropic(monkeypatch, EMPTY)
    consent = prepare(bench, "anthropic")
    result = record(bench, "anthropic", consent)
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 3
    path = bench / "recordings" / "dev" / "correcto" / f"anthropic--{SLUG}.json"
    recording = load_recording(path)
    assert [r.n for r in recording.repetitions] == [1, 2, 3]
    assert {r.outcome for r in recording.repetitions} == {RepetitionOutcome.RESPONSE}
    assert json.loads(recording.repetitions[0].response) == EMPTY
    assert recording.case == "dev/correcto"
    assert recording.provider is Provider.ANTHROPIC and recording.model == MODEL
    assert recording.destination == "anthropic https://api.anthropic.com"
    assert recording.recorded_by == "Responsable Ficticia"
    raw = json.loads(path.read_text())
    assert raw["temperature"] is None and raw["temperature_note"].startswith("no aplica")
    assert not (bench / "recordings" / "dev" / "solo-determinista").exists()


def test_sin_la_huella_combinada_correcta_no_se_envia_nada(bench, monkeypatch):
    fake = FakeAnthropic(monkeypatch, EMPTY)
    result = record(bench, "anthropic", "0" * 64)
    assert result.exit_code == 1, result.output
    assert "No se ha enviado nada" in result.output
    assert fake.requests == []
    assert not (bench / "recordings").exists()


def test_la_huella_combinada_liga_el_modelo(bench, monkeypatch):
    FakeAnthropic(monkeypatch, EMPTY)
    consent = prepare(bench, "anthropic")
    other = invoke("prepare", "--cases", str(bench), "--provider", "anthropic", "--model", "otro")
    assert FINGERPRINT.search(other.output).group(1) != consent


def test_no_sobrescribe_sin_replace(bench, monkeypatch):
    fake = FakeAnthropic(monkeypatch, EMPTY)
    consent = prepare(bench, "anthropic")
    assert record(bench, "anthropic", consent, "--repetitions", "1").exit_code == 0
    again = record(bench, "anthropic", consent, "--repetitions", "1")
    assert again.exit_code == 1 and "usa --replace" in again.output
    assert len(fake.requests) == 1
    replaced = record(bench, "anthropic", consent, "--repetitions", "2", "--replace")
    assert replaced.exit_code == 0, replaced.output
    path = recording_path(bench, "dev/correcto", Provider.ANTHROPIC, MODEL)
    assert len(load_recording(path).repetitions) == 2


def test_un_nombre_declarado_invalido_no_envia(bench, monkeypatch):
    fake = FakeAnthropic(monkeypatch, EMPTY)
    consent = prepare(bench, "anthropic")
    result = invoke(
        "record", "--cases", str(bench), "--provider", "anthropic", "--model", MODEL,
        "--consent", consent, "--by", "persona@correo.es",
    )  # fmt: skip
    assert result.exit_code == 2, result.output
    assert fake.requests == []


def test_la_reservada_exige_measurement_y_queda_anotada(bench, monkeypatch):
    fake = FakeAnthropic(monkeypatch, EMPTY)
    locked = invoke(
        "prepare", "--cases", str(bench), "--partition", "holdout",
        "--provider", "anthropic", "--model", MODEL,
    )  # fmt: skip
    assert locked.exit_code == 2 and "--measurement" in locked.output
    assert not (bench / MEASUREMENTS_FILE).exists()
    consent = prepare(bench, "anthropic", "--partition", "holdout", "--measurement")
    result = record(bench, "anthropic", consent, "--partition", "holdout", "--measurement")
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 3
    lines = [json.loads(line) for line in (bench / MEASUREMENTS_FILE).read_text().splitlines()]
    assert [line["action"] for line in lines] == ["prepare", "record"]
    assert {line["model"] for line in lines} == {MODEL}


def test_codex_sin_accept_shell_risk_no_envia(bench, tmp_path, monkeypatch):
    codex = FakeCodex(tmp_path, monkeypatch, json.dumps(EMPTY))
    consent = prepare(bench, "codex")
    result = record(bench, "codex", consent)
    assert result.exit_code == 1, result.output
    assert "--accept-shell-risk" in result.output
    assert codex.calls == 0
    assert not (bench / "recordings").exists()


@pytest.mark.parametrize(("provider", "flagged"), [("codex", True), ("anthropic", False)])
def test_la_orden_que_sugiere_prepare_lleva_accept_shell_risk_si_hace_falta(
    bench, tmp_path, monkeypatch, provider, flagged
):
    if flagged:
        FakeCodex(tmp_path, monkeypatch, json.dumps(EMPTY))
    else:
        FakeAnthropic(monkeypatch, EMPTY)
    result = invoke("prepare", "--cases", str(bench), "--provider", provider, "--model", MODEL)
    assert result.exit_code == 0, result.output
    [suggested] = [line for line in result.output.splitlines() if line.startswith("Para grabar")]
    assert suggested.endswith("--accept-shell-risk") is flagged


def test_codex_graba_el_destino_sin_rutas_absolutas(bench, tmp_path, monkeypatch):
    codex = FakeCodex(tmp_path, monkeypatch, json.dumps(EMPTY))
    consent = prepare(bench, "codex")
    result = record(bench, "codex", consent, "--accept-shell-risk")
    assert result.exit_code == 0, result.output
    assert codex.calls == 3
    path = recording_path(bench, "dev/correcto", Provider.CODEX, MODEL)
    text = path.read_text()
    assert str(tmp_path) not in text and str(Path.home()) not in text
    for value in strings(json.loads(text)):
        assert not re.search(r"(?:^|\s)(?:/|~|[A-Za-z]:\\)", value), value
    recording = load_recording(path)
    assert "ejecutable codex (sha256 " in recording.destination
    assert recording.destination.startswith("codex 0.160.1")


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps({"findings": [finding(f"La evidencia muestra {SECRET} en claro.")]}),
        # El mismo secreto con escapes Unicode: el texto crudo no lo deja ver.
        json.dumps({"findings": [finding(f"La evidencia muestra {SECRET} en claro.")]}).replace(
            "ghp_", "\\u0067\\u0068\\u0070_"
        ),
    ],
    ids=["en-claro", "escape-unicode"],
)
def test_una_respuesta_con_un_secreto_no_se_escribe(bench, tmp_path, monkeypatch, reply):
    assert json.loads(reply)["findings"][0]["reason"].count(SECRET) == 1
    codex = FakeCodex(tmp_path, monkeypatch, reply)
    consent = prepare(bench, "codex")
    result = record(bench, "codex", consent, "--accept-shell-risk")
    assert result.exit_code == 1, result.output
    assert codex.calls == 3
    assert "parece un secreto" in result.output
    assert SECRET not in result.output
    assert not recording_path(bench, "dev/correcto", Provider.CODEX, MODEL).exists()


# --- grabar: lo que se guarda de cada envío ----------------------------------------------------


class Scripted:
    """Un destino de juicio que devuelve, en orden, cada texto o `ProviderError` de `script`."""

    requires_shell_warning = False
    destination = "anthropic https://api.anthropic.com"

    def __init__(self, script: list) -> None:
        self.script = script

    def judge(self, system_prompt, payload, schema, config) -> str:
        step = self.script.pop(0)
        if isinstance(step, ProviderError):
            raise step
        return step


def prepared_case(bench: Path, tmp_path: Path, mini: Build, factory=None):
    settings = PreAuditSettings(provider=Provider.ANTHROPIC, model=MODEL)
    judged, _ = load_judgment_cases(bench, Partition.DEV)
    partition = prepare_cases(
        bench,
        judged,
        settings,
        load_pack().sha256,
        tmp_path / "scratch",
        builder=fork(mini),
        client_factory=factory,
    )
    return settings, partition.cases[0]


def recorded(bench, tmp_path, mini, script) -> tuple[Recording, object]:
    recorder = Recorder(lambda provider, settings: Scripted(script))
    settings, case = prepared_case(bench, tmp_path, mini, recorder)
    recording = record_case(
        case,
        settings,
        by="Responsable Ficticia",
        repetitions=3,
        accept_shell_risk=False,
        recorder=recorder,
        clock=lambda: datetime(2026, 11, 4, tzinfo=UTC),
    )
    return recording, case


def test_se_graba_solo_lo_que_devuelve_judge(bench, tmp_path, mini):
    timeout = ProviderError(ProviderErrorKind.TIMEOUT, "el proveedor no respondió a tiempo")
    recording, _ = recorded(bench, tmp_path, mini, [json.dumps(EMPTY), '{"otra": 1}', timeout])
    first, second, third = recording.repetitions
    assert first.outcome is RepetitionOutcome.RESPONSE and first.response == json.dumps(EMPTY)
    assert second.outcome is RepetitionOutcome.SCHEMA_ERROR and second.response is None
    assert third.outcome is RepetitionOutcome.PROVIDER_ERROR
    assert third.kind is ProviderErrorKind.TIMEOUT and third.response is None


def test_el_destino_de_una_cli_pierde_la_ruta_del_ejecutable():
    destination = (
        "codex 0.160.1, sesión del usuario (plan contratado); ejecutable "
        "/Users/x/Application Support/bin/codex (sha256 " + "ab" * 32 + "); aviso"
    )
    shown = portable_destination(destination)
    assert shown.startswith("codex 0.160.1") and "ejecutable codex (sha256 " in shown
    assert "/Users" not in shown
    assert "C:" not in portable_destination(r"x; ejecutable C:\tools\codex.exe (sha256 00); y")


def test_el_destino_compuesto_por_el_producto_no_se_toma_por_un_dato_real(bench, tmp_path, mini):
    recording, _ = recorded(bench, tmp_path, mini, [json.dumps(EMPTY)] * 3)
    for digest in ("308831041ea4863c3f87d222c31f759411898c874a9006b4bd6c745858b8f3bd", "0" * 64):
        cli = recording.model_copy(
            update={"destination": f"codex 0.160.1; ejecutable codex (sha256 {digest}); aviso"}
        )
        assert recording_problems(cli) == []
    assert recording_problems(recording) == []
    leaked = recording.model_copy(update={"destination": f"openai https://u:{SECRET}@x.example"})
    assert recording_problems(leaked)
    with pytest.raises(RecordingRefused):
        write_recording(bench, leaked)


# --- repetir ---------------------------------------------------------------------------------


def replay(case, settings, recording: Recording, n: int):
    build = case.build
    factory = replay_factory(recording, n)
    prepared = service.prepare(build.store, build.dossier, None, settings, now=build.tick())
    return service.run(
        build.store,
        build.dossier,
        None,
        prepared.payload.sha256,
        "Responsable Ficticia",
        settings=settings,
        now=build.tick(),
        client_factory=factory,
    )


def test_la_repeticion_reproduce_cada_resultado_grabado(bench, tmp_path, mini):
    timeout = ProviderError(ProviderErrorKind.TIMEOUT, "el proveedor no respondió a tiempo")
    recording, case = recorded(bench, tmp_path, mini, [json.dumps(EMPTY), "no es json", timeout])
    settings = PreAuditSettings(provider=Provider.ANTHROPIC, model=MODEL)
    done, schema, failed = (replay(case, settings, recording, n).record.run for n in (1, 2, 3))
    assert done.judgment_status is LayerStatus.DONE
    assert schema.judgment_status is LayerStatus.ERROR
    assert failed.judgment_status is LayerStatus.ERROR
    assert failed.judgment_error == replayed_error(ProviderErrorKind.TIMEOUT)
    assert UNPARSEABLE == ""


@pytest.mark.parametrize("field", ["pack_sha256", "payload_sha256"])
def test_un_pack_o_payload_distinto_del_grabado_aborta(bench, tmp_path, mini, field):
    recording, case = recorded(bench, tmp_path, mini, [json.dumps(EMPTY)] * 3)
    settings = PreAuditSettings(provider=Provider.ANTHROPIC, model=MODEL)
    stale = recording.model_copy(update={field: "f" * 64})
    with pytest.raises(RecordingStale):
        replay(case, settings, stale, 1)
    assert not issubclass(RecordingStale, ProviderError)


def test_el_pack_cargado_se_compara_con_el_grabado(bench, tmp_path, mini, monkeypatch):
    from ens_evidence.benchmark import replay as replay_module

    recording, case = recorded(bench, tmp_path, mini, [json.dumps(EMPTY)] * 3)
    settings = PreAuditSettings(provider=Provider.ANTHROPIC, model=MODEL)
    real = replay_module.load_pack()
    monkeypatch.setattr(replay_module, "load_pack", lambda: replace(real, sha256="e" * 64))
    with pytest.raises(RecordingStale):
        replay(case, settings, recording, 1)


def test_la_repeticion_exige_el_proveedor_el_modelo_y_la_repeticion_grabados(bench, tmp_path, mini):
    recording, case = recorded(bench, tmp_path, mini, [json.dumps(EMPTY)] * 3)
    with pytest.raises(RecordingStale):
        replay_factory(recording, 4)(Provider.ANTHROPIC, case_settings())
    with pytest.raises(ProviderError):
        replay_factory(recording, 1)(Provider.ANTHROPIC, case_settings("otro"))


def case_settings(model: str = MODEL) -> PreAuditSettings:
    return PreAuditSettings(provider=Provider.ANTHROPIC, model=model)


# --- --pending -------------------------------------------------------------------------------

KEYS = ["dev/bueno", "dev/desligado", "dev/con-error", "dev/sin-grabar"]


def pending_bench(bench: Path, monkeypatch) -> FakeAnthropic:
    """Cuatro casos de juicio, todos grabados; después uno se desliga, otro queda con un
    `provider_error` y otro sin fichero. Devuelve el fake con el contador de envíos a cero."""
    for key in KEYS:
        (bench / "cases" / "dev" / f"{key.split('/')[1]}.yaml").write_text(
            CORRECT.format(partition="dev").replace("id: correcto", f"id: {key.split('/')[1]}")
        )
    fake = FakeAnthropic(monkeypatch, EMPTY)
    assert record(bench, "anthropic", prepare(bench, "anthropic")).exit_code == 0
    stale = recording_path(bench, "dev/desligado", Provider.ANTHROPIC, MODEL)
    raw = json.loads(stale.read_text())
    stale.write_text(json.dumps({**raw, "payload_sha256": "f" * 64}))
    errored = recording_path(bench, "dev/con-error", Provider.ANTHROPIC, MODEL)
    recording = load_recording(errored)
    broken = recording.repetitions[1].model_copy(
        update={
            "outcome": RepetitionOutcome.PROVIDER_ERROR,
            "kind": ProviderErrorKind.TIMEOUT,
            "response": None,
        }
    )
    reps = [recording.repetitions[0], broken, recording.repetitions[2]]
    write_recording(bench, recording.model_copy(update={"repetitions": reps}), replace=True)
    recording_path(bench, "dev/sin-grabar", Provider.ANTHROPIC, MODEL).unlink()
    fake.requests.clear()
    return fake


def test_pending_selecciona_solo_desligadas_con_error_y_sin_grabar(bench, monkeypatch):
    pending_bench(bench, monkeypatch)
    result = invoke(
        "prepare", "--cases", str(bench), "--provider", "anthropic", "--model", MODEL, "--pending"
    )
    assert result.exit_code == 0, result.output
    listed = re.findall(r"^(dev/[a-z-]+)  [0-9a-f]{64}$", result.output, re.MULTILINE)
    assert sorted(listed) == ["dev/con-error", "dev/desligado", "dev/sin-grabar"]
    assert "--pending" in result.output.splitlines()[-1]


def test_la_huella_pending_difiere_de_la_completa(bench, monkeypatch):
    pending_bench(bench, monkeypatch)
    assert prepare(bench, "anthropic", "--pending") != prepare(bench, "anthropic")


def test_record_pending_envia_solo_lo_pendiente_y_no_toca_lo_bueno(bench, monkeypatch):
    fake = pending_bench(bench, monkeypatch)
    good = recording_path(bench, "dev/bueno", Provider.ANTHROPIC, MODEL)
    before = good.read_bytes()
    consent = prepare(bench, "anthropic", "--pending")
    result = record(bench, "anthropic", consent, "--pending")
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 3 * 3
    assert good.read_bytes() == before
    for key in KEYS:
        recording = load_recording(recording_path(bench, key, Provider.ANTHROPIC, MODEL))
        assert {r.outcome for r in recording.repetitions} == {RepetitionOutcome.RESPONSE}
    again = invoke(
        "prepare", "--cases", str(bench), "--provider", "anthropic", "--model", MODEL, "--pending"
    )
    assert "nada pendiente" in again.output


def test_record_pending_sin_nada_pendiente_lo_dice_y_no_envia(bench, monkeypatch):
    fake = pending_bench(bench, monkeypatch)
    consent = prepare(bench, "anthropic", "--pending")
    assert record(bench, "anthropic", consent, "--pending").exit_code == 0
    fake.requests.clear()
    result = record(bench, "anthropic", "0" * 64, "--pending")
    assert result.exit_code == 0, result.output
    assert "nada pendiente" in result.output
    assert fake.requests == []


def test_record_pending_rechaza_la_huella_completa(bench, monkeypatch):
    fake = pending_bench(bench, monkeypatch)
    good = recording_path(bench, "dev/bueno", Provider.ANTHROPIC, MODEL)
    before = good.read_bytes()
    result = record(bench, "anthropic", prepare(bench, "anthropic"), "--pending")
    assert result.exit_code == 1 and "No se ha enviado nada" in result.output
    assert fake.requests == [] and good.read_bytes() == before
    assert not recording_path(bench, "dev/sin-grabar", Provider.ANTHROPIC, MODEL).exists()
