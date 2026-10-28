"""`github.default_branch_protected`: matriz por regla, sobre datos mínimos y recogida real."""

import pytest

from ens_evidence.connectors.github.checks import default_branch_protected
from ens_evidence.models.enums import CheckStatus

from .check_data import (
    ALL_RULES,
    EVIDENCE_ID,
    UNKNOWN_CLASSIC,
    actor,
    branch_item,
    branch_result,
    by_subject,
    classic,
    effective,
    only,
    repo,
    result,
    ruleset,
    run,
    section,
)
from .fake_api import FACTURAS, ORG, REPO, denied, fixture, message, not_found, repo_body

UNPROTECTED = (404, message("Branch not protected"))
LABELS = {
    "pull_request": "pull request",
    "non_fast_forward": "force-push",
    "deletion": "borrado",
}


def verdict(*items, repos=None):
    return only(run(default_branch_protected, branch_result(*items, repos=repos)))


def unprotected_default_branch(api):
    api.set(rf"{FACTURAS}/rules/branches/main", [])
    api.set(rf"{FACTURAS}/rulesets", [])
    api.set(rf"{FACTURAS}/branches/main/protection", UNPROTECTED)


# --- reglas de ruleset activo ---


def test_ruleset_activo_sin_bypass_con_las_tres_reglas_da_pass_citando_la_evidencia():
    outcome = verdict(branch_item())

    assert outcome.status is CheckStatus.PASS
    assert outcome.evidence_ids == [EVIDENCE_ID]
    assert outcome.subject == "facturas"
    assert outcome.check_id == "github.default_branch_protected"
    assert outcome.asset_id == "gh-taller"


@pytest.mark.parametrize("missing", ALL_RULES)
def test_ruleset_al_que_le_falta_una_regla_da_fail_nombrandola(missing):
    present = [rule for rule in ALL_RULES if rule != missing]

    outcome = verdict(branch_item(rules=effective(*present)))

    assert outcome.status is CheckStatus.FAIL
    assert LABELS[missing] in outcome.reason


def test_sin_ninguna_regla_ni_proteccion_clasica_da_fail():
    outcome = verdict(branch_item(rulesets=[], rules=effective()))

    assert outcome.status is CheckStatus.FAIL


@pytest.mark.parametrize("mode", ["always", "exempt"])
def test_ruleset_con_actores_de_bypass_da_unknown_bypass_configurado(mode):
    outcome = verdict(branch_item(rulesets=[ruleset(bypass_actors=[actor(mode)])]))

    assert outcome.status is CheckStatus.UNKNOWN
    assert "bypass configurado" in outcome.reason


def test_bypass_actors_ausente_da_unknown_bypass_no_visible_nunca_pass():
    outcome = verdict(branch_item(rulesets=[ruleset(bypass_actors=None)]))

    assert outcome.status is CheckStatus.UNKNOWN
    assert "bypass no visible" in outcome.reason


def test_bypass_actors_lista_vacia_puede_dar_pass():
    outcome = verdict(branch_item(rulesets=[ruleset(bypass_actors=[])]))

    assert outcome.status is CheckStatus.PASS


@pytest.mark.parametrize("enforcement", ["evaluate", "disabled"])
def test_ruleset_evaluate_o_disabled_no_aporta_reglas(enforcement):
    inventory = [ruleset(12, enforcement=enforcement)]

    outcome = verdict(branch_item(rulesets=inventory, rules=effective()))

    assert outcome.status is CheckStatus.FAIL


@pytest.mark.parametrize("enforcement", ["evaluate", "disabled"])
def test_bypass_de_un_ruleset_que_no_aporta_la_regla_no_impide_el_pass(enforcement):
    inventory = [ruleset(11), ruleset(12, [actor("always")], enforcement=enforcement)]

    assert verdict(branch_item(rulesets=inventory)).status is CheckStatus.PASS


def test_ruleset_sin_detalle_da_unknown_bypass_no_visible():
    inventory = [ruleset(11, bypass_actors=None, status="PERMISSION_DENIED")]

    outcome = verdict(branch_item(rulesets=inventory))

    assert outcome.status is CheckStatus.UNKNOWN
    assert "bypass no visible" in outcome.reason


