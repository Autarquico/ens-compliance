import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from ens_evidence.benchmark.access import MEASUREMENTS_FILE
from ens_evidence.benchmark.cases import (
    BenchError,
    load_case,
    load_recording,
    recording_problems,
    requirement_problem,
    validate_bench,
)
from ens_evidence.benchmark.fictional import value_problems
from ens_evidence.benchmark.model import Case, Partition, Recording
from ens_evidence.cli import app

runner = CliRunner()
SHA = "a" * 64
CLAUSE = "op.exp.1.1"
QUOTE = "identificando a su responsable"


def profile() -> dict[str, Any]:
    return {
        "format": "ens-benchmark-profile/1",
        "fictional": True,
        "id": "mini",
        "title": "Asesoría Ficticia",
        "clock": {"start": "2026-10-01T09:00:00+00:00"},
        "categorization": {d: "BAJO" for d in "CITAD"},
        "people": [{"id": "ana", "name": "Ana Prueba", "roles": ["security_officer"]}],
        "documents": [{"ref": "politica-seguridad", "approved_by": "ana"}],
        "evidence": [
            {
                "alias": "inventario",
                "file": "evidence/inventario.txt",
                "source": "manual",
                "links": [{"measure": "op.exp.1", "covers": ["base"]}],
            }
        ],
        "document_links": [
            {"measure": "org.1", "document": "politica-seguridad", "covers": ["base"]}
        ],
        "matrix": [
            {
                "measure": "op.exp.1",
                "owner": "ana",
                "review_every_days": 365,
                "reviewed_by": "ana",
                "assessment": "COMPLIANT",
            }
        ],
    }


def judgment_defect(**changes: Any) -> dict[str, Any]:
    defect = {
        "id": "sin-responsable",
        "layer": "judgment",
        "measure": "op.exp.1",
        "target": {"kind": "evidence", "alias": "inventario"},
        "expected_criterion": "coherencia-politica-evidencia",
        "requirement": {"measure": "op.exp.1", "clause": CLAUSE, "quote": QUOTE},
        "rationale": "El inventario no nombra a quien decide sobre cada equipo.",
    }
    return defect | changes


def case(**changes: Any) -> dict[str, Any]:
    data = {
        "format": "ens-benchmark-case/1",
        "id": "inventario-sin-responsable",
        "partition": "dev",
        "profile": "mini",
        "kind": "defective",
        "mutations": [
            {"op": "replace_evidence_text", "alias": "inventario", "file": "inventario-v2.txt"},
            {
                "op": "expire_evidence",
                "alias": "inventario",
                "valid_until": "2026-09-01T00:00:00+00:00",
            },
        ],
        "defects": [
            judgment_defect(),
            {
                "id": "caducada",
                "layer": "deterministic",
                "measure": "op.exp.1",
                "target": {"kind": "evidence", "alias": "inventario"},
                "expected_rule": "EVIDENCE_EXPIRED",
                "requirement": {
                    "measure": "op.exp.1",
                    "clause": CLAUSE,
                    "quote": "Se mantendrá un inventario actualizado",
                },
                "rationale": "La evidencia del inventario ha caducado.",
            },
        ],
    }
    return data | changes


