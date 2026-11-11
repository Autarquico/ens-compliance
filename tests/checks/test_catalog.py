import ast
import importlib
import re
from pathlib import Path

import pytest

from ens_evidence.checks import catalog
from ens_evidence.checks.catalog import CHECKS, KNOWN_CHECKS, SubjectKind
from ens_evidence.matrix import KNOWN_CHECKS as MATRIX_KNOWN_CHECKS


def _constants(module: str, pattern: str) -> set[str]:
    mod = importlib.import_module(module)
    return {
        value
        for name, value in vars(mod).items()
        if not name.startswith("_") and isinstance(value, str) and re.fullmatch(pattern, value)
    }


@pytest.mark.parametrize(
    ("module", "connector", "pattern"),
    [
        ("ens_evidence.connectors.github.checks", "github", r"github\.[a-z0-9_]+"),
        ("ens_evidence.connectors.cloudflare.checks", "cloudflare", r"cloudflare\.[a-z0-9_]+"),
        (
            "ens_evidence.connectors.google_workspace.checks",
            "google-workspace",
            r"workspace\.[a-z0-9_]+",
        ),
        ("ens_evidence.connectors.backups.checks", "backups", r"backups\.[a-z0-9_]+"),
        ("ens_evidence.connectors.keystore.checks", "keystore", r"keystore\.[a-z0-9_]+"),
        ("ens_evidence.connectors.passwords.checks", "passwords", r"passwords\.[a-z0-9_]+"),
        ("ens_evidence.connectors.restic.checks", "restic", r"restic\.[a-z0-9_]+"),
        ("ens_evidence.endpoint.checks", "endpoint", r"HOST-\d{3}"),
    ],
)
def test_el_catalogo_coincide_con_las_constantes_de_cada_modulo(module, connector, pattern):
    declared = {check for check, spec in CHECKS.items() if spec.connector == connector}

    assert declared == _constants(module, pattern)
    assert declared


def test_la_matriz_usa_el_catalogo_sin_un_segundo_mapa():
    assert MATRIX_KNOWN_CHECKS is KNOWN_CHECKS
    assert KNOWN_CHECKS == {check_id: spec.connector for check_id, spec in CHECKS.items()}


def test_el_conector_de_workspace_es_el_nombre_del_conector_registrado():
    assert KNOWN_CHECKS["workspace.user_2sv_enrolled"] == "google-workspace"
    assert "google_workspace" not in set(KNOWN_CHECKS.values())


def test_org_2fa_esta_en_el_catalogo_con_la_organizacion_como_sujeto():
    assert CHECKS["github.org_2fa_required"].subjects is SubjectKind.ORGANIZATION


def test_los_tres_checks_de_github_con_skip_archived_excluyen_los_archivados():
    skipping = {check_id for check_id, spec in CHECKS.items() if spec.skip_archived}

    assert skipping == {
        "github.default_branch_protected",
        "github.secret_scanning_enabled",
        "github.dependabot_alerts_enabled",
    }


def test_la_politica_de_archivados_coincide_con_la_de_los_productores():
    source = Path(importlib.import_module("ens_evidence.connectors.github.checks").__file__)
    producers = {
        node.name: any(
            kw.arg == "skip_archived" and getattr(kw.value, "value", None) is True
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            for kw in call.keywords
        )
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef)
    }

    assert (
        producers["default_branch_protected"]
        is CHECKS["github.default_branch_protected"].skip_archived
    )
    assert (
        producers["secret_scanning_enabled"]
        is CHECKS["github.secret_scanning_enabled"].skip_archived
    )
    assert (
        producers["dependabot_alerts_enabled"]
        is CHECKS["github.dependabot_alerts_enabled"].skip_archived
    )
    assert producers["org_2fa_required"] is CHECKS["github.org_2fa_required"].skip_archived


def test_el_catalogo_es_un_modulo_hoja():
    tree = ast.parse(Path(catalog.__file__).read_text())
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }

    assert not any(name and name.startswith("ens_evidence") for name in imported)