def test_regla_de_un_ruleset_ausente_del_inventario_da_unknown():
    outcome = verdict(branch_item(rulesets=[ruleset(99)]))

    assert outcome.status is CheckStatus.UNKNOWN


def test_inventario_de_rulesets_no_recogido_da_unknown_sin_bypass_demostrado():
    item = branch_item()
    item["rulesets"] = {"status": "PERMISSION_DENIED", "reason": "sin permiso", "items": []}

    outcome = verdict(item)

    assert outcome.status is CheckStatus.UNKNOWN
    assert "bypass no visible" in outcome.reason


@pytest.mark.parametrize("failed", ["PERMISSION_DENIED", "ERROR"])
def test_inventario_de_rulesets_caido_traduce_su_estado_segun_el_adr_0002(failed):
    item = branch_item()
    item["rulesets"] = {"status": failed, "reason": "sin permiso", "items": []}

    outcome = verdict(item)

    assert outcome.collection_status.value == failed
    assert (outcome.status is CheckStatus.ERROR) == (failed == "ERROR")
    if failed == "PERMISSION_DENIED":
        assert outcome.reason_code == "permission_denied"


def test_detalle_de_ruleset_caido_traduce_su_estado():
    outcome = verdict(branch_item(rulesets=[ruleset(status="PERMISSION_DENIED")]))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


def test_rulesets_de_la_organizacion_y_del_repo_se_evaluan_por_separado():
    rules = [
        {"type": "pull_request", "ruleset_id": 11},
        {"type": "non_fast_forward", "ruleset_id": 21},
        {"type": "deletion", "ruleset_id": 21},
    ]
    item = branch_item(
        rulesets=[ruleset(11), ruleset(21, [actor("always")])],
        rules={"status": "SUCCESS", "rules": rules},
    )

    outcome = verdict(item)

    assert outcome.status is CheckStatus.UNKNOWN
    assert "force-push" in outcome.reason
    assert "pull request" not in outcome.reason


# --- protección clásica ---


def classic_only(**flags):
    return branch_item(rulesets=[], rules=effective(), classic_part=classic(**flags))


def test_proteccion_clasica_completa_con_admins_incluidos_da_pass():
    assert verdict(classic_only()).status is CheckStatus.PASS


@pytest.mark.parametrize("enforce_admins", [False, None])
def test_proteccion_clasica_sin_enforce_admins_demostrado_da_unknown(enforce_admins):
    outcome = verdict(classic_only(enforce_admins=enforce_admins))

    assert outcome.status is CheckStatus.UNKNOWN


def test_proteccion_clasica_con_enforce_admins_false_da_unknown_bypass_configurado():
    outcome = verdict(classic_only(enforce_admins=False))

    assert "bypass configurado" in outcome.reason


def test_proteccion_clasica_con_bypass_pull_request_allowances_da_unknown():
    outcome = verdict(classic_only(bypass_allowances=True))

    assert outcome.status is CheckStatus.UNKNOWN
    assert "bypass configurado" in outcome.reason


def test_proteccion_clasica_sin_la_bandera_de_bypass_allowances_no_da_pass():
    item = classic_only()
    del item["classic"]["bypass_pull_request_allowances"]

    assert verdict(item).status is CheckStatus.UNKNOWN


@pytest.mark.parametrize("flag", ["pull_request", "force_push", "deletion"])
def test_proteccion_clasica_con_una_bandera_ausente_da_unknown(flag):
    outcome = verdict(classic_only(**{flag: None}))

    assert outcome.status is CheckStatus.UNKNOWN
    assert "no visible" in outcome.reason


@pytest.mark.parametrize(
    ("flag", "value", "label"),
    [
        ("pull_request", False, "pull request"),
        ("force_push", True, "force-push"),
        ("deletion", True, "borrado"),
    ],
)
def test_proteccion_clasica_que_permite_lo_que_debe_bloquear_da_fail(flag, value, label):
    outcome = verdict(classic_only(**{flag: value}))

    assert outcome.status is CheckStatus.FAIL
    assert label in outcome.reason


