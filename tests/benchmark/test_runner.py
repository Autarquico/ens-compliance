"""Runner del banco: `run`, `baseline` y `validate --recordings`. Sin red ni claves: las
grabaciones son sintéticas y cualquier cliente real hace fallar el test."""

import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ens_evidence.benchmark import runner as runner_module
from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.baseline import BASELINE_FILE
from ens_evidence.benchmark.build import Build
from ens_evidence.benchmark.cases import load_case
from ens_evidence.benchmark.model import (
    Recording,
    Repetition,
    RepetitionOutcome,
)
from ens_evidence.benchmark.mutations import build_case
from ens_evidence.benchmark.recordings import recording_path, write_recording
from ens_evidence.benchmark.runner import REQUIRED_PAIRS, Pair, ProfileCache
from ens_evidence.catalog import catalog_sha256
from ens_evidence.cli import app
from ens_evidence.preaudit import service
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload
from ens_evidence.preaudit.records import PREAUDITS_PREFIX

FIXTURES = Path(__file__).parent / "fixtures"
VERDICT_WORDS = re.compile(r"certific|garantiz|\bconforme\b", re.IGNORECASE)
NOTICE = (
    "Cobertura del banco, no garantía universal. Superar el banco no aprueba un expediente real."
)
AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CLAUDE, CODEX = REQUIRED_PAIRS
DESTINATION = {
    CLAUDE.provider: "claude-code 2.1.0, sesión del usuario; ejecutable claude (sha256 "
    "308831041ea4863c3f87d222c31f759411898c874a9006b4bd6c745858b8f3bd)",
    CODEX.provider: "codex 0.160.1, sesión del usuario; ejecutable codex (sha256 " + "0" * 64 + ")",
}
CORRECT = """format: ens-benchmark-case/1
id: correcto
partition: dev
profile: mini
kind: correct
"""
JUDGED = """format: ens-benchmark-case/1
id: juicio
partition: dev
profile: mini
kind: defective
defects:
  - id: normativa-incoherente
    layer: judgment
    measure: org.2
    target: {kind: evidence, alias: revision-anual}
    expected_criterion: coherencia-politica-evidencia
    requirement:
      measure: org.2
      clause: org.2.1
      quote: El uso correcto de equipos, servicios e instalaciones
    rationale: El acta de revisión no trata el uso de equipos que la normativa regula.
"""
EMPTY = {"findings": []}
cli = CliRunner()


# --- mundo -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    def refuse(provider, config):
        raise AssertionError(f"se ha construido un cliente real de {provider}")

    monkeypatch.setattr(service, "default_client", refuse)


class Memo:
    """Cada caso se construye una vez por módulo; cada ejecución usa una copia."""

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.inner = ProfileCache(scratch / "profiles")
        self.done: dict[str, Build] = {}

    def __call__(self, root: Path, case, destination: Path) -> Build:
        if case.key not in self.done:
            self.done[case.key] = self.inner(root, case, self.scratch / "memo" / case.id)
        return self.done[case.key].fork(destination)


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("bench") / "bench"
    shutil.copytree(FIXTURES / "profiles" / "mini", root / "profiles" / "mini")
    dev = root / "cases" / "dev"
    dev.mkdir(parents=True)
    for name in ("inventario-caducado", "inventario-alterado"):
        shutil.copy(FIXTURES / "cases" / "dev" / f"{name}.yaml", dev)
    (dev / "correcto.yaml").write_text(CORRECT)
    (dev / "juicio.yaml").write_text(JUDGED)
    return root


@pytest.fixture(scope="module")
def memo(tmp_path_factory, template) -> Memo:
    memo = Memo(tmp_path_factory.mktemp("memo"))
    for path in sorted((template / "cases" / "dev").glob("*.yaml")):
        memo(template, load_case(path, template), memo.scratch / "warm" / path.stem)
    return memo


