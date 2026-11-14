"""Mutaciones de un caso: cada una siembra su defecto por la API del producto, antes del
expediente, y deja el hallazgo determinista o el cambio de payload que se espera."""

import hashlib
import shutil
from pathlib import Path
from typing import get_args

import pytest
from typer.testing import CliRunner

from ens_evidence.benchmark.build import BASE_STEPS, Build, build_bench_profile, inspect
from ens_evidence.benchmark.cases import BenchError, case_files_dir, load_case, validate_bench
from ens_evidence.benchmark.model import Case, Mutation, Partition
from ens_evidence.benchmark.mutations import (
    OPERATIONS,
    apply_mutations,
    build_case,
    finish_case,
    resolve_targets,
)
from ens_evidence.cli import app
from ens_evidence.models.preaudit import Ref, RefKind
from ens_evidence.orgdocs import all_statuses
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import PayloadError, build_payload

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Build:
    """El perfil mini sin expediente, construido una vez; cada test muta su propia copia."""
    destination = tmp_path_factory.mktemp("base") / "ws"
    return build_bench_profile(
        FIXTURES, Partition.DEV, "mini", destination, seed="dev/base", steps=BASE_STEPS
    )


def fixture_case(name: str) -> Case:
    return load_case(FIXTURES / "cases" / "dev" / f"{name}.yaml", FIXTURES)


def inline_case(*mutations: dict, **changes) -> Case:
    return Case.model_validate(
        {
            "format": "ens-benchmark-case/1",
            "id": "en-linea",
            "partition": "dev",
            "profile": "mini",
            "kind": "correct",
            "mutations": list(mutations),
        }
        | changes
    )


def mutate(base: Build, tmp_path: Path, case: Case) -> Build:
    return finish_case(base.fork(tmp_path / "ws"), case, case_files_dir(FIXTURES, case))


def findings(build: Build) -> list[tuple[str, str, list[str]]]:
    return [(f.criterion, f.measure, [r.id for r in f.refs]) for f in inspect(build).findings]


def payload(build: Build) -> dict:
    assert build.dossier is not None
    return build_payload(build.store, build.dossier, load_pack()).data["dossier"]


def measure(content: dict, measure_id: str) -> dict:
    return next(m for m in content["measures"] if m["id"] == measure_id)


def test_los_casos_de_las_fixtures_son_validos():
    assert validate_bench(FIXTURES, Partition.DEV) == []


def test_cada_operacion_de_la_lista_cerrada_tiene_su_mutacion():
    (union, _) = get_args(Mutation)
    assert set(OPERATIONS) == set(get_args(union))


def test_expire_evidence_da_evidence_expired_sobre_el_id_real(base, tmp_path):
    case = fixture_case("inventario-caducado")
    build = mutate(base, tmp_path, case)

    target = resolve_targets(build, case)["inventario-caducado"]

    assert target is not None and target.kind is RefKind.EVIDENCE
    assert target.id != base.evidence_ids["inventario"]
    assert findings(build) == [
        ("EVIDENCE_EXPIRED", "op.exp.1", [f"7:evidence_expired:{target.id}", target.id])
    ]
    evidence = next(e for e in payload(build)["evidence"] if e["ref"] == target.id)
    assert evidence["valid_until"] == "2026-09-01T00:00:00+00:00"


def test_tamper_artifact_da_hash_mismatch_y_el_caso_es_solo_determinista(base, tmp_path):
    case = fixture_case("inventario-alterado")
    build = mutate(base, tmp_path, case)
    inventory = base.evidence_ids["inventario"]

    inspection = inspect(build)

    assert not case.judgment and inspection.payload_sha256 is None
    assert findings(build) == [
        ("HASH_MISMATCH", "op.exp.1", [f"7:evidence_unverified:{inventory}", inventory])
    ]
    assert resolve_targets(build, case) == {
        "inventario-alterado": Ref(kind=RefKind.EVIDENCE, id=inventory)
    }
    with pytest.raises(PayloadError):  # por eso no se prepara para juicio
        payload(build)


