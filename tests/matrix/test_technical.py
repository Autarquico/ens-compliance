import importlib
import re
from datetime import timedelta

import pytest

from ens_evidence.matrix import (
    KNOWN_CHECKS,
    TECHNICAL_MAX_AGE,
    CheckRun,
    NoCheckResults,
    StaticCheckResults,
    technical_support,
)
from ens_evidence.models.enums import CheckStatus
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.workspace import LocalStore

from .source_helpers import AT, CHECK, OTHER_CHECK, ROW, check_link, check_result, run

UNITS = frozenset({"org.1.1", "org.1.2"})


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def support(store, runs, link=None, at=AT):
    return technical_support(store, ROW, link or check_link(), runs, at)


def details(result):
    return sorted((b.kind, b.detail) for b in result.blockers)


def test_todo_pass_de_la_ultima_ejecucion_completa_sustenta_las_clausulas(store):
    result = support(store, [run([check_result(store)])])

    assert result.counts
    assert result.clauses == UNITS
    assert len(result.evidence) == 1


def test_sin_fuente_de_resultados_no_cuenta(store):
    runs = NoCheckResults().runs(store, AT)

    result = support(store, runs)

    assert not result.counts
    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, f"check_no_results:{CHECK}")]


def test_la_fuente_estatica_devuelve_sus_ejecuciones(store):
    runs = [run([check_result(store)])]

    assert StaticCheckResults(runs).runs(store, AT) == tuple(runs)


def test_un_check_desconocido_no_cuenta(store):
    result = support(store, [run([])], check_link("github.inventado"))

    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, "check_unknown:github.inventado")]


def test_dos_activos_con_uno_omitido_es_incompleto(store):
    runs = [run([check_result(store, "repo-a")], expected=("repo-a", "repo-b"))]

    result = support(store, runs)

    assert not result.counts
    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, f"check_incomplete:{CHECK}:repo-b")]


def test_dos_activos_con_resultado_cuentan(store):
    results = [check_result(store, "repo-a"), check_result(store, "repo-b")]

    result = support(store, [run(results, expected=("repo-a", "repo-b"))])

    assert result.counts
    assert len(result.evidence) == 2


def test_una_ejecucion_parcial_no_cuenta_ni_oculta_a_la_completa_anterior(store):
    complete = run([check_result(store)], run_id="ok", finished_at=AT - timedelta(days=2))
    partial = run(
        [check_result(store, status=CheckStatus.FAIL, evidence_ids=[])],
        run_id="parcial",
        finished_at=AT - timedelta(hours=1),
        complete=False,
        errors=("timeout",),
    )

    result = support(store, [complete, partial])

    assert result.counts
    assert result.available_at == complete.finished_at


def test_solo_una_ejecucion_parcial_no_cuenta(store):
    partial = run([check_result(store)], complete=False, errors=("timeout",))

    result = support(store, [partial])

    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, f"check_no_results:{CHECK}")]


def test_gana_la_ejecucion_completa_mas_reciente(store):
    old = run([check_result(store)], run_id="a", finished_at=AT - timedelta(days=3))
    new = run(
        [check_result(store, status=CheckStatus.FAIL, evidence_ids=[])],
        run_id="b",
        finished_at=AT - timedelta(days=1),
    )

    result = support(store, [old, new])

    assert details(result) == [(BlockerKind.NOT_COMPLIANT, f"check_failed:{CHECK}:repo-a")]


def test_dos_conectores_ejecutados_por_separado_son_independientes(store):
    github = run([check_result(store)], connector="github", run_id="g")
    restic_result = check_result(store, "repo-r", check_id=OTHER_CHECK)
    restic = run(
        [restic_result],
        connector="restic",
        run_id="r",
        expected=("repo-r",),
        finished_at=AT - timedelta(days=5),
    )
    runs = [github, restic]

    assert support(store, runs, check_link(CHECK)).counts
    assert support(store, runs, check_link(OTHER_CHECK)).counts
    only_github = support(store, [github], check_link(OTHER_CHECK))
    assert details(only_github) == [
        (BlockerKind.MISSING_EVIDENCE, f"check_no_results:{OTHER_CHECK}")
    ]


def test_un_fail_es_not_compliant(store):
    results = [
        check_result(store, "repo-a"),
        check_result(store, "repo-b", status=CheckStatus.FAIL, evidence_ids=[]),
    ]

    result = support(store, [run(results, expected=("repo-a", "repo-b"))])

    assert not result.counts
    assert result.clauses == frozenset()
    assert details(result) == [(BlockerKind.NOT_COMPLIANT, f"check_failed:{CHECK}:repo-b")]