class Integrity:
    """`service.dossier_problems` (unos 10 s por llamada) recordado por el contenido del
    workspace, sin la cadena de pre-auditorías: dos árboles iguales dan lo mismo."""

    def __init__(self) -> None:
        self.real = service.dossier_problems
        self.seen: dict[tuple[str, str], list[str]] = {}

    def __call__(self, store, dossier: str) -> list[str]:
        root = store._root
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            key = path.relative_to(root).as_posix()
            if path.is_file() and not key.startswith(PREAUDITS_PREFIX):
                digest.update(key.encode() + hashlib.sha256(path.read_bytes()).digest())
        seen = (dossier, digest.hexdigest())
        if seen not in self.seen:
            self.seen[seen] = self.real(store, dossier)
        return list(self.seen[seen])


@pytest.fixture(scope="module")
def integrity() -> Integrity:
    return Integrity()


@pytest.fixture
def bench(tmp_path, template, memo, integrity, monkeypatch) -> Path:
    root = tmp_path / "bench"
    shutil.copytree(template, root)
    monkeypatch.setattr(runner_module, "ProfileCache", lambda scratch: memo)
    monkeypatch.setattr(service, "dossier_problems", integrity)
    return root


def payload_of(memo: Memo, key: str) -> dict:
    build = memo.done[key]
    assert build.dossier is not None
    return build_payload(build.store, build.dossier, load_pack()).data


def detecting(memo: Memo) -> dict:
    """Un hallazgo de juicio válido sobre la revisión anual de org.2 del caso `juicio`."""
    data = payload_of(memo, "dev/juicio")["dossier"]
    measure = next(m for m in data["measures"] if m["id"] == "org.2")
    evidence = memo.done["dev/juicio"].evidence_ids["revision-anual"]
    assert evidence in measure["evidence"]
    texts = {e["ref"]: e["text"] for e in data["evidence"]} | {
        d["ref"]: d["text"] for d in data["documents"]
    }

    def cite(ref: str) -> dict:
        line = next(line for line in texts[ref].splitlines() if len(line.split()) >= 8)
        return {"ref": ref, "quote": line[:100].rsplit(" ", 1)[0].strip()}

    return {
        "findings": [
            {
                "criterion": "coherencia-politica-evidencia",
                "measure": "org.2",
                "proposal": "OBSERVACION",
                "documents": [cite(measure["documents"][0])],
                "evidence": [cite(evidence)],
                "justifications": [],
                "reason": "El acta de revisión no trata el uso de equipos que regula la normativa.",
            }
        ]
    }


def record(root: Path, memo: Memo, key: str, pair: Pair, replies: list[dict], **changes) -> Path:
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
    ).model_copy(update=changes)
    return write_recording(root, recording, replace=True)


def record_all(root: Path, memo: Memo, pairs=REQUIRED_PAIRS, judged: list[dict] | None = None):
    for pair in pairs:
        record(root, memo, "dev/correcto", pair, [EMPTY] * 3)
        record(root, memo, "dev/inventario-caducado", pair, [EMPTY] * 3)
        record(root, memo, "dev/juicio", pair, judged or [detecting(memo)] * 3)


def run(root: Path, *args: str):
    return cli.invoke(app, ["benchmark", "run", "--cases", str(root), *args])


# --- códigos de salida e informe --------------------------------------------------------------


def test_la_puerta_threshold_en_desarrollo_es_un_error_de_uso_sin_repetir_nada(bench):
    result = run(bench, "--all-recorded", "--gate", "threshold")

    assert result.exit_code == 2, result.output
    assert "la puerta threshold es de la partición reservada" in result.output
    assert "falta la grabación" not in result.output


