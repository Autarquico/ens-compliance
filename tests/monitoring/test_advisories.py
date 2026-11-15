from datetime import timedelta

import httpx

from ens_evidence.monitoring.advisories import (
    Advisory,
    AdvisoryFeed,
    RepoFeed,
    affects,
    judge,
    make_client,
    parse_version,
)

from .support import NOW, github_transport, unreachable_transport

ADVISORY = {
    "ghsa_id": "GHSA-aaaa-bbbb-cccc",
    "severity": "high",
    "state": "published",
    "vulnerabilities": [{"vulnerable_version_range": ">= 1.0, < 1.5.0"}],
}


def feed(tmp_path, transport):
    return AdvisoryFeed(tmp_path, make_client(transport))


def test_range_matching():
    assert affects((1, 4, 9), ">= 1.0, < 1.5.0") is True
    assert affects((1, 5), ">= 1.0, < 1.5.0") is False
    assert affects((1, 2), "= 1.2.0") is True
    assert affects((1, 2), "") is None
    assert affects((1, 2), "<= 1.x") is None
    assert parse_version("2.7.2-rc1") is None


def test_judge_fail_review_and_clear():
    hit = Advisory("GHSA-1", "high", (">= 1.0, < 2.0",))
    odd = Advisory("GHSA-2", "low", ("raro",))
    assert judge("1.5", RepoFeed(NOW, (hit,), None))[0] == "fail"
    assert judge("2.1", RepoFeed(NOW, (hit, odd), None))[0] == "review"
    assert judge("2.1", RepoFeed(NOW, (hit,), None))[0] == "clear"
    assert judge(None, RepoFeed(NOW, (), None))[0] == "review"


def test_fetch_caches_for_a_day_and_a_failed_refresh_is_never_a_verdict(tmp_path):
    calls = []
    source = feed(tmp_path, github_transport([ADVISORY], calls=calls))

    first = source.get("openbao/openbao", NOW)
    again = source.get("openbao/openbao", NOW + timedelta(hours=23))

    assert first.advisories[0].ghsa_id == "GHSA-aaaa-bbbb-cccc"
    assert again == first and len(calls) == 2  # una consulta de avisos y una de releases
    offline = feed(tmp_path, unreachable_transport())
    assert offline.get("openbao/openbao", NOW + timedelta(hours=40)) is None  # fallo: no vigente
    assert offline.reference("openbao/openbao", NOW + timedelta(hours=40)) == first
    assert offline.reference("openbao/openbao", NOW + timedelta(hours=49)) is None


def test_failure_without_cache_is_unknown_and_bad_repo_name_is_refused(tmp_path):
    assert feed(tmp_path, unreachable_transport()).get("openbao/openbao", NOW) is None
    assert feed(tmp_path, github_transport()).get("../etc/passwd", NOW) is None


def test_pagination_follows_only_github_links(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(404)
        if "page=2" in str(request.url):
            return httpx.Response(200, json=[{**ADVISORY, "ghsa_id": "GHSA-2"}])
        link = '<https://api.github.com/repos/o/r/security-advisories?page=2>; rel="next"'
        return httpx.Response(200, json=[ADVISORY], headers={"link": link})

    data = feed(tmp_path, httpx.MockTransport(handler)).get("o/r", NOW)

    assert [a.ghsa_id for a in data.advisories] == ["GHSA-aaaa-bbbb-cccc", "GHSA-2"]

    def evil(request):
        link = '<https://evil.example/x>; rel="next"'
        return httpx.Response(200, json=[ADVISORY], headers={"link": link})

    other = tmp_path / "otro"
    AdvisoryFeed(other, make_client(httpx.MockTransport(evil))).get("o/r", NOW)
    assert not any("evil" in url for url in seen)


def test_withdrawn_advisories_are_ignored(tmp_path):
    withdrawn = {**ADVISORY, "withdrawn_at": "2026-01-01T00:00:00Z"}

    data = feed(tmp_path, github_transport([withdrawn])).get("o/r", NOW)

    assert data.advisories == ()