def test_replace_evidence_text_cambia_el_texto_sin_hallazgos_deterministas(base, tmp_path):
    case = fixture_case("inventario-sustituido")
    build = mutate(base, tmp_path, case)

    target = resolve_targets(build, case)["inventario-incompleto"]
    content = payload(build)

    assert findings(build) == []
    assert target is not None and target.id in measure(content, "op.exp.1")["evidence"]
    assert base.evidence_ids["inventario"] not in measure(content, "op.exp.1")["evidence"]
    texts = {e["ref"]: e["text"] for e in content["evidence"]}
    assert "versión de 2024" in texts[target.id]
    assert all("revisado el 30 de septiembre" not in text for text in texts.values())


@pytest.mark.parametrize(
    ("name", "rule"),
    [("escaneo-desactivado", "CHECK_FAIL"), ("escaneo-sin-estado", "CHECK_UNKNOWN")],
)
def test_collected_override_hace_fallar_o_desconocer_el_check(base, tmp_path, name, rule):
    build = mutate(base, tmp_path, fixture_case(name))

    tag = {"CHECK_FAIL": "check_failed", "CHECK_UNKNOWN": "check_unknown_subject"}[rule]
    assert findings(build) == [
        (rule, "op.exp.1", [f"7:{tag}:github.secret_scanning_enabled:nominas"])
    ]
    assert build.evidence_ids["github"] != base.evidence_ids["github"]


def test_unlink_evidence_deja_la_medida_sin_evidencia(base, tmp_path):
    case = inline_case({"op": "unlink_evidence", "alias": "revision-anual", "measure": "mp.if.1"})
    build = mutate(base, tmp_path, case)

    found = findings(build)

    assert {(rule, measure_id) for rule, measure_id, _ in found} == {
        ("EVIDENCE_MISSING", "mp.if.1")
    }
    assert ["7:clause:mp.if.1.1"] in [refs for _, _, refs in found]
    assert measure(payload(build), "mp.if.1")["evidence"] == []


POLICY_LINK = "link:org.1:document:politica-seguridad"


def test_unlink_document_quita_el_documento_y_con_la_evidencia_falta_cobertura(base, tmp_path):
    only_document = {"op": "unlink_document", "ref": "politica-seguridad", "measure": "org.1"}
    build = mutate(base, tmp_path / "a", inline_case(only_document))
    assert findings(build) == []  # el acta anual sigue cubriendo org.1
    link = next(x for x in payload(build)["links"] if x["id"] == POLICY_LINK)
    assert link["via"] == ["template"]  # sin el enlace manual; la plantilla la sigue citando

    both = [{"op": "unlink_evidence", "alias": "revision-anual", "measure": "org.1"}, only_document]
    found = findings(mutate(base, tmp_path / "b", inline_case(*both)))
    assert {(rule, measure_id) for rule, measure_id, _ in found} == {("EVIDENCE_MISSING", "org.1")}


def test_set_answer_reconstruye_y_reaprueba_los_documentos(base, tmp_path):
    answer = "Nos reunimos cuando hace falta, sin acta."
    case = inline_case({"op": "set_answer", "key": "coordinacion", "value": answer})
    build = mutate(base, tmp_path, case)

    texts = {d["ref"]: d["text"] for d in payload(build)["documents"]}

    assert findings(build) == []
    assert any(answer in text for text in texts.values())
    statuses = all_statuses(build.store)
    assert len(statuses) == 6 and all(status.current for status in statuses)


def test_set_answer_rechaza_una_respuesta_anidada_que_no_existe(base, tmp_path):
    case = inline_case({"op": "set_answer", "key": "no_existe.campo", "value": "x"})

    with pytest.raises(BenchError) as excinfo:
        mutate(base, tmp_path, case)

    assert excinfo.value.problems == [
        "perfil mini › mutations[0] (set_answer): set_answer: la respuesta «no_existe» "
        "no existe en org/answers.yaml"
    ]


def test_declare_not_applicable_lleva_la_justificacion_al_payload(base, tmp_path):
    justification = "No dependemos de ningún proveedor para las nóminas."
    mutation = {"op": "declare_not_applicable", "measure": "op.cont.2"} | {
        "justification": justification
    }
    defect = {
        "id": "justificacion-debil",
        "layer": "judgment",
        "measure": "op.cont.2",
        "target": {"kind": "justification"},
        "expected_criterion": "solidez-justificacion-aplicabilidad",
        "requirement": {"measure": "op.cont.2", "clause": "op.cont.2", "quote": "x"},
        "rationale": "La justificación no se sostiene.",
    }
    case = inline_case(mutation, kind="defective", defects=[defect])
    build = mutate(base, tmp_path, case)

    items = {j["id"]: j for j in payload(build)["justifications"]}

    assert findings(build) == []
    assert items["justification:not_selected:op.cont.2"]["justification"] == justification
    assert measure(payload(build), "op.cont.2")["applicable"] is False
    assert resolve_targets(build, case) == {
        "justificacion-debil": Ref(kind=RefKind.SECTION, id="justification:not_selected:op.cont.2")
    }


