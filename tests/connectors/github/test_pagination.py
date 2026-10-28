import httpx
import pytest

from ens_evidence.connectors.github.client import MAX_PAGES

from .fake_api import API, FACTURAS, ORG, denied, fixture, page, repo_body

REPOS = f"/orgs/{ORG}/repos"
FILTERS = {REPOS: "&type=all"}


def next_link(path: str, number: int, host: str = API) -> str:
    return f"{host}{path}?per_page=100&page={number}{FILTERS.get(path, '')}"


def paged_repos(pages: dict[str, list], *, link_for=lambda number: next_link(REPOS, number)):
    """Responde cada página con su lista y `Link: next` mientras exista la siguiente."""

    def reply(request: httpx.Request) -> httpx.Response:
        number = request.url.params.get("page", "1")
        following = str(int(number) + 1)
        return page(pages[number], next_url=link_for(following) if following in pages else None)(
            request
        )

    return reply


def names(section: dict) -> list[str]:
    return [entry["name"] for entry in section["items"]]


def test_varias_paginas_se_concatenan_en_orden(api, section):
    api.set(
        REPOS,
        paged_repos(
            {"1": [repo_body("facturas")], "2": [repo_body("infra")], "3": [repo_body(name="otro")]}
        ),
    )

    repositories = section("repositories")

    assert repositories["status"] == "SUCCESS"
    assert names(repositories) == ["facturas", "infra", "otro"]


def test_la_primera_peticion_pide_el_tamano_maximo_y_no_sigue_sin_enlace(api, section):
    section("repositories")

    (first,) = api.params(REPOS)
    assert first["per_page"] == "100"
    assert first["type"] == "all"


def test_pagina_posterior_con_fallo_invalida_el_conjunto_sin_resultados_parciales(api, section):
    def second_fails(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(500, json={"message": "boom-CANARY"})
        return page([repo_body("facturas")], next_url=next_link(REPOS, 2))(request)

    api.set(REPOS, second_fails)

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["items"] == []
    assert repositories["reason"] == "HTTP 500: error de la API"


def test_pagina_posterior_sin_permiso_es_permission_denied_no_conjunto_parcial(api, section):
    def second_denied(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(403, json=denied()[1])
        return page([repo_body("facturas")], next_url=next_link(REPOS, 2))(request)

    api.set(REPOS, second_denied)

    repositories = section("repositories")

    assert repositories["status"] == "PERMISSION_DENIED"
    assert repositories["items"] == []


def test_ciclo_de_enlaces_es_error(api, section):
    api.set(REPOS, page([repo_body("facturas")], next_url=next_link(REPOS, 1)))

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["reason"] == "enlace de paginación no válido"
    assert len(api.params(REPOS)) < MAX_PAGES


def test_ciclo_entre_dos_paginas_es_error(api, section):
    def loop(request):
        number = request.url.params.get("page", "1")
        target = "2" if number == "1" else "1"
        return page([repo_body("facturas")], next_url=next_link(REPOS, int(target)))(request)

    api.set(REPOS, loop)

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert len(api.params(REPOS)) < MAX_PAGES


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/orgs/taller-norte/repos?page=2",
        "https://api.github.com.evil.example/orgs/taller-norte/repos?page=2",
        "http://api.github.com/orgs/taller-norte/repos?page=2",
        "https://api.github.com:8443/orgs/taller-norte/repos?page=2",
        "https://user:pw@api.github.com/orgs/taller-norte/repos?page=2",
        "https://api.github.com/orgs/otra-org/repos?page=2",
        "https://api.github.com/user?page=2",
        "https://api.github.com/orgs/taller-norte/members?page=2",
        "https://api.github.com/orgs/taller-norte/../otra-org/repos?page=2",
        "https://api.github.com/orgs/taller-norte/repos/../../otra-org/repos?page=2",
        "https://api.github.com/orgs/taller-norte/%2e%2e/otra-org/repos?page=2",
        "https://api.github.com/orgs/taller-norte/%2E%2E%2Fotra-org/repos?page=2",
        "https://api.github.com/orgs/taller-norte/./repos?page=2",
        "https://api.github.com/orgs/taller-norte//repos?page=2",
        "https://api.github.com/organizations/abc/repos?page=2",
        "https://api.github.com/organizations/456/members?page=2",
        "https://api.github.com/organizations/456/repos/extra?page=2",
        "https://api.github.com/repositories/456/repos?page=2",
        "https://api.github.com/orgs/taller-norte/repos?page=2&access_token=x",
        "https://api.github.com/orgs/taller-norte/repos?page=2#frag",
        "https://api.github.com:99999999/orgs/taller-norte/repos?page=2",
        "https://[api.github.com/orgs/taller-norte/repos?page=2",
        "https://api.github.com/orgs/taller-norte/repos?page=2\x00",
        "https://api.github.com/orgs/taller-norte/repos?page=2 CANARY",
    ],
)
def test_enlace_a_otro_origen_o_prefijo_no_se_sigue_y_es_error(api, section, target):
    api.set(REPOS, page([repo_body("facturas")], next_url=target))

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["reason"] == "enlace de paginación no válido"
    assert "CANARY" not in repositories["reason"]
    assert {call.url.host for call in api.calls} == {"api.github.com"}
    assert len(api.params(REPOS)) == 1


