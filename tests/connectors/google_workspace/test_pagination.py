import httpx

from .fake_api import ANA, LUIS, MARTA, USERS, paginated, user

MAX_PAGES = 100


def ids(users: dict) -> list[str]:
    return [u["id"] for u in users["items"]]


def test_se_siguen_todas_las_paginas_y_se_envia_el_token_de_cada_una(api, section):
    api.set(USERS, paginated("users", [user(ANA)], [user(LUIS)], [user(MARTA)]))

    users = section("users")

    assert ids(users) == [ANA, LUIS, MARTA]
    assert [p.get("pageToken") for p in api.params(USERS)] == [None, "page-1", "page-2"]


def test_una_pagina_vacia_con_token_no_corta_la_paginacion(api, section):
    api.set(USERS, paginated("users", [], [user(ANA)], [], [user(LUIS)]))

    assert ids(section("users")) == [ANA, LUIS]
    assert len(api.api_calls(USERS)) == 4


def test_token_vacio_o_ausente_termina_la_paginacion(api, section):
    api.set(USERS, {"users": [user(ANA)], "nextPageToken": ""})

    assert ids(section("users")) == [ANA]
    assert len(api.api_calls(USERS)) == 1


def test_un_ciclo_de_tokens_invalida_la_seccion(api, section):
    api.set(USERS, {"users": [user(ANA)], "nextPageToken": "siempre-el-mismo"})

    users = section("users")

    assert users["status"] == "ERROR"
    assert users["reason"] == "token de paginación no válido"
    assert users["items"] == []
    assert len(api.api_calls(USERS)) == 2


def test_el_tope_de_paginas_invalida_la_seccion(api, section):
    counter = {"n": 0}

    def endless(request):
        counter["n"] += 1
        return httpx.Response(200, json={"users": [], "nextPageToken": f"t{counter['n']}"})

    api.set(USERS, endless)

    users = section("users")

    assert users["status"] == "ERROR"
    assert users["reason"] == "límite de páginas alcanzado"
    assert len(api.api_calls(USERS)) == MAX_PAGES


def test_un_token_desmesurado_o_de_tipo_incorrecto_invalida_la_seccion(api, section):
    for token in ("x" * 3000, 12345, ["a"]):
        api.set(USERS, {"users": [user(ANA)], "nextPageToken": token})

        users = section("users")

        assert users["status"] == "ERROR"
        assert users["reason"] == "token de paginación no válido"


def test_un_fallo_en_una_pagina_intermedia_descarta_las_anteriores(api, section):
    def second_fails(request):
        if request.url.params.get("pageToken") is None:
            return httpx.Response(200, json={"users": [user(ANA)], "nextPageToken": "page-1"})
        return httpx.Response(500, json={})

    api.set(USERS, second_fails)

    users = section("users")

    assert users["status"] == "ERROR"
    assert users["items"] == []


def test_una_lista_que_no_es_lista_invalida_la_seccion(api, section):
    api.set(USERS, {"users": {"id": ANA}})

    users = section("users")

    assert users["status"] == "ERROR"
    assert users["reason"] == "respuesta sin lista de resultados"