TARGET_NOTE = "objetivo de ajuste sobre la partición de desarrollo"


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (
            ("--partition", "holdout", "--all-recorded", "--gate", "target"),
            "la puerta target es de la partición de desarrollo",
        ),
        (
            ("--provider", CLAUDE.provider.value, "--model", CLAUDE.model, "--gate", "target"),
            "la puerta target exige --all-recorded",
        ),
    ],
    ids=["reservada", "un-par"],
)
def test_la_puerta_target_fuera_de_su_uso_es_un_error_sin_repetir_nada(bench, args, error):
    result = run(bench, *args)

    assert result.exit_code == 2, result.output
    assert error in result.output
    assert not (bench / MEASUREMENTS_FILE).exists()


def test_la_puerta_target_pasa_en_desarrollo_como_objetivo_de_ajuste(bench, memo):
    record_all(bench, memo)

    result = run(bench, "--all-recorded", "--gate", "target")

    assert result.exit_code == 0, result.output
    assert "resultado: la puerta target pasa" in result.output
    assert result.output.count(TARGET_NOTE) == 2
    assert "no cierra v0.1" in result.output
    assert "puerta target: pasa\n" in result.output
    assert not VERDICT_WORDS.search(result.output), result.output
    assert not (bench / MEASUREMENTS_FILE).exists()


def test_la_puerta_target_falla_si_un_par_de_la_puerta_no_llega_al_80_de_juicio(bench, memo):
    record_all(bench, memo)
    record(bench, memo, "dev/juicio", CODEX, [EMPTY] * 3)

    out = bench / "target.json"
    result = run(bench, "--all-recorded", "--gate", "target", "--format", "json", "--out", str(out))

    assert result.exit_code == 1, result.output
    data = json.loads(out.read_text())
    assert data["gate"] == "target" and data["gate_passed"] is False
    by_pair = {f"{r['provider']}/{r['model']}": r for r in data["results"]}
    assert by_pair[str(CLAUDE)]["gate_passed"] is True
    assert by_pair[str(CODEX)]["gate_problems"] == ["detección de juicio 0.0 % (mínimo 80 %)"]


def test_todo_grabado_y_detectado_sale_con_0_con_los_avisos(bench, memo):
    record_all(bench, memo)

    result = run(bench, "--all-recorded")

    assert result.exit_code == 0, result.output
    assert NOTICE in result.output
    assert "cubre solo las reglas incluidas en el banco" in result.output
    assert "DOSSIER_INTEGRITY queda fuera" in result.output
    assert "EVIDENCE_EXPIRED, HASH_MISMATCH" in result.output
    assert "no aplica: JudgmentConfig no expone temperatura" in result.output
    assert "resultado: sin puerta" in result.output
    assert not VERDICT_WORDS.search(result.output), result.output


def test_el_informe_json_lleva_fuentes_timestamps_y_el_caso_solo_determinista(bench, memo):
    record_all(bench, memo)
    out = bench / "bench.json"

    result = run(bench, "--all-recorded", "--format", "json", "--out", str(out))

    assert result.exit_code == 0, result.output
    text = out.read_text()
    assert not VERDICT_WORDS.search(text)
    data = json.loads(text)
    assert data["mode"] == "replay" and data["notice"] == NOTICE
    assert data["pack_sha256"] == load_pack().sha256 and data["catalog_sha256"]
    assert data["cases_sha256"] and data["run_at"] and data["gate"] is None
    assert [(r["provider"], r["model"]) for r in data["results"]] == [
        (p.provider.value, p.model) for p in sorted(REQUIRED_PAIRS)
    ]
    first = data["results"][0]
    assert first["temperature"] is None and "no aplica" in first["temperature_note"]
    assert "/" not in first["destination"].split("ejecutable")[1].split("(")[0]
    assert first["recorded_by"] == ["Ana Prueba"] and first["recorded_at"]
    assert first["recordings_sha256"]
    cases = {c["case"]: c for c in first["cases"]}
    tampered = cases["dev/inventario-alterado"]
    assert tampered["judgment"] is False and tampered["payload_sha256"] is None
    assert [r["outcome"] for r in tampered["repetitions"]] == [None] * 3
    assert tampered["defects"][0]["detected"] is True
    judged = cases["dev/juicio"]["defects"][0]
    assert judged["detected_by_repetition"] == [True] * 3
    totals = first["totals"]
    assert totals["deterministic"] == {"numerator": 2, "denominator": 2, "value": 1.0}
    assert totals["judgment"]["value"] == 1.0
    assert totals["false_alarms"]["numerator"] == 0