def write_yaml(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def bench(root: Path, *, case_data: dict[str, Any] | None = None, **texts: str) -> Path:
    write_yaml(root / "profiles/mini/profile.yaml", profile())
    files = {
        "profiles/mini/files/evidence/inventario.txt": (
            "Inventario de equipos. Responsable: Ana Prueba (ana@asesoria.example), "
            "servidor 192.0.2.10."
        ),
        "cases/dev/files/inventario-sin-responsable/inventario-v2.txt": "Inventario de equipos.",
        **texts,
    }
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    data = case_data or case()
    write_yaml(root / f"cases/{data['partition']}/{data['id']}.yaml", data)
    return root


def test_un_banco_correcto_no_tiene_problemas_y_validate_sale_con_0(tmp_path):
    root = bench(tmp_path)
    assert validate_bench(root, Partition.DEV) == []
    result = runner.invoke(app, ["benchmark", "validate", "--cases", str(root)])
    assert result.exit_code == 0, result.output
    assert "1 casos en dev" in result.output


@pytest.mark.parametrize(
    "quote",
    [
        "identificando a su propietario",  # inventada
        "detallando su tipo e identificando al responsable",  # parafraseada
        "Identificando a su responsable",  # mayúsculas: no es literal
    ],
)
def test_una_cita_no_literal_se_rechaza_con_el_id_de_la_clausula(tmp_path, quote):
    defect = judgment_defect(requirement={"measure": "op.exp.1", "clause": CLAUSE, "quote": quote})
    root = bench(tmp_path, case_data=case(defects=[defect]))
    problems = validate_bench(root, Partition.DEV)
    assert problems == [
        "dev/inventario-sin-responsable: defecto sin-responsable: "
        f"la cita no es literal del texto de {CLAUSE}"
    ]
    result = runner.invoke(app, ["benchmark", "validate", "--cases", str(root)])
    assert result.exit_code == 2
    assert CLAUSE in result.output


def test_la_cita_solo_normaliza_los_espacios():
    assert requirement_problem("op.exp.1", CLAUSE, "identificando  a su\n  responsable") is None


def test_medida_y_clausula_deben_existir_en_el_catalogo():
    assert requirement_problem("op.exp.99", "op.exp.99.1", QUOTE) == (
        "la medida op.exp.99 no existe en el catálogo"
    )
    assert requirement_problem("op.exp.1", "op.exp.1.7", QUOTE) == (
        "la cláusula op.exp.1.7 no existe en la medida op.exp.1"
    )


def test_los_parrafos_sin_etiqueta_se_citan_con_el_id_de_la_medida_o_del_refuerzo():
    assert requirement_problem("org.1", "org.1", "La política de seguridad") is None
    assert requirement_problem("op.acc.6", "op.acc.6.r8", "zona controlada") is None
    assert requirement_problem("org.1", "org.1", "Los objetivos o misión") is not None


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Contacto: ana.prueba@gmail.com", "un correo electrónico"),
        ("Web corporativa: https://asesoria-aurora.es/contacto", "un dominio no reservado"),
        ("Dominio del correo: asesoria-aurora.es", "un dominio no reservado"),
        ("DNI de la responsable: 12345678Z", "un DNI/NIE"),
        ("NIF de la empresa: B12345674", "un NIF de empresa"),
        ("Teléfono: 612 345 678", "un teléfono"),
    ],
)
def test_un_dato_real_en_las_evidencias_del_perfil_se_rechaza(tmp_path, text, kind):
    root = bench(tmp_path, **{"profiles/mini/files/evidence/inventario.txt": text})
    problems = validate_bench(root, Partition.DEV)
    assert problems
    assert all(
        p.startswith("profiles/mini/files/evidence/inventario.txt › texto: ") for p in problems
    )
    assert any(kind in p for p in problems)
    value = text.split(": ", 1)[1]
    assert all(value not in p for p in problems)


@pytest.mark.parametrize(
    "linked",
    [
        "profiles/mini/files/evidence/inventario.txt",
        "cases/dev/files/inventario-sin-responsable/inventario-v2.txt",
        "cases/dev/inventario-sin-responsable.yaml",
    ],
)
def test_un_enlace_simbolico_fuera_del_banco_se_rechaza(tmp_path, linked):
    root = bench(tmp_path / "bench")
    outside = tmp_path / "fuera"
    (root / linked).rename(outside)
    (root / linked).symlink_to(outside)
    problems = validate_bench(root, Partition.DEV)
    assert any(linked in p and "enlace simbólico" in p for p in problems), problems


def test_un_nif_en_el_caso_se_rechaza(tmp_path):
    defect = judgment_defect(rationale="El responsable 12345678Z no figura.")
    root = bench(tmp_path, case_data=case(defects=[defect]))
    [problem] = validate_bench(root, Partition.DEV)
    assert "inventario-sin-responsable.yaml" in problem
    assert "un DNI/NIE" in problem


def test_dos_defectos_de_juicio_con_misma_medida_objetivo_y_criterio_se_rechazan(tmp_path):
    other = judgment_defect(id="otro", rationale="Otra forma de decir lo mismo.")
    root = bench(tmp_path, case_data=case(defects=[judgment_defect(), other]))
    [problem] = validate_bench(root, Partition.DEV)
    assert "misma medida, objetivo y regla o criterio" in problem
    with pytest.raises(BenchError):
        load_case(root / "cases/dev/inventario-sin-responsable.yaml")


