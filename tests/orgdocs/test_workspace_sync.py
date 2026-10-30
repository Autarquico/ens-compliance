import shutil

import pytest

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import approve_document, build_documents
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.workspace import LocalStore

from .orgdocs_helpers import (
    APPROVED_AT,
    DOCUMENTS,
    EXAMPLE_SCOPE,
    SCOPE_FILES,
    ens,
    fixture_answers,
    snapshot,
    write_answers,
)

ORG = "org/"


def org_files(store, *, skip=()):
    return {
        k: v
        for k, v in snapshot(store).items()
        if k.startswith(ORG) and not k.startswith(tuple(skip))
    }


@pytest.fixture
def with_history(built):
    approve_document(
        built,
        "politica-seguridad",
        "Marta Ibáñez Soler",
        Role.MANAGEMENT,
        APPROVED_AT.replace(day=29),
    )
    answers = fixture_answers()
    answers["mision"] = "Una misión nueva y distinta."
    write_answers(built, answers)
    build_documents(built)
    assert built.list("org/docs/history/")
    return built


def test_pull_copia_respuestas_documentos_metas_e_historial(with_history, settings, tmp_path):
    directory = tmp_path / "draft"

    result = ens(settings, "workspace", "pull", str(directory))

    assert result.exit_code == 0, result.output
    pulled = LocalStore(directory)
    expected = org_files(with_history, skip=["org/docs/approvals/"])
    assert expected["org/answers.yaml"]
    assert {f"org/docs/{n}.md" for n in DOCUMENTS} <= set(expected)
    assert any(k.startswith("org/docs/history/") for k in expected)
    assert org_files(pulled) == expected


def test_pull_no_copia_los_registros_de_aprobacion_de_documentos(with_history, settings, tmp_path):
    directory = tmp_path / "draft"

    ens(settings, "workspace", "pull", str(directory))

    assert with_history.list("org/docs/approvals/")
    assert LocalStore(directory).list("org/docs/approvals/") == []


def test_push_publica_respuestas_documentos_metas_e_historial(with_history, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(EXAMPLE_SCOPE, draft / "scope")
    source = LocalStore(draft)
    for key, content in org_files(with_history, skip=["org/docs/approvals/"]).items():
        source.write_bytes(key, content)
    destination = LocalStore(tmp_path / "destination")
    settings = tmp_path / "destination-settings.yaml"
    settings.write_text(
        f"workspace:\n  backend: local\n  local:\n    path: {tmp_path / 'destination'}\n"
    )

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert org_files(destination) == org_files(source)
    assert any(k.startswith("org/docs/history/") for k in org_files(destination))


def test_push_de_documentos_e_historial_conserva_la_aprobacion_del_alcance(
    with_history, settings, tmp_path
):
    draft = tmp_path / "draft"
    ens(settings, "workspace", "pull", str(draft))
    path = draft / "org" / "docs" / "politica-seguridad.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nNota.\n", encoding="utf-8")
    for name in SCOPE_FILES:
        assert (draft / "scope" / f"{name}.yaml").exists()

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert with_history.read_text("org/docs/politica-seguridad.md").endswith("\nNota.\n")
    assert scope_status(with_history).state is ScopeState.APPROVED
