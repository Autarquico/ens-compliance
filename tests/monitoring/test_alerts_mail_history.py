import json
from datetime import timedelta

from ens_evidence.monitoring.alerts import AlertBook
from ens_evidence.monitoring.history import RETENTION_DAYS, History
from ens_evidence.monitoring.mail import SmtpSettings, send_alert
from ens_evidence.monitoring.model import GROUP_VPS, Reading, Status

from .support import NOW, SMTP_ENV, fake_smtp


def reading(status, id="vps:disk:/", value=None):
    return Reading(id, GROUP_VPS, "Disco", status, "detalle", value=value)


def test_alert_on_transition_then_reminder_every_24_hours(tmp_path):
    book = AlertBook(tmp_path)

    assert [r.id for r in book.update([reading(Status.FAIL)], NOW)] == ["vps:disk:/"]
    book.mark_notified(["vps:disk:/"], NOW)
    book.save()

    again = AlertBook(tmp_path)
    assert again.update([reading(Status.FAIL)], NOW + timedelta(hours=23)) == []
    assert again.since("vps:disk:/") == NOW
    assert len(again.update([reading(Status.FAIL)], NOW + timedelta(hours=24))) == 1


def test_recovery_clears_since_and_a_new_failure_alerts_again(tmp_path):
    book = AlertBook(tmp_path)
    book.update([reading(Status.FAIL)], NOW)
    book.mark_notified(["vps:disk:/"], NOW)

    book.update([reading(Status.OK)], NOW + timedelta(hours=1))
    assert book.since("vps:disk:/") is None
    later = NOW + timedelta(hours=2)

    assert len(book.update([reading(Status.FAIL)], later)) == 1
    assert book.since("vps:disk:/") == later


def test_unknown_to_fail_is_a_transition_and_unknown_never_alerts(tmp_path):
    book = AlertBook(tmp_path)

    assert book.update([reading(Status.UNKNOWN)], NOW) == []
    assert book.since("vps:disk:/") == NOW
    assert len(book.update([reading(Status.FAIL)], NOW + timedelta(minutes=15))) == 1


def test_smtp_settings_are_all_or_nothing():
    assert SmtpSettings.from_env({}) is None
    partial = {k: v for k, v in SMTP_ENV.items() if k != "ENS_SMTP_PASSWORD"}
    assert SmtpSettings.from_env(partial) is None
    settings = SmtpSettings.from_env(SMTP_ENV)
    assert settings.recipients == ("ana@example.org", "luis@example.org")
    assert "s3cret" not in repr(settings)


def test_send_uses_starttls_before_login():
    smtp = fake_smtp()

    error = send_alert(SmtpSettings.from_env(SMTP_ENV), [reading(Status.FAIL)], factory=smtp)

    assert error is None
    names = [call[0] for call in smtp.calls]
    assert names.index("starttls") < names.index("login")
    assert "no cambia" in smtp.sent[0].get_content()


def test_no_starttls_means_no_login_and_a_sanitized_error():
    smtp = fake_smtp(starttls=False)

    error = send_alert(SmtpSettings.from_env(SMTP_ENV), [reading(Status.FAIL)], factory=smtp)

    assert error == "SMTPNotSupportedError"
    assert all(call[0] != "login" for call in smtp.calls)


def test_history_appends_prunes_and_keeps_going_after_a_torn_line(tmp_path):
    history = History(tmp_path)
    old = NOW - timedelta(days=RETENTION_DAYS + 2)
    history.append([reading(Status.OK, value=1.0)], old)
    history.append([reading(Status.FAIL, value=90.0)], NOW - timedelta(hours=1))
    day_file = next((tmp_path / "monitoring" / "history").glob(f"{NOW.date()}*.jsonl"))
    day_file.write_text(day_file.read_text() + '{"t": "2026-11-14T11:5')
    history.append([reading(Status.OK, value=2.0)], NOW)

    names = sorted(p.name for p in (tmp_path / "monitoring" / "history").iterdir())
    series = history.series(NOW)["vps:disk:/"]

    assert f"{old.date()}.jsonl" not in names
    assert series[-1].value == 2.0
    assert Status.FAIL in {p.status for p in series}
    assert json.loads(day_file.read_text().splitlines()[0])["items"]