@pytest.mark.parametrize("target", [{"kind": "evidence", "alias": "inventario"}, {"kind": "none"}])
def test_dos_defectos_deterministas_con_misma_medida_objetivo_y_regla_se_rechazan(target):
    expired = case()["defects"][1] | {"target": target}
    data = case(defects=[expired, expired | {"id": "otra-caducada"}])
    with pytest.raises(ValueError, match="misma medida, objetivo y regla o criterio"):
        Case.model_validate(data)


def test_con_otro_criterio_u_objetivo_no_es_ambiguo():
    other = judgment_defect(id="otro", expected_criterion="calidad-documental")
    Case.model_validate(case(defects=[judgment_defect(), other]))


def test_una_base_workspace_dentro_de_un_repo_git_se_rechaza(tmp_path):
    data = case(profile=None, workspace="../copia", mutations=[])
    root = bench(tmp_path, case_data=data)
    (root / ".git").mkdir()
    [problem] = validate_bench(root, Partition.DEV)
    assert "`workspace:`" in problem and "repo git" in problem


def test_una_base_workspace_fuera_de_un_repo_se_admite(tmp_path):
    root = bench(tmp_path, case_data=case(profile=None, workspace="../copia", mutations=[]))
    assert validate_bench(root, Partition.DEV) == []


def test_las_particiones_son_disjuntas(tmp_path):
    root = bench(tmp_path)
    write_yaml(root / "cases/holdout/inventario-sin-responsable.yaml", {"no": "se lee"})
    assert validate_bench(root, Partition.DEV) == [
        "caso inventario-sin-responsable: está en las dos particiones"
    ]


def test_validar_la_reservada_exige_measurement_y_lo_anota(tmp_path):
    data = case(partition="holdout")
    root = bench(
        tmp_path,
        case_data=data,
        **{"cases/holdout/files/inventario-sin-responsable/inventario-v2.txt": "Inventario."},
    )
    args = ["benchmark", "validate", "--cases", str(root), "--partition", "holdout"]
    locked = runner.invoke(app, args)
    assert locked.exit_code == 2
    assert "--measurement" in locked.output
    assert not (root / MEASUREMENTS_FILE).exists()

    opened = runner.invoke(app, [*args, "--measurement"])
    assert opened.exit_code == 0, opened.output
    [line] = (root / MEASUREMENTS_FILE).read_text().splitlines()
    assert json.loads(line) | {"at": None} == {
        "at": None,
        "action": "validate",
        "partition": "holdout",
    }


def test_la_particion_del_fichero_debe_coincidir_con_su_directorio(tmp_path):
    root = bench(tmp_path)
    write_yaml(root / "cases/dev/otro.yaml", case(id="otro", partition="holdout"))
    assert "la partición no coincide con su directorio" in "\n".join(
        validate_bench(root, Partition.DEV)
    )


def test_las_referencias_del_caso_deben_existir_en_el_perfil(tmp_path):
    defect = judgment_defect(target={"kind": "document", "ref": "plan-inexistente"})
    mutations = [{"op": "replace_evidence_text", "alias": "fantasma", "file": "falta.txt"}]
    root = bench(tmp_path, case_data=case(defects=[defect], mutations=mutations))
    problems = validate_bench(root, Partition.DEV)
    assert any("plan-inexistente no está en el perfil" in p for p in problems)
    assert any("fantasma no está en el perfil" in p for p in problems)
    assert any("falta el fichero falta.txt" in p for p in problems)


def test_criterio_desconocido_en_el_pack_se_rechaza(tmp_path):
    defect = judgment_defect(expected_criterion="criterio-inventado")
    root = bench(tmp_path, case_data=case(defects=[defect]))
    assert validate_bench(root, Partition.DEV) == [
        "dev/inventario-sin-responsable: defecto sin-responsable: criterio desconocido en el pack"
    ]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"mutations": [{"op": "borrar_todo", "alias": "x"}]}, "op"),
        (
            {"mutations": [{"op": "tamper_artifact", "alias": "x", "phase": "after_generate"}]},
            "phase",
        ),
        ({"mutations": [{"op": "tamper_artifact", "alias": "x"}]}, "judgment: false"),
        ({"judgment": False}, "solo determinista no lleva defectos de juicio"),
        ({"kind": "correct"}, "un caso correcto no lleva defectos"),
        ({"defects": []}, "al menos un defecto"),
        ({"workspace": "../copia"}, "solo de uno"),
        (
            {"defects": [judgment_defect(layer="deterministic", expected_criterion=None)]},
            "`expected_rule`",
        ),
        (
            {
                "defects": [
                    judgment_defect(
                        layer="deterministic",
                        expected_criterion=None,
                        expected_rule="DOSSIER_INTEGRITY",
                    )
                ]
            },
            "fuera del banco",
        ),
        (
            {"defects": [judgment_defect(expected_rule="CHECK_FAIL")]},
            "`expected_criterion` y no regla",
        ),
        (
            {"mutations": [{"op": "replace_evidence_text", "alias": "x", "file": "../fuera"}]},
            "ruta no relativa",
        ),
    ],
)
def test_el_modelo_de_caso_rechaza_formas_invalidas(changes, message):
    with pytest.raises(ValidationError, match=message):
        Case.model_validate(case(**changes))