def test_falta_un_par_requerido_sale_con_3(bench, memo):
    record_all(bench, memo, pairs=[CLAUDE])

    result = run(bench, "--all-recorded", "--gate", "baseline")

    assert result.exit_code == 3, result.output
    assert f"falta la grabación de {CODEX}" in result.output


def test_un_caso_de_juicio_sin_grabacion_sale_con_3(bench, memo):
    record_all(bench, memo)
    recording_path(bench, "dev/correcto", CODEX.provider, CODEX.model).unlink()

    result = run(bench, "--all-recorded")

    assert result.exit_code == 3, result.output
    assert f"dev/correcto: no hay grabación de {CODEX}" in result.output


def test_una_grabacion_desligada_sale_con_3_en_run_y_en_validate(bench, memo):
    record_all(bench, memo)
    record(bench, memo, "dev/correcto", CLAUDE, [EMPTY] * 3, payload_sha256="b" * 64)

    result = run(bench, "--provider", CLAUDE.provider.value, "--model", CLAUDE.model)
    assert result.exit_code == 3, result.output
    assert "regraba" in result.output

    checked = cli.invoke(app, ["benchmark", "validate", "--cases", str(bench), "--recordings"])
    assert checked.exit_code == 3, checked.output
    assert "dev/correcto: la grabación de claude-code/claude-opus-5-5" in checked.output


def test_validate_recordings_acepta_las_grabaciones_ligadas_y_sus_destinos(bench, memo):
    record_all(bench, memo)

    result = cli.invoke(app, ["benchmark", "validate", "--cases", str(bench), "--recordings"])

    assert result.exit_code == 0, result.output
    assert "grabaciones ligadas: 6 en dev" in result.output


def test_validate_recordings_rechaza_con_2_una_grabacion_con_secreto(bench, memo):
    record_all(bench, memo)
    path = recording_path(bench, "dev/correcto", CLAUDE.provider, CLAUDE.model)
    data = json.loads(path.read_text())
    data["recorded_by"] = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    path.write_text(json.dumps(data))

    result = cli.invoke(app, ["benchmark", "validate", "--cases", str(bench), "--recordings"])

    assert result.exit_code == 2, result.output


def test_un_caso_invalido_sale_con_2(bench, memo):
    record_all(bench, memo)
    (bench / "cases" / "dev" / "roto.yaml").write_text("format: ens-benchmark-case/1\nid: roto\n")

    assert run(bench, "--all-recorded").exit_code == 2


def test_un_banco_vacio_sale_con_1(tmp_path):
    (tmp_path / "cases" / "dev").mkdir(parents=True)

    result = run(tmp_path, "--all-recorded", "--gate", "baseline")

    assert result.exit_code == 1, result.output
    assert "vacío" in result.output


def test_las_opciones_de_par_se_excluyen(bench):
    assert run(bench).exit_code == 2
    assert run(bench, "--all-recorded", "--provider", "codex", "--model", "x").exit_code == 2
    assert run(bench, "--provider", "codex").exit_code == 2


def test_la_reservada_exige_measurement_y_lo_anota(tmp_path):
    (tmp_path / "cases" / "holdout").mkdir(parents=True)

    locked = run(tmp_path, "--partition", "holdout", "--all-recorded")
    assert locked.exit_code == 2 and not (tmp_path / MEASUREMENTS_FILE).exists()

    opened = run(tmp_path, "--partition", "holdout", "--all-recorded", "--measurement")
    assert opened.exit_code == 1, opened.output
    ledger = (tmp_path / MEASUREMENTS_FILE).read_text().splitlines()
    assert [json.loads(line)["action"] for line in ledger] == ["run"]


