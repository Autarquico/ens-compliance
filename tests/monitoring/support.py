from datetime import UTC, datetime

import httpx

from ens_evidence.agent.monitoring import Sample

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def sample(query, rows, at=NOW):
    return Sample(query, at, tuple(rows))


def github_transport(advisories=(), release_status=404, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path.endswith("/security-advisories"):
            return httpx.Response(200, json=list(advisories))
        if release_status == 200:
            return httpx.Response(200, json={"tag_name": "v9.9.9"})
        return httpx.Response(release_status, json={})

    return httpx.MockTransport(handler)


def unreachable_transport():
    def handler(request):
        raise httpx.ConnectError("sin red")

    return httpx.MockTransport(handler)


class FakeSmtp:
    """SMTP falso: anota lo que pasa y puede negarse a STARTTLS o a autenticar."""

    sent: list = []
    calls: list = []
    starttls_supported = True

    def __init__(self, host, port):
        self.calls.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self, context=None):
        import smtplib

        if not self.starttls_supported:
            raise smtplib.SMTPNotSupportedError("sin STARTTLS")
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        self.sent.append(message)


def fake_smtp(starttls=True):
    """Una clase nueva por prueba, para no compartir listas."""
    return type("Smtp", (FakeSmtp,), {"sent": [], "calls": [], "starttls_supported": starttls})


SMTP_ENV = {
    "ENS_SMTP_HOST": "smtp.example.org",
    "ENS_SMTP_PORT": "587",
    "ENS_SMTP_USER": "avisos@example.org",
    "ENS_SMTP_PASSWORD": "s3cret-password",
    "ENS_SMTP_FROM": "avisos@example.org",
    "ENS_SMTP_TO": "ana@example.org, luis@example.org",
}
