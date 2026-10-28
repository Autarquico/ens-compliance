"""Scenarios de la spec `collector-github` sobre la recogida real (MockTransport)."""

import pytest

from ens_evidence.connectors.github import GitHubConnector, checks
from ens_evidence.connectors.github.connector import TOKEN_ENV
from ens_evidence.models.enums import CheckStatus, CollectionStatus

from .check_data import by_subject, repo, result, run, section
from .fake_api import FACTURAS, NOW, ORG, REPO, TOKEN, FakeApi, denied, message, repo_body

ALL_CHECKS = [
    checks.org_2fa_required,
    checks.default_branch_protected,
    checks.secret_scanning_enabled,
    checks.dependabot_alerts_enabled,
]
NAMES = [check.__name__ for check in ALL_CHECKS]


def run_all(evidence):
    return {check.__name__: run(check, evidence) for check in ALL_CHECKS}


@pytest.mark.parametrize("check", ALL_CHECKS, ids=NAMES)
def test_credencial_revocada_al_inicio_no_da_pass_en_ningun_check(api, collect, check):
    api.set("/user", (401, message("Bad credentials")))

    evidence = collect()
    results = run(check, evidence)

    assert evidence.collection_status is CollectionStatus.ERROR
    assert results
    assert {r.status for r in results} == {CheckStatus.ERROR}
    assert {r.collection_status for r in results} == {CollectionStatus.ERROR}


@pytest.mark.parametrize("check", ALL_CHECKS, ids=NAMES)
def test_credencial_revocada_a_mitad_de_la_recogida_no_da_pass_en_ningun_check(api, collect, check):
    api.sequence("/user", [{"login": "ci-bot"}, (401, message("Bad credentials"))])

    results = run(check, collect())

    assert results
    assert all(r.status is CheckStatus.ERROR for r in results)


def test_sin_token_ningun_check_da_pass(config):
    connector = GitHubConnector(transport=FakeApi().transport, clock=lambda: NOW, env={})
    evidence = connector.collect(config)

    assert TOKEN_ENV in evidence.data["error"]
    assert all(r.status is CheckStatus.ERROR for rs in run_all(evidence).values() for r in rs)


def test_el_motivo_de_la_recogida_fallida_no_contiene_el_token(api, collect):
    api.set("/user", (401, message(f"Bad credentials {TOKEN}")))

    reasons = [r.reason for rs in run_all(collect()).values() for r in rs]

    assert reasons
    assert not any(TOKEN in reason for reason in reasons)


def test_recogida_exitosa_cumple_todos_los_checks_sobre_el_fixture_base(collect):
    evidence = collect()

    statuses = {name: {r.status for r in rs} for name, rs in run_all(evidence).items()}

    assert statuses["org_2fa_required"] == {CheckStatus.PASS}
    assert statuses["default_branch_protected"] == {CheckStatus.PASS}
    assert statuses["dependabot_alerts_enabled"] == {CheckStatus.PASS}
    assert statuses["secret_scanning_enabled"] == {CheckStatus.PASS, CheckStatus.FAIL}


def test_permiso_insuficiente_en_cada_lectura_da_unknown_permission_denied_nunca_pass(api, collect):
    api.set(rf"/orgs/{ORG}", denied())
    api.set(rf"{REPO}/branches/[^/]+/protection", denied())
    api.set(rf"{REPO}/rules/branches/[^/]+", denied())
    api.set(rf"{REPO}/vulnerability-alerts", denied())
    api.set(rf"/orgs/{ORG}/repos", [repo_body("facturas", security_and_analysis=None)])

    for name, results in run_all(collect()).items():
        assert results, name
        for outcome in results:
            assert outcome.status is CheckStatus.UNKNOWN, (name, outcome.reason)
            assert outcome.reason_code == "permission_denied", name


def test_secret_scanning_no_disponible_en_el_plan_da_unknown_not_supported(api, collect):
    api.set(rf"/orgs/{ORG}/repos", [repo_body("facturas", security_and_analysis={})])

    outcome = by_subject(run(checks.secret_scanning_enabled, collect()))["facturas"]

    assert outcome.status is CheckStatus.UNKNOWN
    assert outcome.reason_code == "not_supported"


@pytest.mark.parametrize("check", ALL_CHECKS, ids=NAMES)
def test_los_resultados_validan_contra_el_modelo_y_el_subject_es_estable(collect, check):
    first = run(check, collect())
    second = run(check, collect())

    assert [r.subject for r in first] == [r.subject for r in second]
    assert all(r.subject for r in first)
    assert all(r.evaluated_at == NOW for r in first)


def test_dependabot_desactivado_explicito_da_fail_y_los_demas_repos_no_cambian(api, collect):
    api.set(rf"{FACTURAS}/vulnerability-alerts", (404, message("Not Found")))
    api.set(
        rf"{FACTURAS}/dependabot/alerts",
        (403, message("Dependabot alerts are disabled for this repository.")),
    )

    outcomes = {r.subject: r.status for r in run(checks.dependabot_alerts_enabled, collect())}

    assert outcomes == {"facturas": CheckStatus.FAIL, "infra": CheckStatus.PASS}


PER_REPOSITORY = [
    (checks.default_branch_protected, "branch_rules"),
    (checks.secret_scanning_enabled, "secret_scanning"),
    (checks.dependabot_alerts_enabled, "dependabot"),
]


@pytest.mark.parametrize(
    ("check", "section_name"), PER_REPOSITORY, ids=[c.__name__ for c, _ in PER_REPOSITORY]
)
def test_repositorio_del_inventario_que_el_token_no_ve_da_unknown_permission_denied(
    check, section_name
):
    evidence = result(
        repos=[],
        **{section_name: section()},
        repositories=section([repo("infra")], missing_from_token=["facturas"]),
    )

    outcomes = by_subject(run(check, evidence))

    missing = outcomes["facturas"]
    assert missing.status is CheckStatus.UNKNOWN
    assert missing.reason_code == "permission_denied"
    assert missing.collection_status is CollectionStatus.PERMISSION_DENIED
    assert "no visible para el token" in missing.reason
