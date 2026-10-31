from datetime import timedelta

import pytest
import yaml

from ens_evidence.evidence.layout import manifest_key
from ens_evidence.matrix import (
    TECHNICAL_MAX_AGE,
    CheckRun,
    NoCheckResults,
    StaticCheckResults,
)
from ens_evidence.matrix.technical import latest_run, technical_support
from ens_evidence.models.enums import CheckStatus
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.workspace import LocalStore

from .source_helpers import AT, CHECK, OTHER_CHECK, ROW, check_link, check_result, run

UNITS = frozenset({"org.1.1", "org.1.2"})


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "ws")


def support(store, runs, link=None, at=AT, problems=None):
    return technical_support(store, ROW, link or check_link(), runs, at, problems or {})


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


def last_run_failed(connector="github"):
    return [(BlockerKind.MISSING_EVIDENCE, f"check_last_run_failed:{connector}")]


@pytest.mark.parametrize("errors", [("timeout",), ()])
def test_un_intento_fallido_posterior_a_un_pass_no_recupera_el_pass(store, errors):
    two_days = AT - timedelta(days=2)
    complete = run(
        [check_result(store, evaluated_at=two_days, collected_at=two_days)],
        run_id="ok",
        finished_at=two_days,
    )
    assert support(store, [complete]).counts
    failed = run(
        [], run_id="fallido", finished_at=AT - timedelta(hours=1), complete=False, errors=errors
    )

    result = support(store, [complete, failed])

    assert not result.counts
    assert details(result) == last_run_failed()


def test_solo_un_intento_incompleto_bloquea_por_ultimo_intento_fallido(store):
    partial = run([check_result(store)], complete=False, errors=("timeout",))

    result = support(store, [partial])

    assert details(result) == last_run_failed()


def test_una_ejecucion_completa_posterior_a_un_fallo_vuelve_a_sustentar(store):
    failed = run([], run_id="a", finished_at=AT - timedelta(days=2), complete=False)
    good = run([check_result(store)], run_id="b", finished_at=AT - timedelta(hours=1))

    assert support(store, [failed, good]).counts


def test_el_fallo_de_un_conector_no_bloquea_a_otro(store):
    failed = run([], complete=False, errors=("x",))
    other = run(
        [check_result(store, "r", check_id=OTHER_CHECK)],
        run_id="r",
        connector="restic",
        expected=("r",),
    )

    assert support(store, [failed, other], check_link(OTHER_CHECK)).counts


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
    restic_result = check_result(
        store,
        "repo-r",
        check_id=OTHER_CHECK,
        evaluated_at=AT - timedelta(days=5),
        collected_at=AT - timedelta(days=5),
    )
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


def test_error_es_evidencia_ausente(store):
    result = support(store, [run([check_result(store, status=CheckStatus.ERROR, evidence_ids=[])])])

    assert details(result) == [(BlockerKind.MISSING_EVIDENCE, f"check_error:{CHECK}:repo-a")]


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


def test_alargar_valid_until_no_reactiva_evidencia_tecnica_de_mas_de_30_dias(store):
    stale = check_result(store, collected_at=AT - TECHNICAL_MAX_AGE - timedelta(days=1))
    key = manifest_key(stale.evidence_ids[0])
    data = yaml.safe_load(store.read_text(key))
    data["valid_until"] = (AT + timedelta(days=365)).isoformat()
    store.write_text(key, yaml.safe_dump(data, sort_keys=False))

    expired = support(store, [run([stale])])

    assert [b.kind for b in expired.blockers] == [BlockerKind.EXPIRED_EVIDENCE]


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


def _collected_at(store, evidence_id, moment):
    key = manifest_key(evidence_id)
    data = yaml.safe_load(store.read_text(key))
    data["collected_at"] = moment.isoformat()
    store.write_text(key, yaml.safe_dump(data, sort_keys=False))


def test_cambiar_collected_at_no_rejuvenece_un_pass_antiguo(store):
    old = AT - timedelta(days=31)
    result = check_result(store, evaluated_at=old, collected_at=old)
    stale = run([result], finished_at=old)
    assert [b.kind for b in support(store, [stale]).blockers] == [BlockerKind.EXPIRED_EVIDENCE]

    _collected_at(store, result.evidence_ids[0], AT - timedelta(days=1))
    forged = support(store, [run([result], finished_at=old)])

    assert not forged.counts
    assert details(forged) == [
        (BlockerKind.MISSING_EVIDENCE, f"evidence_inconsistent:{result.evidence_ids[0]}")
    ]


def test_la_evidencia_posterior_al_fin_de_la_ejecucion_es_incoherente(store):
    result = check_result(store, evaluated_at=AT - timedelta(days=1))
    _collected_at(store, result.evidence_ids[0], AT - timedelta(hours=6))

    forged = support(store, [run([result], finished_at=AT - timedelta(hours=12))])

    assert details(forged) == [
        (BlockerKind.MISSING_EVIDENCE, f"evidence_inconsistent:{result.evidence_ids[0]}")
    ]


