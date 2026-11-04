"""El builder del perfil base: un workspace real, aprobado y reproducible byte a byte."""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.applicability import DeclarationState, declaration_status
from ens_evidence.assessment.dossier.model import snapshot_export
from ens_evidence.assessment.dossier.store import load_snapshot
from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.build import Build, build_bench_profile, inspect, seeded_ids
from ens_evidence.benchmark.cases import BenchError, validate_bench
from ens_evidence.benchmark.model import Partition
from ens_evidence.catalog.controls import load_measures
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.cli import app
from ens_evidence.evidence import is_evidence_id, list_manifests, verify_all
from ens_evidence.models.enums import Applicability, Category
from ens_evidence.models.preaudit import Provider
from ens_evidence.orgdocs import all_statuses
from ens_evidence.preaudit import service
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.workspace import LocalStore
from ens_evidence.workspace.settings import PreAuditSettings

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = "mini"

BUILD_AND_DIGEST = """
import hashlib, json, sys
from pathlib import Path
from ens_evidence.benchmark.build import build_bench_profile

from ens_evidence.benchmark.build import inspect
from ens_evidence.models.preaudit import Provider
from ens_evidence.preaudit import service
from ens_evidence.workspace.settings import PreAuditSettings

root, destination = Path(sys.argv[1]), Path(sys.argv[2])
build = build_bench_profile(root, "mini", destination)
inspection = inspect(build)
settings = PreAuditSettings(provider=Provider.ANTHROPIC, model="modelo")
prepared = service.prepare(build.store, build.dossier, None, settings, now=build.tick())
files = {
    p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(destination.rglob("*")) if p.is_file()
}
print(json.dumps({
    "ids": build.evidence_ids,
    "dossier": build.dossier,
    "payload": inspection.payload_sha256,
    "prepared": prepared.payload.sha256,
    "files": files,
}))
"""
runner = CliRunner()


@pytest.fixture(scope="module")
def mini(tmp_path_factory) -> Build:
    """El perfil mini construido una vez por módulo; los tests que escriben usan su copia."""
    return build_bench_profile(FIXTURES, PROFILE, tmp_path_factory.mktemp("mini") / "ws")


SETTINGS = PreAuditSettings(provider=Provider.ANTHROPIC, model="modelo")


def test_el_perfil_mini_es_un_perfil_valido_del_banco():
    assert validate_bench(FIXTURES, Partition.DEV) == []


def test_el_perfil_mini_da_un_workspace_aprobado_con_evidencias_integras(mini):
    build = mini
    store = build.store

    assert scope_status(store).state is ScopeState.APPROVED
    assert categorization_status(store, build.now).state is CategorizationState.CURRENT
    declaration = declaration_status(store, build.now)
    assert declaration.state is DeclarationState.CURRENT, declaration.errors
    report = verify_all(store)
    assert report.ok, report.problems
    assert report.checked == len(list_manifests(store).manifests)


def test_las_decisiones_del_perfil_quedan_en_la_declaracion(mini):
    build = mini
    document = load_measures(build.store)
    assert document is not None
    measures = {m.id: m for m in document.measures}

    assert measures["op.cont.2"].applicability is Applicability.APPLICABLE
    assert not measures["op.cont.2"].proposed
    assert measures["op.acc.5"].chosen_reinforcements == ["R1"]
    assert measures["op.acc.5"].proposed  # sigue siendo la propuesta del producto
    assert all(m.applicability is not Applicability.UNDETERMINED for m in measures.values())


def test_cada_alias_tiene_el_id_real_de_su_evidencia(mini):
    build = mini
    manifests = {m.id: m for m in list_manifests(build.store).manifests}

    assert set(build.evidence_ids) == {"inventario", "github", "revision-anual", "simulacro"}
    assert set(build.evidence_ids.values()) <= set(manifests)
    assert all(is_evidence_id(i) for i in build.evidence_ids.values())
    assert manifests[build.evidence_ids["inventario"]].asset_id == "srv-nominas"
    assert manifests[build.evidence_ids["github"]].source == "github"
    assert manifests[build.evidence_ids["simulacro"]].source == "incident-history"