def test_el_enlace_a_otro_origen_nunca_recibe_el_token(api, section):
    api.set(REPOS, page([repo_body("facturas")], next_url="https://evil.example/x?page=2"))

    section("repositories")

    assert all(call.url.host == "api.github.com" for call in api.calls)


def test_enlace_relativo_al_mismo_prefijo_con_query_se_sigue(api, section):
    api.set(
        REPOS,
        paged_repos(
            {"1": [repo_body("facturas")], "2": [repo_body("infra")]},
            link_for=lambda number: f"{API}{REPOS}?type=all&per_page=100&page={number}",
        ),
    )

    assert names(section("repositories")) == ["facturas", "infra"]


def test_lista_envuelta_se_pagina_por_su_clave(api, section):
    base = fixture("installations")["installations"][0]

    def installations(request):
        number = request.url.params.get("page", "1")
        body = {
            "total_count": 2,
            "installations": [{**base, "app_slug": f"app-{number}"}],
        }
        link = next_link(f"/orgs/{ORG}/installations", 2) if number == "1" else None
        return page(body, next_url=link)(request)

    api.set(f"/orgs/{ORG}/installations", installations)

    result = section("app_installations")

    assert [entry["app_slug"] for entry in result["items"]] == ["app-1", "app-2"]


def test_lista_envuelta_sin_su_clave_es_error(api, section):
    api.set(f"/orgs/{ORG}/installations", {"total_count": 0})

    result = section("app_installations")

    assert result["status"] == "ERROR"
    assert result["reason"] == "respuesta sin lista de resultados"


def test_endpoint_que_devuelve_un_objeto_en_vez_de_lista_es_error(api, section):
    api.set(REPOS, {"message": "no es una lista"})

    assert section("repositories")["status"] == "ERROR"


def test_tope_de_paginas_es_error_y_no_pide_mas(api, section):
    def endless(request):
        number = int(request.url.params.get("page", "1"))
        return page([repo_body("facturas")], next_url=next_link(REPOS, number + 1))(request)

    api.set(REPOS, endless)

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["reason"] == "límite de páginas alcanzado"
    assert len(api.params(REPOS)) == MAX_PAGES


def test_la_paginacion_tambien_vale_para_lecturas_por_repo(api, section):
    def keys(request):
        number = request.url.params.get("page", "1")
        key = {"read_only": number == "1", "created_at": "2026-01-01T00:00:00Z"}
        link = next_link(f"{FACTURAS}/keys", 2) if number == "1" else None
        return page([key], next_url=link)(request)

    api.set(rf"{FACTURAS}/keys", keys)

    entry = next(e for e in section("deploy_keys")["items"] if e["repository"] == "facturas")

    assert entry["count"] == 2
    assert entry["writable_count"] == 1


def test_respuesta_no_json_en_una_pagina_es_error(api, section):
    api.set(REPOS, (200, "<html>proxy-CANARY</html>"))

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert "mal formada" in repositories["reason"]


CANONICAL_REPOS = r"/organizations/\d+/repos"


def test_enlace_canonico_por_id_de_organizacion_se_sigue(api, section):
    api.set(
        rf"{REPOS}|{CANONICAL_REPOS}",
        paged_repos(
            {"1": [repo_body("facturas")], "2": [repo_body("infra")]},
            link_for=lambda number: next_link("/organizations/456/repos", number) + "&type=all",
        ),
    )

    assert names(section("repositories")) == ["facturas", "infra"]
    assert "/organizations/456/repos" in api.paths()


def test_enlace_canonico_por_id_de_repositorio_se_sigue(api, section):
    def keys(request):
        number = request.url.params.get("page", "1")
        link = next_link("/repositories/1300192/keys", 2) if number == "1" else None
        return page([{"read_only": False, "created_at": "2026-01-01T00:00:00Z"}], next_url=link)(
            request
        )

    api.set(rf"{FACTURAS}/keys|/repositories/\d+/keys", keys)

    entry = next(e for e in section("deploy_keys")["items"] if e["repository"] == "facturas")

    assert entry["count"] == 2