def test_un_caso_solo_determinista_admite_tamper_artifact():
    deterministic = case()["defects"][1] | {"expected_rule": "HASH_MISMATCH"}
    Case.model_validate(
        case(
            judgment=False,
            mutations=[{"op": "tamper_artifact", "alias": "inventario"}],
            defects=[deterministic],
        )
    )


def recording(**changes: Any) -> dict[str, Any]:
    data = {
        "format": "ens-benchmark-recording/1",
        "case": "dev/inventario-sin-responsable",
        "case_sha256": SHA,
        "provider": "claude-code",
        "model": "claude-opus-5-5",
        "destination": "claude-code 2.1.0 (sha256 " + SHA + ")",
        "pack_version": "1.0.0",
        "pack_sha256": SHA,
        "payload_sha256": SHA,
        "tool_version": "0.1.0",
        "catalog_sha256": SHA,
        "recorded_at": "2026-11-04T10:00:00+00:00",
        "recorded_by": "Ana Prueba",
        "repetitions": [
            {
                "n": 1,
                "at": "2026-11-04T10:00:00+00:00",
                "outcome": "response",
                "response": json.dumps({"findings": [{"reason": "Falta el responsable."}]}),
            },
            {
                "n": 2,
                "at": "2026-11-04T10:01:00+00:00",
                "outcome": "provider_error",
                "kind": "TIMEOUT",
            },
            {"n": 3, "at": "2026-11-04T10:02:00+00:00", "outcome": "schema_error"},
        ],
    }
    return data | changes


def test_una_grabacion_limpia_no_tiene_problemas():
    assert recording_problems(Recording.model_validate(recording())) == []


def test_un_secreto_en_escape_unicode_dentro_de_la_respuesta_se_detecta():
    escaped = '{"findings": [{"reason": "token \\u0073k-ABCDEFGHIJKLMNOPQRSTUVWX"}]}'
    assert "sk-" not in escaped
    data = recording()
    data["repetitions"][0]["response"] = escaped
    problems = recording_problems(Recording.model_validate(data))
    assert problems
    assert all(p.startswith("repetitions[1].response") for p in problems)
    assert all("ABCDEFGHIJKLMNOPQRSTUVWX" not in p for p in problems)


def test_un_secreto_en_escape_unicode_en_los_metadatos_del_fichero_se_detecta(tmp_path):
    raw = json.dumps(recording(recorded_by="Ana sk-ABCDEFGHIJKLMNOPQRSTUVWX"))
    escaped = raw.replace("sk-", "\\u0073k-")
    assert "sk-" not in escaped
    path = tmp_path / "claude-code--claude-opus-5-5.json"
    path.write_text(escaped, encoding="utf-8")
    problems = recording_problems(load_recording(path))
    assert any(p.startswith("recorded_by") for p in problems)


def test_un_json_anidado_en_un_texto_tambien_se_escanea():
    nested = json.dumps({"quote": "escribe a ana@gmail.com"})
    assert "reason<json>.quote: contiene un correo electrónico" in value_problems(
        {"reason": nested}
    )


@pytest.mark.parametrize(
    "destination", ["claude-code /usr/local/bin/claude", "codex C:\\tools\\codex.exe", "x ~/bin/c"]
)
def test_el_destino_de_una_grabacion_no_lleva_rutas_absolutas(destination):
    with pytest.raises(ValidationError, match="ruta absoluta"):
        Recording.model_validate(recording(destination=destination))


def test_las_repeticiones_se_numeran_en_orden():
    data = recording()
    data["repetitions"] = data["repetitions"][1:]
    with pytest.raises(ValidationError, match="1, 2, 3"):
        Recording.model_validate(data)