def test_proteccion_clasica_de_estado_desconocido_no_da_fail_ni_pass():
    item = branch_item(rulesets=[], rules=effective(), classic_part=UNKNOWN_CLASSIC)

    outcome = verdict(item)

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


# --- mezcla de fuentes ---


def test_pr_por_ruleset_y_force_push_y_borrado_por_proteccion_clasica_da_pass():
    item = branch_item(rules=effective("pull_request"), classic_part=classic())

    assert verdict(item).status is CheckStatus.PASS


def test_ruleset_con_bypass_y_clasica_que_demuestra_las_tres_da_pass():
    item = branch_item(rulesets=[ruleset(bypass_actors=[actor("always")])], classic_part=classic())

    assert verdict(item).status is CheckStatus.PASS


def test_ruleset_limpio_con_clasica_sin_enforce_admins_da_pass_porque_el_ruleset_demuestra():
    item = branch_item(classic_part=classic(enforce_admins=False))

    assert verdict(item).status is CheckStatus.PASS


def test_una_fuente_caida_no_anula_a_otra_que_demuestra_ruleset():
    item = branch_item(classic_part=UNKNOWN_CLASSIC)

    assert verdict(item).status is CheckStatus.PASS


def test_una_fuente_caida_no_anula_a_otra_que_demuestra_clasica():
    item = classic_only()
    item["effective_rules"] = effective(status="ERROR", reason="HTTP 500")

    assert verdict(item).status is CheckStatus.PASS


def test_fuente_caida_y_la_otra_sin_demostrar_da_la_traduccion_de_la_caida():
    item = branch_item(
        rules=effective(status="ERROR", reason="HTTP 500"), classic_part=classic(pull_request=None)
    )

    outcome = verdict(item)

    assert outcome.status is CheckStatus.ERROR


# --- repositorios ---


def test_repositorios_archivados_quedan_fuera():
    evidence = result(
        repos=[repo("facturas"), repo("viejo", archived=True)],
        branch_rules=section([branch_item("facturas")]),
    )

    assert set(by_subject(run(default_branch_protected, evidence))) == {"facturas"}


def test_repo_sin_item_en_branch_rules_da_error_explicito():
    evidence = result(
        repos=[repo("facturas"), repo("infra")], branch_rules=section([branch_item()])
    )

    outcomes = by_subject(run(default_branch_protected, evidence))

    assert outcomes["facturas"].status is CheckStatus.PASS
    assert outcomes["infra"].status is CheckStatus.ERROR
    assert "sin dato" in outcomes["infra"].reason


def test_seccion_branch_rules_ausente_da_error_por_repo():
    outcome = only(run(default_branch_protected, result(repos=[repo()])))

    assert outcome.status is CheckStatus.ERROR
    assert outcome.subject == "facturas"


def test_branch_rules_no_recogida_da_unknown_con_su_motivo():
    evidence = result(
        repos=[repo()],
        branch_rules=section(status="PERMISSION_DENIED", reason="depende de repositories"),
    )

    outcome = only(run(default_branch_protected, evidence))

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


# --- sobre la recogida real ---


def test_recogida_con_ruleset_activo_sin_bypass_da_pass_en_todos_los_repos(collect):
    outcomes = by_subject(run(default_branch_protected, collect()))

    assert {o.status for o in outcomes.values()} == {CheckStatus.PASS}
    assert set(outcomes) == {"facturas", "infra"}


def test_recogida_con_bypass_always_en_el_ruleset_activo_da_unknown(api, collect):
    api.set(
        rf"{FACTURAS}/rulesets/11",
        {
            **fixture("ruleset_detail"),
            "bypass_actors": [{"actor_type": "OrganizationAdmin", "bypass_mode": "always"}],
        },
    )
    api.set(rf"{FACTURAS}/branches/main/protection", UNPROTECTED)

    outcomes = by_subject(run(default_branch_protected, collect()))

    assert outcomes["facturas"].status is CheckStatus.UNKNOWN
    assert outcomes["infra"].status is CheckStatus.PASS