def test_dos_procesos_con_directorios_distintos_construyen_los_mismos_bytes(tmp_path):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    outputs = [
        subprocess.run(
            [sys.executable, "-c", BUILD_AND_DIGEST, str(FIXTURES), str(tmp_path / name)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout
        for name in ("primera", "segunda")
    ]
    first, second = (json.loads(output) for output in outputs)

    assert first == second
    assert first["prepared"] == first["payload"]
    assert any(key.startswith("evidence/manifests/") for key in first["files"])
    assert any(key.startswith("controls/declarations/") for key in first["files"])
    assert any(key.startswith("evidence/assessments/") for key in first["files"])
    assert any(key.startswith("evidence/preaudits/") for key in first["files"])


def test_la_semilla_cambia_los_ids_y_nada_mas_del_formato(tmp_path):
    first = build_bench_profile(FIXTURES, PROFILE, tmp_path / "a", seed="caso-a")
    second = build_bench_profile(FIXTURES, PROFILE, tmp_path / "b", seed="caso-b")

    assert first.evidence_ids.keys() == second.evidence_ids.keys()
    assert set(first.evidence_ids.values()).isdisjoint(second.evidence_ids.values())


def test_los_ids_sembrados_son_una_secuencia_reproducible():
    now = datetime(2026, 10, 1, 9, tzinfo=UTC)
    one, other = seeded_ids("x"), seeded_ids("x")
    sequence = [one(now) for _ in range(3)]

    assert sequence == [other(now) for _ in range(3)]
    assert len(set(sequence)) == 3
    assert all(is_evidence_id(i) for i in sequence)


def edited_bench(tmp_path: Path, edit) -> Path:
    root = tmp_path / "bench"
    shutil.copytree(FIXTURES, root)
    path = root / "profiles/mini/profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return root


def _without_security_officer(data):
    for person in data["people"]:
        person["roles"] = [r for r in person["roles"] if r != "security_officer"] or ["management"]


def _without_default_justification(data):
    del data["not_required_justification"]


def _other_level(data):
    data["categorization"]["C"] = "MEDIO"


def _manual_without_asset(data):
    del data["evidence"][0]["asset"]


def _missing_reinforcement(data):
    del data["reinforcements"]["op.acc.5"]


def _missing_file(data):
    data["evidence"][0]["file"] = "evidence/no-existe.txt"


def _document_signed_by_wrong_role(data):
    data["documents"][0]["approved_by"] = "bruno"


def _document_without_signer(data):
    data["documents"] = data["documents"][1:]
    data["document_links"] = [
        link for link in data["document_links"] if link["document"] != "politica-seguridad"
    ]


def _reviewer_without_self_assessor_role(data):
    data["matrix_defaults"]["reviewed_by"] = "ana"


def _without_matrix_defaults(data):
    del data["matrix_defaults"]


def _collected_link_without_check(data):
    del data["evidence"][1]["links"][0]["check"]


def _uncovered_reinforcement(data):
    links = data["evidence"][2]["links"]
    links[:] = [link for link in links if link["measure"] != "op.acc.6"]


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (_without_security_officer, "perfil mini › categorización: nadie tiene el rol"),
        (_without_default_justification, "perfil mini › aplicabilidad: medidas sin decidir"),
        (_other_level, "perfil mini › categorización: los niveles calculados"),
        (_manual_without_asset, "perfil mini › evidencias: inventario: una evidencia manual"),
        (_missing_reinforcement, "perfil mini › aplicabilidad: controls/measures.yaml"),
        (_missing_file, "perfil mini › evidencias: inventario: falta files/"),
        (_document_signed_by_wrong_role, "perfil mini › org-docs: politica-seguridad: lo aprueba"),
        (_document_without_signer, "perfil mini › org-docs: documentos sin firmante"),
        (_reviewer_without_self_assessor_role, "perfil mini › matriz: org.1: ana no revisa"),
        (_without_matrix_defaults, "perfil mini › matriz: org.1: sin fila en `matrix`"),
        (_collected_link_without_check, "perfil mini › matriz: github: un enlace de una recogida"),
        (_uncovered_reinforcement, "perfil mini › matriz: op.acc.6:"),
    ],
)
def test_un_perfil_que_no_se_puede_construir_falla_con_el_paso_y_el_motivo(
    tmp_path, edit, expected
):
    root = edited_bench(tmp_path, edit)
    with pytest.raises(BenchError) as raised:
        build_bench_profile(root, PROFILE, tmp_path / "ws")
    assert any(p.startswith(expected) for p in raised.value.problems), raised.value.problems


def test_no_construye_sobre_un_destino_con_contenido(tmp_path):
    destination = tmp_path / "ws"
    destination.mkdir()
    (destination / "algo.txt").write_text("previo", encoding="utf-8")
    with pytest.raises(BenchError, match="el destino no está vacío"):
        build_bench_profile(FIXTURES, PROFILE, destination)


def test_el_perfil_invalido_por_esquema_no_llega_a_construirse(tmp_path):
    root = edited_bench(tmp_path, lambda data: data.update(fictional=False))
    with pytest.raises(BenchError):
        build_bench_profile(root, PROFILE, tmp_path / "ws")
    assert not (tmp_path / "ws").exists()