def test_una_ejecucion_reciente_con_evidencia_reciente_si_cuenta(store):
    result = check_result(
        store, evaluated_at=AT - timedelta(hours=2), collected_at=AT - timedelta(hours=3)
    )

    assert support(store, [run([result], finished_at=AT - timedelta(hours=1))]).counts


def test_un_manifiesto_con_una_clave_repetida_no_cuenta(store):
    result = check_result(store)
    key = manifest_key(result.evidence_ids[0])
    store.write_text(key, store.read_text(key) + f"collected_at: {AT.isoformat()}\n")

    forged = support(store, [run([result])])

    assert details(forged) == [
        (BlockerKind.MISSING_EVIDENCE, f"evidence_unverified:{result.evidence_ids[0]}")
    ]


# --- huella de la evidencia al ejecutar ------------------------------------------------------


def test_la_evidencia_modificada_tras_la_ejecucion_no_sustenta(store):
    result = check_result(store)
    executed = run([result])
    assert support(store, [executed]).counts

    key = manifest_key(result.evidence_ids[0])
    data = yaml.safe_load(store.read_text(key))
    data["valid_until"] = (AT + timedelta(days=3)).isoformat()
    store.write_text(key, yaml.safe_dump(data, sort_keys=False))
    changed = support(store, [executed])

    assert not changed.counts
    assert details(changed) == [
        (BlockerKind.MISSING_EVIDENCE, f"check_evidence_changed:{result.evidence_ids[0]}")
    ]


def test_un_id_de_evidencia_sin_huella_en_la_ejecucion_no_sustenta(store):
    result = check_result(store)

    unbound = support(store, [run([result], evidence_sha256={})])

    assert not unbound.counts
    assert details(unbound) == [
        (BlockerKind.MISSING_EVIDENCE, f"evidence_unbound:{result.evidence_ids[0]}")
    ]


def test_una_huella_ajena_a_la_evidencia_citada_no_sustenta(store):
    result = check_result(store)
    other = check_result(store, "repo-b")
    forged = {result.evidence_ids[0]: run([other]).evidence_sha256[other.evidence_ids[0]]}

    assert not support(store, [run([result], evidence_sha256=forged)]).counts


# --- sujetos esperados por check -------------------------------------------------------------


def test_expected_for_usa_el_sujeto_del_check_y_si_no_el_de_la_ejecucion():
    executed = run([], expected=("a", "b"), expected_by_check={CHECK: ("a",)})

    assert executed.expected_for(CHECK) == {"a"}
    assert executed.expected_for(OTHER_CHECK) == {"a", "b"}


def test_los_sujetos_esperados_del_check_mandan_sobre_los_del_conector(store):
    results = [check_result(store, "repo-a")]
    both = run(results, expected=("repo-a", "repo-b"))
    narrowed = run(results, expected=("repo-a", "repo-b"), expected_by_check={CHECK: ("repo-a",)})

    assert not support(store, [both]).counts
    assert support(store, [narrowed]).counts


# --- UNKNOWN y problemas de conector ---------------------------------------------------------


def test_un_unknown_no_se_confunde_con_un_check_inexistente(store):
    unknown = check_result(store, status=CheckStatus.UNKNOWN, evidence_ids=[])

    result = support(store, [run([unknown])])

    assert details(result) == [
        (BlockerKind.MISSING_EVIDENCE, f"check_unknown_subject:{CHECK}:repo-a")
    ]


def test_un_problema_de_conector_bloquea_sus_checks_aunque_haya_un_pass(store):
    runs = [run([check_result(store)])]
    problems = {"github": ["check_history_unreadable:evidence/checks/github/x.json"]}

    result = support(store, runs, problems=problems)

    assert not result.counts
    assert details(result) == [
        (BlockerKind.MISSING_EVIDENCE, "check_history_unreadable:evidence/checks/github/x.json")
    ]


def test_un_problema_de_un_conector_no_bloquea_a_otro(store):
    runs = [
        run([check_result(store, "r", check_id=OTHER_CHECK)], connector="restic", expected=("r",))
    ]

    assert support(store, runs, check_link(OTHER_CHECK), problems={"github": ["p:1"]}).counts


def test_las_fuentes_estaticas_exponen_sus_problemas(store):
    assert NoCheckResults().connector_problems(store, AT) == {}
    problems = {"github": ("check_scope_changed:github",)}

    assert NoCheckResults(problems=problems).connector_problems(store, AT) == problems
    assert StaticCheckResults([], problems=problems).connector_problems(store, AT) == problems


def test_latest_run_devuelve_el_ultimo_intento_en_o_antes_de_at():
    early = run([], run_id="a", finished_at=AT - timedelta(days=2))
    late_failed = run([], run_id="b", finished_at=AT - timedelta(days=1), complete=False)
    future = run([], run_id="c", finished_at=AT + timedelta(days=1))

    assert latest_run([early, late_failed, future], "github", AT) is late_failed
    assert latest_run([early], "restic", AT) is None
