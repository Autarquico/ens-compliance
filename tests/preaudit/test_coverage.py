"""Cobertura determinista del payload: qué propiedad de una evidencia `collector` ya comprueba un
check y qué queda para el juicio."""

from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from assessment.dossier.stages import AFTER, ID, basic, restore, run_at

from ens_evidence.checks.catalog import CHECKS
from ens_evidence.matrix.obligations import RowKind
from ens_evidence.preaudit.coverage import CHECK_PROPERTIES, FAIL, PASS, UNVERIFIED
from ens_evidence.preaudit.coverage import deterministic_coverage as coverage
from ens_evidence.preaudit.pack import load_pack
from ens_evidence.preaudit.payload import build_payload

PACK = load_pack()
MEASURE = "op.mon.1"


def export(*rows, checks=()):
    sources = NS(
        checks=[NS(check_id=c, evidence_ids=ids) for c, ids in checks],
    )
    return NS(rows=list(rows), sources=sources)


def row(measure, links, blockers=(), kind=RowKind.SELECTED):
    return NS(
        measure_id=measure,
        kind=kind,
        technical=[NS(kind=k, target=t, counts=c) for k, t, c in links],
        blockers=[NS(detail=d) for d in blockers],
    )


def test_every_catalog_check_describes_the_property_it_evaluates():
    assert set(CHECK_PROPERTIES) == set(CHECKS)
    assert all(text.strip() for text in CHECK_PROPERTIES.values())


@pytest.mark.parametrize(
    ("check", "stated"),
    [
        ("HOST-002", ["nada", "desconocido"]),
        ("HOST-004", ["volumen raíz"]),
        ("HOST-005", ["macOS", "Linux", "desconocido"]),
        ("HOST-006", ["macOS", "Linux", "desconocido"]),
        ("HOST-007", ["no prueba que el inventario sea completo"]),
        ("github.default_branch_protected", ["bypass"]),
        ("workspace.user_2sv_enrolled", ["usuario activo", "no prueba MFA"]),
        ("workspace.admin_2sv_enforced", ["administrador", "descubrimiento"]),
        ("restic.backup_005_restore_test", ["declaración humana"]),
        ("restic.backup_006_offsite", ["declaración offsite", "no comprobada"]),
    ],
)
def test_a_description_states_the_limits_of_what_the_check_really_evaluates(check, stated):
    text = CHECK_PROPERTIES[check]
    assert all(fragment in text for fragment in stated)


def test_a_covered_property_names_measure_check_evidence_property_and_result():
    secrets = "github.secret_scanning_enabled"
    found = coverage(
        export(
            row("op.exp.8", [("check", secrets, True), ("evidence", "ev-1", True)]),
            checks=[(secrets, ["ev-1", "ev-fuera"])],
        ),
        {"ev-1"},
    )

    assert found == [
        {
            "id": f"coverage:op.exp.8:{secrets}",
            "measure": "op.exp.8",
            "check": secrets,
            "evidence": ["ev-1"],
            "evaluates": CHECK_PROPERTIES[secrets],
            "result": PASS,
        }
    ]


def test_a_collector_property_without_a_check_is_not_covered():
    """Un check de secret scanning no dice nada de la visibilidad del repositorio: la visibilidad
    no aparece como propiedad evaluada y queda para el juicio."""
    secrets = "github.secret_scanning_enabled"
    found = coverage(
        export(row("op.exp.8", [("check", secrets, True)]), checks=[(secrets, ["ev-1"])]),
        {"ev-1"},
    )

    assert [e["check"] for e in found] == [secrets]
    assert "visibilidad" not in found[0]["evaluates"]  # lo que el check no evalúa no consta


@pytest.mark.parametrize(
    ("blockers", "counts", "expected"),
    [
        (["check_failed:github.secret_scanning_enabled:org/repo"], True, FAIL),
        (["check_unknown_subject:github.secret_scanning_enabled:org/repo"], True, UNVERIFIED),
        (["check_no_results:github.secret_scanning_enabled"], False, UNVERIFIED),
        (["check_failed:HOST-001:equipo"], True, PASS),
        ([], False, UNVERIFIED),
    ],
)
def test_the_result_comes_from_the_matrix_blockers(blockers, counts, expected):
    secrets = "github.secret_scanning_enabled"
    found = coverage(
        export(row("op.exp.8", [("check", secrets, counts)], blockers), checks=[]), set()
    )

    assert [e["result"] for e in found] == [expected]


def test_only_applicable_measures_and_check_links_are_covered():
    secrets = "github.secret_scanning_enabled"
    found = coverage(
        export(
            row("op.a", [("check", secrets, True)], kind=RowKind.NOT_SELECTED),
            row("op.b", [("evidence", "ev-1", True)]),
        ),
        {"ev-1"},
    )

    assert found == []


# --- sobre un expediente real ----------------------------------------------------------------


@pytest.fixture(scope="module")
def stages():
    return basic()


@pytest.fixture
def world(stages, tmp_path, monkeypatch):
    return restore(stages, "generated", tmp_path, monkeypatch)


def test_the_payload_exposes_the_coverage_of_a_linked_check(world):
    world.at(AFTER - timedelta(minutes=5))
    linked = world.run(
        "matrix", "link", MEASURE, "--check", "github.secret_scanning_enabled", "--covers", "base"
    )
    assert linked.exit_code == 0, linked.output
    generated = run_at(world, "assessment", "dossier", "generate", at=AFTER)
    assert generated.exit_code == 0, generated.output

    built = build_payload(world.store, ID.search(generated.output).group(), PACK)

    entries = built.data["dossier"]["deterministic_coverage"]
    entry = next(
        e for e in entries if e["id"] == f"coverage:{MEASURE}:github.secret_scanning_enabled"
    )
    assert entry["evaluates"] == CHECK_PROPERTIES["github.secret_scanning_enabled"]
    assert entry["result"] == PASS
    assert entries == sorted(entries, key=lambda e: e["id"])
