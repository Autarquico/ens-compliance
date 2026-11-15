import logging

import pytest

from ens_evidence.models.enums import CollectionStatus

from .fake_api import ACCOUNT, denied, envelope, failure, fixture
from .test_no_secrets import CANARY, artifact, log_text

IDP_PATH = f"/accounts/{ACCOUNT}/access/identity_providers"
APPS_PATH = f"/accounts/{ACCOUNT}/access/apps"


def providers(collect):
    return collect().data["sections"]["identity_providers"]


def test_solo_se_guardan_id_nombre_y_tipo_de_cada_proveedor(collect):
    section = providers(collect)

    assert section["status"] == CollectionStatus.SUCCESS
    assert section["items"] == [
        {"id": "idp-1", "name": "Entra pyme", "type": "azureAD"},
        {"id": "idp-2", "name": "Workspace", "type": "google-apps"},
    ]


def test_sin_permiso_es_permission_denied_con_mensaje_local(api, collect):
    status, body = denied()
    body["errors"][0]["message"] = "ext-CANARY"
    api.set(IDP_PATH, (status, body))

    section = providers(collect)

    assert section["status"] == CollectionStatus.PERMISSION_DENIED
    assert section["items"] == []
    assert section["reason"] == "sin permiso para leer los proveedores de identidad"


def test_sin_proveedores_es_success_con_lista_vacia(api, collect):
    api.set(IDP_PATH, envelope([], {"page": 1, "per_page": 1000, "total_pages": 1}))

    section = providers(collect)

    assert section["status"] == CollectionStatus.SUCCESS
    assert section["items"] == []


def test_un_fallo_en_una_pagina_posterior_no_deja_proveedores_parciales(api, collect):
    first = fixture("identity_providers")
    first["result_info"] = {"page": 1, "per_page": 1, "total_pages": 2}
    api.set(IDP_PATH, [first, (500, failure(1001, "boom-CANARY"))])

    section = providers(collect)

    assert section["status"] == CollectionStatus.ERROR
    assert section["items"] == []
    assert section["reason"] == "no se pudieron leer los proveedores de identidad"


def test_el_fallo_de_proveedores_no_degrada_el_resto_de_secciones(api, collect):
    api.set(IDP_PATH, (500, failure(1001, "boom")))

    sections = collect().data["sections"]

    assert sections["access_applications"]["status"] == CollectionStatus.SUCCESS
    assert sections["access_groups"]["status"] == CollectionStatus.SUCCESS


@pytest.mark.parametrize(
    "reply",
    [
        (500, {"success": False, "errors": [{"code": 1, "message": "e-CANARY"}], "result": None}),
        (502, "<html>proxy-CANARY</html>"),
        (200, "no es json CANARY"),
        (200, {"success": True, "errors": [], "result": {"leak": "CANARY"}}),
        (200, {"success": True, "errors": [], "result": [{"name": "sin id", "x": "CANARY"}]}),
        (200, {"success": True, "errors": [], "result": ["CANARY"]}),
    ],
    ids=["5xx", "html", "no-json", "result-no-lista", "item-sin-id", "item-no-objeto"],
)
def test_ningun_cuerpo_de_error_o_respuesta_malformada_llega_al_artefacto_ni_a_los_logs(
    api, collect, caplog, reply
):
    caplog.set_level(logging.DEBUG)
    api.set(IDP_PATH, reply)

    result = collect()

    assert result.data["sections"]["identity_providers"]["status"] == CollectionStatus.ERROR
    assert CANARY not in artifact(result)
    assert CANARY not in log_text(caplog)


def test_error_4xx_distinto_de_403_tampoco_guarda_el_cuerpo(api, collect):
    api.set(IDP_PATH, (400, failure(1003, "bad-CANARY")))

    result = collect()

    assert providers(lambda: result)["reason"] == "no se pudieron leer los proveedores de identidad"
    assert CANARY not in artifact(result)


def test_allowed_idps_distingue_ausente_null_vacio_lista_y_malformado(api, collect):
    def app(app_id, **extra):
        return {
            "id": app_id,
            "name": app_id,
            "type": "self_hosted",
            "domain": f"{app_id}.example",
            **extra,
        }

    api.set(
        APPS_PATH,
        envelope(
            [
                app("ausente"),
                app("nulo", allowed_idps=None),
                app("vacio", allowed_idps=[]),
                app("lista", allowed_idps=["idp-1", "idp-2"]),
                app("cadena", allowed_idps="idp-1"),
                app("mixta", allowed_idps=["idp-1", 7]),
                app("hostil", allowed_idps=["x y-CANARY"]),
            ],
            {"page": 1, "per_page": 50, "total_pages": 1},
        ),
    )

    items = collect().data["sections"]["access_applications"]["items"]

    observed = {i["id"]: (i["allowed_idps_form"], i["allowed_idps"]) for i in items}
    assert observed == {
        "ausente": ("absent", None),
        "nulo": ("null", None),
        "vacio": ("list", []),
        "lista": ("list", ["idp-1", "idp-2"]),
        "cadena": ("malformed", None),
        "mixta": ("malformed", None),
        "hostil": ("malformed", None),
    }


def test_selectores_de_politica_con_formas_raras_se_saneann(api, collect):
    api.set(
        f"/accounts/{ACCOUNT}/access/apps/app-1/policies",
        envelope(
            [
                {
                    "id": "p",
                    "decision": "allow",
                    "include": [
                        {"group": {"id": "grp-9"}},
                        {"group": "no-objeto"},
                        {"login_method": {}},
                        {"oidc": {"identity_provider_id": "no valido-CANARY"}},
                        {"email": {"email": "a-CANARY@x.invalid"}, "ip": {}},
                        "texto",
                    ],
                }
            ],
            {"page": 1, "per_page": 1000, "total_pages": 1},
        ),
    )

    policy = collect().data["sections"]["access_policies"]["items"][0]["policies"][0]

    assert policy["include_selectors"] == [
        {"kind": "group", "idp": None, "group": "grp-9"},
        {"kind": "group", "idp": None, "group": None},
        {"kind": "login_method", "idp": None, "group": None},
        {"kind": "oidc", "idp": None, "group": None},
        {"kind": "unknown", "idp": None, "group": None},
        {"kind": "unknown", "idp": None, "group": None},
    ]