@pytest.mark.parametrize("status", [CheckStatus.ERROR, CheckStatus.UNKNOWN])
def test_error_o_unknown_es_evidencia_ausente(store, status):
    result = support(store, [run([check_result(store, status=status, evidence_ids=[])])])

    assert details(result) == [
        (BlockerKind.MISSING_EVIDENCE, f"check_{status.value.lower()}:{CHECK}:repo-a")
    ]


def test_fail_y_pass_conviven_y_gana_el_fail(store):
    now = AT - timedelta(days=1)
    results = [
        check_result(store, evaluated_at=now),
        check_result(store, status=CheckStatus.FAIL, evaluated_at=now, evidence_ids=[]),
    ]

    result = support(store, [run(results)])

    assert details(result) == [(BlockerKind.NOT_COMPLIANT, f"check_failed:{CHECK}:repo-a")]


def test_la_evidencia_sin_valid_until_caduca_a_los_30_dias(store):
    assert TECHNICAL_MAX_AGE == timedelta(days=30)
    fresh = check_result(store, collected_at=AT - timedelta(days=29))
    stale = check_result(store, collected_at=AT - TECHNICAL_MAX_AGE)

    assert support(store, [run([fresh])]).counts
    expired = support(store, [run([stale])])
    assert [b.kind for b in expired.blockers] == [BlockerKind.EXPIRED_EVIDENCE]
    assert expired.blockers[0].detail.startswith("evidence_expired:EV-")


def test_un_resultado_futuro_se_ignora(store):
    future = check_result(store, evaluated_at=AT + timedelta(hours=1))

    result = support(store, [run([future])])

    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, f"check_incomplete:{CHECK}:repo-a")]


def test_un_resultado_futuro_no_tapa_un_fail_vigente(store):
    results = [
        check_result(store, status=CheckStatus.FAIL, evidence_ids=[]),
        check_result(store, evaluated_at=AT + timedelta(hours=1)),
    ]

    result = support(store, [run(results)])

    assert [b.kind for b in result.blockers] == [BlockerKind.NOT_COMPLIANT]


def test_una_ejecucion_posterior_a_la_consulta_se_ignora(store):
    later = run([check_result(store)], finished_at=AT + timedelta(hours=1))

    result = support(store, [later])

    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, f"check_no_results:{CHECK}")]


def test_el_manifiesto_con_artefacto_ausente_no_cuenta(store):
    result_ = check_result(store)
    (store._root / store.list("evidence/raw")[0]).unlink()

    result = support(store, [run([result_])])

    assert details(result) == [
        (BlockerKind.MISSING_EVIDENCE, f"evidence_unverified:{result_.evidence_ids[0]}")
    ]


def test_la_evidencia_inexistente_no_cuenta(store):
    result = support(store, [run([check_result(store, evidence_ids=["EV-20260101-deadbeef"])])])

    assert details(result) == [
        (BlockerKind.MISSING_EVIDENCE, "evidence_unverified:EV-20260101-deadbeef")
    ]


def test_covers_ajeno_a_la_medida_bloquea(store):
    result = support(store, [run([check_result(store)])], check_link(covers=("R9",)))

    assert not result.counts
    assert result.clauses == frozenset()


def test_cubre_solo_las_clausulas_pedidas(store):
    result = support(store, [run([check_result(store)])], check_link(covers=("org.1.2",)))

    assert result.clauses == frozenset({"org.1.2"})


def test_una_ejecucion_completa_con_errores_es_incoherente():
    with pytest.raises(ValueError, match="errores"):
        run([], errors=("x",))
    with pytest.raises(ValueError, match="finished_at"):
        CheckRun("r", "github", AT, AT - timedelta(seconds=1), True)


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
        ("ens_evidence.connectors.github.checks", "github", r"github\.[a-z_]+"),
        ("ens_evidence.connectors.cloudflare.checks", "cloudflare", r"cloudflare\.[a-z_]+"),
        (
            "ens_evidence.connectors.google_workspace.checks",
            "google_workspace",
            r"workspace\.[a-z0-9_]+",
        ),
        ("ens_evidence.connectors.restic.checks", "restic", r"restic\.[a-z0-9_]+"),
        ("ens_evidence.endpoint.checks", "endpoint", r"HOST-\d{3}"),
    ],
)
def test_known_checks_coincide_con_las_constantes_de_cada_modulo(module, connector, pattern):
    declared = {check for check, owner in KNOWN_CHECKS.items() if owner == connector}

    assert declared == _constants(module, pattern)
    assert declared