# --- línea base ------------------------------------------------------------------------------


def baseline(root: Path, *args: str):
    return cli.invoke(app, ["benchmark", "baseline", "--cases", str(root), *args])


def test_baseline_escribe_con_diff_y_la_puerta_pasa_contra_ella(bench, memo):
    record_all(bench, memo)

    dry = baseline(bench)
    assert dry.exit_code == 0, dry.output
    assert "inventario, altas:" in dry.output and not (bench / BASELINE_FILE).exists()

    written = baseline(bench, "--write")
    assert written.exit_code == 0, written.output
    data = json.loads((bench / BASELINE_FILE).read_text())
    assert "dev/juicio#normativa-incoherente" in data["inventory"]
    assert {tuple(e["detected"]) for e in data["entries"]} == {
        (
            "dev/inventario-alterado#inventario-alterado",
            "dev/inventario-caducado#inventario-caducado",
            "dev/juicio#normativa-incoherente",
        )
    }

    again = run(bench, "--all-recorded", "--gate", "baseline")
    assert again.exit_code == 0, again.output
    assert f"línea base: {BASELINE_FILE}" in again.output
    assert "sin cambios" in baseline(bench).output


def test_perder_un_defecto_frente_a_la_linea_base_sale_con_1(bench, memo):
    record_all(bench, memo)
    assert baseline(bench, "--write").exit_code == 0
    record(bench, memo, "dev/juicio", CODEX, [EMPTY] * 3)

    result = run(bench, "--all-recorded", "--gate", "baseline")

    assert result.exit_code == 1, result.output
    assert "dev/juicio#normativa-incoherente se detectaba y ya no" in result.output


def test_retirar_un_caso_exige_anotarlo(bench, memo):
    record_all(bench, memo)
    assert baseline(bench, "--write").exit_code == 0
    reference = bench / "base.json"
    shutil.copy(bench / BASELINE_FILE, reference)
    (bench / "cases" / "dev" / "inventario-caducado.yaml").unlink()
    shutil.rmtree(bench / "recordings" / "dev" / "inventario-caducado")

    unannotated = run(bench, "--all-recorded", "--gate", "baseline", "--baseline", str(reference))
    assert unannotated.exit_code == 1, unannotated.output
    assert "dev/inventario-caducado sale del inventario sin una entrada `retired`" in (
        unannotated.output
    )

    [sha] = re.findall(
        r"dev/inventario-caducado sale .*sha256 en la línea base: ([0-9a-f]{64})",
        unannotated.output,
    )
    annotated = json.loads((bench / BASELINE_FILE).read_text())
    annotated["retired"] = [
        {"id": "dev/inventario-caducado", "sha256": sha, "reason": "duplica otro caso"}
    ]
    (bench / BASELINE_FILE).write_text(json.dumps(annotated))
    result = run(bench, "--all-recorded", "--gate", "baseline", "--baseline", str(reference))
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "changed",
    ["profile.yaml", "files/evidence"],
    ids=["profile-yaml", "fichero-del-perfil"],
)
def test_cambiar_el_perfil_cambia_la_huella_de_sus_casos_y_exige_retirarlos(bench, memo, changed):
    record_all(bench, memo)
    assert baseline(bench, "--write").exit_code == 0
    profile = bench / "profiles" / "mini"
    target = profile / changed
    if target.is_dir():
        target = next(p for p in sorted(target.rglob("*")) if p.is_file())
    target.write_bytes(target.read_bytes() + b"\n# cambio del perfil\n")

    unannotated = run(bench, "--all-recorded", "--gate", "baseline")
    assert unannotated.exit_code == 1, unannotated.output
    cases = sorted(p.stem for p in (bench / "cases" / "dev").glob("*.yaml"))
    shas = dict(
        re.findall(
            r"(dev/[a-z-]+) cambia de huella sin una entrada `retired` "
            r"\(sha256 en la línea base: ([0-9a-f]{64})\)",
            unannotated.output,
        )
    )
    assert sorted(shas) == [f"dev/{c}" for c in cases], unannotated.output
    assert "#" not in unannotated.output.split("problemas generales:")[1].split("resultado:")[0]

    annotated = json.loads((bench / BASELINE_FILE).read_text())
    annotated["retired"] = [
        {"id": key, "sha256": sha, "reason": "cambia el perfil mini"} for key, sha in shas.items()
    ]
    (bench / BASELINE_FILE).write_text(json.dumps(annotated))
    result = run(bench, "--all-recorded", "--gate", "baseline")
    assert result.exit_code == 0, result.output


