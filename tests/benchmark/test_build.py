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

from ens_evidence.applicability import DeclarationState, declaration_status
from ens_evidence.benchmark.build import build_bench_profile, seeded_ids
from ens_evidence.benchmark.cases import BenchError, validate_bench
from ens_evidence.benchmark.model import Partition
from ens_evidence.catalog.controls import load_measures
from ens_evidence.categorization import CategorizationState, categorization_status
from ens_evidence.evidence import is_evidence_id, list_manifests, verify_all
from ens_evidence.models.enums import Applicability
from ens_evidence.scope import ScopeState, scope_status

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = "mini"

BUILD_AND_DIGEST = """
import hashlib, json, sys
from pathlib import Path
from ens_evidence.benchmark.build import build_bench_profile

root, destination = Path(sys.argv[1]), Path(sys.argv[2])
build = build_bench_profile(root, "mini", destination)
files = {
    p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(destination.rglob("*")) if p.is_file()
}
print(json.dumps({"ids": build.evidence_ids, "files": files}))
"""


def test_el_perfil_mini_es_un_perfil_valido_del_banco():
    assert validate_bench(FIXTURES, Partition.DEV) == []


def test_el_perfil_mini_da_un_workspace_aprobado_con_evidencias_integras(tmp_path):
    build = build_bench_profile(FIXTURES, PROFILE, tmp_path / "ws")
    store = build.store

    assert scope_status(store).state is ScopeState.APPROVED
    assert categorization_status(store, build.now).state is CategorizationState.CURRENT
    declaration = declaration_status(store, build.now)
    assert declaration.state is DeclarationState.CURRENT, declaration.errors
    report = verify_all(store)
    assert report.ok, report.problems
    assert report.checked == 2


def test_las_decisiones_del_perfil_quedan_en_la_declaracion(tmp_path):
    build = build_bench_profile(FIXTURES, PROFILE, tmp_path / "ws")
    document = load_measures(build.store)
    assert document is not None
    measures = {m.id: m for m in document.measures}

    assert measures["op.cont.2"].applicability is Applicability.APPLICABLE
    assert not measures["op.cont.2"].proposed
    assert measures["op.acc.5"].chosen_reinforcements == ["R1"]
    assert measures["op.acc.5"].proposed  # sigue siendo la propuesta del producto
    assert all(m.applicability is not Applicability.UNDETERMINED for m in measures.values())


def test_cada_alias_tiene_el_id_real_de_su_evidencia(tmp_path):
    build = build_bench_profile(FIXTURES, PROFILE, tmp_path / "ws")
    manifests = {m.id: m for m in list_manifests(build.store).manifests}

    assert set(build.evidence_ids) == {"inventario", "github"}
    assert set(build.evidence_ids.values()) == set(manifests)
    assert all(is_evidence_id(i) for i in build.evidence_ids.values())
    assert manifests[build.evidence_ids["inventario"]].asset_id == "srv-nominas"
    assert manifests[build.evidence_ids["github"]].source == "github"


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
    assert any(key.startswith("evidence/manifests/") for key in first["files"])
    assert any(key.startswith("controls/declarations/") for key in first["files"])


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
    data["people"] = [p for p in data["people"] if "security_officer" not in p["roles"]]


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


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (_without_security_officer, "perfil mini › categorización: nadie tiene el rol"),
        (_without_default_justification, "perfil mini › aplicabilidad: medidas sin decidir"),
        (_other_level, "perfil mini › categorización: los niveles calculados"),
        (_manual_without_asset, "perfil mini › evidencias: inventario: una evidencia manual"),
        (_missing_reinforcement, "perfil mini › aplicabilidad: controls/measures.yaml"),
        (_missing_file, "perfil mini › evidencias: inventario: falta files/"),
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


def test_ningun_fichero_escrito_lleva_la_ruta_del_workspace(tmp_path):
    build_bench_profile(FIXTURES, PROFILE, tmp_path / "ws")
    for path in (tmp_path / "ws").rglob("*"):
        if path.is_file():
            assert str(tmp_path).encode() not in path.read_bytes(), path
