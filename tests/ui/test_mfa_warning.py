"""Aviso global de MFA: visible mientras el último resultado del check no sea PASS."""

import re
from datetime import timedelta

import pytest

from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.checks.store import connector_prefix, record_run
from ens_evidence.connectors.cloudflare.checks import UI_ACCESS_REQUIRES_MFA
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.ui.mfa_notice import RUNBOOK

from .conftest import NOW

SHA = "a" * 64
EVIDENCE = "EV-20261108-aaaaaaaa"
WARNING = "MFA no confirmada"
FORBIDDEN = re.compile(r"certific|garantiza|cumple el ENS|MFA activa", re.IGNORECASE)
NOTICE = re.compile(r'<div class="notice notice-warn".*?</div>', re.DOTALL)


def record_mfa(store, status, code=None, *, check_id=UI_ACCESS_REQUIRES_MFA, seq=1, hours=1):
    result = CheckResult(
        check_id=check_id,
        asset_id="cf-account",
        status=status,
        evaluated_at=NOW - timedelta(hours=hours),
        evidence_ids=[EVIDENCE],
        reason="r",
        reason_code=code,
    )
    run = StoredCheckRun(
        seq=seq,
        connector="cloudflare",
        engine_version="1",
        started_at=NOW - timedelta(hours=hours + 1),
        finished_at=NOW - timedelta(hours=hours),
        complete=True,
        scope_fingerprint=SHA,
        config_sha256=SHA,
        inputs=[CheckInput(evidence_id=EVIDENCE, manifest_sha256=SHA)],
        results=[result],
    )
    record_run(store, run, NOW)


def page(session) -> str:
    response = session.get("/ayuda/monitorizacion")
    assert response.status_code == 200
    return response.text


def test_no_run_warns(session):
    body = page(session)
    assert WARNING in body
    assert "not_checked" in body
    assert RUNBOOK in body


def test_other_check_only_warns(session, example_store):
    record_mfa(example_store, CheckStatus.PASS, check_id="cloudflare.otro")
    assert WARNING in page(session)


@pytest.mark.parametrize(
    ("status", "code"),
    [(CheckStatus.FAIL, "policy_without_mfa"), (CheckStatus.UNKNOWN, "ui_hostname_not_configured")],
)
def test_fail_and_unknown_warn_with_reason_code(session, example_store, status, code):
    record_mfa(example_store, status, code)
    body = page(session)
    assert WARNING in body
    assert code in body
    assert status.value in body
    assert RUNBOOK in body
    assert not FORBIDDEN.search(NOTICE.search(body)[0])


def test_pass_shows_nothing(session, example_store):
    record_mfa(example_store, CheckStatus.PASS)
    body = page(session)
    assert WARNING not in body
    assert not re.search(r"MFA activa|MFA está activa|garantiza", body, re.IGNORECASE)


def test_latest_run_wins_and_cache_follows_new_runs(session, example_store):
    record_mfa(example_store, CheckStatus.FAIL, "x_code", hours=3)
    assert "x_code" in page(session)
    record_mfa(example_store, CheckStatus.PASS, seq=2, hours=2)
    assert WARNING not in page(session)


def test_unreadable_store_warns(session, example_store):
    record_mfa(example_store, CheckStatus.PASS)
    key = example_store.list(connector_prefix("cloudflare"))[0]
    example_store.write_text(key, "no es json")
    body = page(session)
    assert WARNING in body
    assert "unreadable" in body


def test_page_never_writes(session, example_store):
    record_mfa(example_store, CheckStatus.FAIL, "x_code")
    before = sorted(example_store.list(""))
    page(session)
    assert sorted(example_store.list("")) == before


def test_every_reason_code_of_the_check_has_human_text():
    from ens_evidence.connectors.cloudflare import mfa_checks
    from ens_evidence.ui.mfa_notice import _REASONS

    codes = {
        getattr(mfa_checks, name)
        for name in (
            "HOSTNAME_NOT_CONFIGURED",
            "APP_NOT_FOUND",
            "APP_PARTIAL",
            "APP_UNRESOLVED",
            "POLICY_UNRESOLVED",
            "NO_ALLOW_POLICY",
            "NON_IDENTITY",
            "BYPASS",
            "MFA_MISSING",
            "STALE_EVIDENCE",
        )
    }

    assert codes <= set(_REASONS)


def test_notice_speaks_human_first_and_keeps_the_code_secondary(session):
    body = page(session)

    assert "Todavía no se ha ejecutado la comprobación" in body
    assert "(código: not_checked)" in body
    assert "`" not in NOTICE.search(body)[0]
    assert "es sin resultado" not in body