def test_compensate_lleva_la_compensacion_al_payload(base, tmp_path):
    justification = "El inventario en papel firmado cada trimestre cubre el único servidor."
    case = inline_case(
        {
            "op": "compensate",
            "measure": "op.exp.1",
            "measures": ["Inventario en papel firmado cada trimestre"],
            "replaces": ["base"],
            "justification": justification,
        }
    )
    build = mutate(base, tmp_path, case)

    (item,) = [j for j in payload(build)["justifications"] if j["kind"] == "compensation"]

    assert item["measure"] == "op.exp.1" and item["justification"] == justification
    # Los enlaces cubrían `base`, que ya no es obligación: falta cubrir la compensación.
    assert {(rule, m) for rule, m, _ in findings(build)} == {("EVIDENCE_MISSING", "op.exp.1")}


def test_una_operacion_desconocida_se_rechaza_al_cargar_y_al_aplicar(base, tmp_path):
    path = tmp_path / "caso.yaml"
    path.write_text(
        "format: ens-benchmark-case/1\nid: caso\npartition: dev\nprofile: mini\nkind: correct\n"
        "mutations:\n  - op: borrar_todo\n",
        encoding="utf-8",
    )
    with pytest.raises(BenchError) as loading:
        load_case(path)
    assert any("borrar_todo" in problem for problem in loading.value.problems)

    with pytest.raises(BenchError) as applying:
        apply_mutations(base, [object()], tmp_path)  # type: ignore[list-item]
    assert "operación desconocida" in applying.value.problems[0]


def test_una_mutacion_que_el_producto_rechaza_falla_con_su_paso(base, tmp_path):
    case = inline_case({"op": "unlink_evidence", "alias": "inventario", "measure": "op.exp.2"})

    with pytest.raises(BenchError) as excinfo:
        mutate(base, tmp_path, case)

    assert excinfo.value.problems[0].startswith("perfil mini › mutations[0] (unlink_evidence): ")


def tree_digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_un_caso_mutado_se_reproduce_byte_a_byte_y_por_la_orden(tmp_path):
    case = fixture_case("inventario-caducado")
    built = build_case(FIXTURES, case, tmp_path / "a")
    expected = inspect(built)

    result = runner.invoke(
        app,
        ["benchmark", "build", case.key, "--cases", str(FIXTURES), "--out", str(tmp_path / "b")],
    )

    assert result.exit_code == 0, result.output
    assert f"expediente: {expected.dossier}" in result.output
    assert f"payload_sha256: {expected.payload_sha256}" in result.output
    assert "hallazgos deterministas: 1" in result.output
    assert "EVIDENCE_EXPIRED op.exp.1" in result.output
    assert tree_digest(tmp_path / "a") == tree_digest(tmp_path / "b")


def test_la_orden_de_un_caso_solo_determinista_no_construye_payload(tmp_path):
    result = runner.invoke(
        app, ["benchmark", "build", "dev/inventario-alterado", "--cases", str(FIXTURES)]
    )

    assert result.exit_code == 0, result.output
    assert "payload_sha256: no aplica (caso solo determinista)" in result.output
    assert "HASH_MISMATCH op.exp.1" in result.output


def test_los_ficheros_de_un_caso_enlazados_fuera_del_banco_no_se_leen(tmp_path):
    root = tmp_path / "bench"
    shutil.copytree(FIXTURES, root)
    case = fixture_case("inventario-sustituido")
    files = case_files_dir(root, case)
    shutil.move(files, tmp_path / "fuera")
    files.symlink_to(tmp_path / "fuera", target_is_directory=True)

    with pytest.raises(BenchError, match="enlace simbólico o queda fuera del banco"):
        build_case(root, case, tmp_path / "ws")
    assert any("enlace simbólico" in p for p in validate_bench(root, Partition.DEV))