def test_ningun_fichero_escrito_lleva_la_ruta_del_workspace(mini):
    root = mini.store._root
    for path in root.rglob("*"):
        if path.is_file():
            assert str(root.parent).encode() not in path.read_bytes(), path


def test_el_perfil_mini_da_un_expediente_basica_sin_hallazgos_deterministas(mini):
    build = mini
    inspection = inspect(build)
    snapshot = load_snapshot(build.store, inspection.dossier)

    assert snapshot.category is Category.BASICA
    assert snapshot_export(snapshot).blockers == []
    assert inspection.integrity == []
    assert inspection.findings == []


def test_los_seis_documentos_quedan_aprobados_y_vigentes(mini):
    build = mini
    statuses = all_statuses(build.store)

    assert len(statuses) == 6
    assert all(status.current for status in statuses), [s.name for s in statuses]


def test_prepare_acepta_el_expediente_y_da_la_huella_de_inspect(mini, tmp_path):
    inspection = inspect(mini)
    shutil.copytree(mini.store._root, tmp_path / "ws")
    store = LocalStore(tmp_path / "ws")

    prepared = service.prepare(store, mini.dossier, None, SETTINGS, now=mini.now)

    assert prepared.dossier == inspection.dossier
    assert prepared.payload.sha256 == inspection.payload_sha256


# --- orden `benchmark build` ---------------------------------------------------------------


def bench_with_case(tmp_path: Path, partition: str, **changes) -> Path:
    root = tmp_path / "bench"
    shutil.copytree(FIXTURES, root)
    case = {
        "format": "ens-benchmark-case/1",
        "id": "correcto",
        "partition": partition,
        "profile": PROFILE,
        "kind": "correct",
    } | changes
    directory = root / "cases" / partition
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{case['id']}.yaml").write_text(yaml.safe_dump(case), encoding="utf-8")
    return root


def bench(*args: str):
    return runner.invoke(app, ["benchmark", "build", *args])


def test_build_de_un_perfil_imprime_expediente_huella_y_cero_hallazgos(mini, tmp_path):
    expected = inspect(mini)

    result = bench(PROFILE, "--cases", str(FIXTURES), "--out", str(tmp_path / "ws"))

    assert result.exit_code == 0, result.output
    assert f"expediente: {expected.dossier}" in result.output
    assert f"payload_sha256: {expected.payload_sha256}" in result.output
    assert "hallazgos deterministas: 0" in result.output
    assert (tmp_path / "ws" / "evidence" / "assessments" / expected.dossier).is_dir()


def test_build_sin_out_construye_en_un_temporal(tmp_path):
    result = bench(PROFILE, "--cases", str(FIXTURES))

    assert result.exit_code == 0, result.output
    assert "hallazgos deterministas: 0" in result.output


def test_build_de_un_caso_siembra_los_ids_con_la_clave_del_caso(mini, tmp_path):
    root = bench_with_case(tmp_path, "dev")

    result = bench("dev/correcto", "--cases", str(root), "--out", str(tmp_path / "ws"))

    assert result.exit_code == 0, result.output
    assert "hallazgos deterministas: 0" in result.output
    assert f"expediente: {mini.dossier}" not in result.output


def test_la_reservada_exige_measurement_y_lo_anota(tmp_path):
    root = bench_with_case(tmp_path, "holdout")

    locked = bench("holdout/correcto", "--cases", str(root))
    assert locked.exit_code == 2
    assert "--measurement" in locked.output
    assert not (root / MEASUREMENTS_FILE).exists()

    measured = bench("holdout/correcto", "--cases", str(root), "--measurement")
    assert measured.exit_code == 0, measured.output
    entries = (root / MEASUREMENTS_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["action"] for line in entries] == ["build"]


def test_la_reservada_no_revela_si_un_caso_existe_sin_measurement(tmp_path):
    root = bench_with_case(tmp_path, "holdout")

    result = bench("holdout/no-existe", "--cases", str(root))

    assert result.exit_code == 2
    assert "no existe el caso" not in result.output
    assert "--measurement" in result.output


@pytest.mark.parametrize(
    ("target", "message"),
    [("otra/correcto", "partición desconocida"), ("dev/no-existe", "no existe el caso dev/")],
)
def test_build_rechaza_objetivos_que_no_existen(tmp_path, target, message):
    root = bench_with_case(tmp_path, "dev")

    result = bench(target, "--cases", str(root))

    assert result.exit_code == 2
    assert message in result.output


def test_un_perfil_que_no_se_construye_sale_con_2_y_el_motivo(tmp_path):
    root = edited_bench(tmp_path, _without_matrix_defaults)

    result = bench(PROFILE, "--cases", str(root))

    assert result.exit_code == 2
    assert "perfil mini › matriz:" in result.output
    assert "No se construye mini" in result.output