def test_recogida_con_bypass_en_un_ruleset_evaluate_no_impide_el_pass(api, collect):
    detail = {
        **fixture("ruleset_detail"),
        "bypass_actors": [{"actor_type": "OrganizationAdmin", "bypass_mode": "always"}],
    }
    api.set(rf"{REPO}/rulesets/12", detail)
    api.set(rf"{REPO}/rulesets/13", detail)

    assert {o.status for o in run(default_branch_protected, collect())} == {CheckStatus.PASS}


def test_recogida_sin_bypass_actors_en_el_detalle_da_unknown_bypass_no_visible(api, collect):
    detail = fixture("ruleset_detail")
    del detail["bypass_actors"]
    api.set(rf"{FACTURAS}/rulesets/11", detail)
    api.set(rf"{FACTURAS}/branches/main/protection", UNPROTECTED)

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert "bypass no visible" in outcome.reason


def test_recogida_sin_ruleset_y_branch_not_protected_explicito_da_fail(api, collect):
    unprotected_default_branch(api)

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.FAIL


@pytest.mark.parametrize(
    "reply",
    [
        not_found(),
        denied(),
        (404, message("branch not protected")),
        (403, message("Branch not protected")),
    ],
    ids=["404-generico", "403", "404-otro-texto", "403-mismo-texto"],
)
def test_recogida_con_404_generico_nunca_da_fail_ni_pass(api, collect, reply):
    api.set(rf"{FACTURAS}/rules/branches/main", [])
    api.set(rf"{FACTURAS}/rulesets", [])
    api.set(rf"{FACTURAS}/branches/main/protection", reply)

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "permission_denied"


def test_recogida_con_proteccion_clasica_completa_y_sin_rulesets_da_pass(api, collect):
    api.set(rf"{FACTURAS}/rules/branches/main", [])
    api.set(rf"{FACTURAS}/rulesets", [])

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.PASS


def test_recogida_con_clasica_que_permite_force_push_y_sin_rulesets_da_fail(api, collect):
    protection = fixture("protection")
    protection["allow_force_pushes"] = {"enabled": True}
    api.set(rf"{FACTURAS}/rules/branches/main", [])
    api.set(rf"{FACTURAS}/rulesets", [])
    api.set(rf"{FACTURAS}/branches/main/protection", protection)

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.FAIL
    assert "force-push" in outcome.reason


def test_recogida_con_clasica_sin_enforce_admins_y_sin_rulesets_da_unknown(api, collect):
    protection = fixture("protection")
    protection["enforce_admins"] = {"enabled": False}
    api.set(rf"{FACTURAS}/rules/branches/main", [])
    api.set(rf"{FACTURAS}/rulesets", [])
    api.set(rf"{FACTURAS}/branches/main/protection", protection)

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN


def test_recogida_con_reglas_efectivas_caidas_y_clasica_completa_da_pass(api, collect):
    api.set(rf"{FACTURAS}/rules/branches/main", (500, message("boom")))

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.PASS


def test_recogida_con_clasica_denegada_y_ruleset_que_demuestra_da_pass(api, collect):
    api.set(rf"{FACTURAS}/branches/main/protection", denied())

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.PASS


def test_recogida_con_repo_archivado_lo_deja_fuera(api, collect):
    api.set(rf"/orgs/{ORG}/repos", [repo_body("facturas"), repo_body("infra", archived=True)])

    assert set(by_subject(run(default_branch_protected, collect()))) == {"facturas"}


def test_sin_proteccion_la_unica_fuente_es_el_texto_exacto(api, collect):
    unprotected_default_branch(api)
    api.set(rf"{FACTURAS}/rulesets", denied())

    outcome = by_subject(run(default_branch_protected, collect()))["facturas"]

    assert outcome.status is CheckStatus.FAIL


def test_repositorios_no_recogidos_dan_un_unico_resultado_sin_pass(api, collect):
    api.set(rf"/orgs/{ORG}/repos", denied())

    results = run(default_branch_protected, collect())

    assert [r.status for r in results] == [CheckStatus.UNKNOWN]
    assert results[0].subject is None