def test_un_caso_nuevo_que_el_pack_no_detecta_no_es_regresion(bench, memo):
    record_all(bench, memo)
    assert baseline(bench, "--write").exit_code == 0
    path = bench / "cases" / "dev" / "juicio-bis.yaml"
    path.write_text(JUDGED.replace("id: juicio\n", "id: juicio-bis\n", 1))
    memo(bench, load_case(path, bench), memo.scratch / "warm" / "juicio-bis")
    for pair in REQUIRED_PAIRS:
        record(bench, memo, "dev/juicio-bis", pair, [EMPTY] * 3)

    result = run(bench, "--all-recorded", "--gate", "baseline")

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "pairs",
    [("--all-recorded",), ("--provider", CLAUDE.provider.value, "--model", CLAUDE.model)],
    ids=["all-recorded", "provider"],
)
def test_una_grabacion_mal_formada_sale_con_2_por_las_dos_vias(bench, memo, pairs):
    record_all(bench, memo)
    recording_path(bench, "dev/correcto", CLAUDE.provider, CLAUDE.model).write_text("{roto")

    result = run(bench, *pairs)

    assert result.exit_code == 2, result.output


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


def test_baseline_ref_lee_la_de_la_rama_base_o_vacia_si_no_existe(bench, memo):
    record_all(bench, memo)
    git(bench, "init", "-q")
    git(bench, "commit", "-q", "--allow-empty", "-m", "sin línea base")
    empty = run(bench, "--all-recorded", "--gate", "baseline", "--baseline-ref", "HEAD")
    assert empty.exit_code == 0, empty.output
    assert "(no existe: vacía)" in empty.output

    assert baseline(bench, "--write").exit_code == 0
    git(bench, "add", BASELINE_FILE)
    git(bench, "commit", "-q", "-m", "línea base")
    record(bench, memo, "dev/juicio", CLAUDE, [EMPTY] * 3)
    lost = run(bench, "--all-recorded", "--gate", "baseline", "--baseline-ref", "HEAD")
    assert lost.exit_code == 1, lost.output
    assert f"HEAD:{BASELINE_FILE}" in lost.output

    assert (
        run(bench, "--all-recorded", "--gate", "baseline", "--baseline-ref", "nada").exit_code == 2
    )


# --- caché -----------------------------------------------------------------------------------


def test_la_cache_del_perfil_da_el_mismo_caso_que_build_case(tmp_path, template):
    case = load_case(template / "cases" / "dev" / "juicio.yaml", template)
    cache = ProfileCache(tmp_path / "profiles")
    cache(
        template, load_case(template / "cases" / "dev" / "correcto.yaml", template), tmp_path / "c"
    )

    cached = cache(template, case, tmp_path / "cached")
    direct = build_case(template, case, tmp_path / "direct")

    assert cached.dossier == direct.dossier and cached.evidence_ids == direct.evidence_ids
    pack = load_pack()
    assert cached.dossier is not None and direct.dossier is not None
    assert (
        build_payload(cached.store, cached.dossier, pack).sha256
        == build_payload(direct.store, direct.dossier, pack).sha256
    )