def test_ciclo_a_la_primera_pagina_se_detecta_sin_peticion_extra(api, section):
    first = f"{API}{REPOS}?per_page=100&type=all"
    api.set(REPOS, page([repo_body("facturas")], next_url=first))

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["reason"] == "enlace de paginación no válido"
    assert len(api.params(REPOS)) == 1


def keys_pages(link_for, *, last: int = 2):
    def keys(request):
        number = int(request.url.params.get("page", "1"))
        link = link_for(number + 1) if number < last else None
        return page([{"read_only": False, "created_at": "2026-01-01T00:00:00Z"}], next_url=link)(
            request
        )

    return keys


def deploy_keys_count(section) -> int:
    return next(e for e in section("deploy_keys")["items"] if e["repository"] == "facturas")[
        "count"
    ]


def test_rama_con_barra_pagina_con_el_segmento_codificado(api, section):
    branch_path = f"/repos/{ORG}/facturas/rules/branches/release%2Fx"
    rule = {"type": "deletion", "ruleset_id": 1, "ruleset_source_type": "Repository"}

    def rules(request):
        assert request.url.raw_path.startswith(branch_path.encode())
        number = int(request.url.params.get("page", "1"))
        link = next_link(branch_path, 2) if number == 1 else None
        return page([rule], next_url=link)(request)

    api.set(rf"{FACTURAS}/rules/branches/.+", rules)
    api.set(rf"/orgs/{ORG}/repos", [repo_body("facturas", default_branch="release/x")])

    entry = next(e for e in section("branch_rules")["items"] if e["repository"] == "facturas")

    assert entry["effective_rules"]["branch"] == "release/x"
    assert len(entry["effective_rules"]["rules"]) == 2


def test_id_canonico_distinto_en_una_pagina_posterior_es_error(api, section):
    ids = {2: 1300192, 3: 999}
    api.set(
        rf"{FACTURAS}/keys|/repositories/\d+/keys",
        keys_pages(lambda number: next_link(f"/repositories/{ids[number]}/keys", number), last=3),
    )

    entry = next(e for e in section("deploy_keys")["items"] if e["repository"] == "facturas")

    assert entry["status"] == "ERROR"
    assert entry["reason"] == "enlace de paginación no válido"


def test_id_canonico_igual_en_todas_las_paginas_se_sigue(api, section):
    api.set(
        rf"{FACTURAS}/keys|/repositories/\d+/keys",
        keys_pages(lambda number: next_link("/repositories/1300192/keys", number), last=3),
    )

    assert deploy_keys_count(section) == 3


def test_filtro_cambiado_en_la_pagina_siguiente_es_error(api, section):
    api.set(
        REPOS,
        paged_repos(
            {"1": [repo_body("facturas")], "2": [repo_body("infra")]},
            link_for=lambda number: f"{API}{REPOS}?type=public&per_page=100&page={number}",
        ),
    )

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["reason"] == "enlace de paginación no válido"


LINK_WITHOUT_FILTER = f"{API}%s?per_page=100&page=2"
FIRST_PAGE = [{"login": "ana"}]


def test_enlace_que_omite_el_filtro_de_miembros_sin_2fa_es_error(api, section):
    path = f"/orgs/{ORG}/members"
    api.set(path, page(FIRST_PAGE, next_url=LINK_WITHOUT_FILTER % path))

    summary = section("organization")["summary"]["members_without_two_factor"]

    assert summary["status"] == "ERROR"
    assert summary["reason"] == "enlace de paginación no válido"
    assert len(api.params(path)) == 1


def test_enlace_que_omite_el_filtro_de_tipo_de_repositorios_es_error(api, section):
    api.set(REPOS, page([repo_body("facturas")], next_url=LINK_WITHOUT_FILTER % REPOS))

    repositories = section("repositories")

    assert repositories["status"] == "ERROR"
    assert repositories["reason"] == "enlace de paginación no válido"
    assert len(api.params(REPOS)) == 1


def test_enlace_que_omite_affiliation_de_colaboradores_es_error(api, section):
    path = f"{FACTURAS}/collaborators"
    api.set(path, page(FIRST_PAGE, next_url=LINK_WITHOUT_FILTER % path))

    entry = next(e for e in section("collaborators")["items"] if e["repository"] == "facturas")

    assert entry["status"] == "ERROR"
    assert entry["reason"] == "enlace de paginación no válido"
    assert len(api.params(path)) == 1


def test_enlace_que_omite_includes_parents_de_rulesets_es_error(api, section):
    path = f"{FACTURAS}/rulesets"
    api.set(path, page([{"id": 1}], next_url=LINK_WITHOUT_FILTER % path))

    entry = next(e for e in section("branch_rules")["items"] if e["repository"] == "facturas")

    assert entry["rulesets"]["status"] == "ERROR"
    assert entry["rulesets"]["reason"] == "enlace de paginación no válido"
    assert len(api.params(path)) == 1
