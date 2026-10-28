import pytest

from .fake_api import (
    FACTURAS,
    ORG,
    REPO,
    denied,
    fixture,
    message,
    not_found,
    repo_body,
    variant,
)

DENIED = "PERMISSION_DENIED"


def item(section: dict, repo: str = "facturas") -> dict:
    return next(entry for entry in section["items"] if entry["repository"] == repo)


@pytest.mark.parametrize(
    ("name", "pattern"),
    [
        ("dependabot", rf"{REPO}/vulnerability-alerts"),
        ("collaborators", rf"{REPO}/collaborators"),
        ("environments", rf"{REPO}/environments"),
        ("deploy_keys", rf"{REPO}/keys"),
    ],
)
def test_recurso_sin_permiso_queda_permission_denied_distinto_de_no_configurado(
    api, section, name, pattern
):
    api.set(pattern, denied())
    api.set(rf"{REPO}/dependabot/alerts", denied())

    result = section(name)

    assert result["status"] == DENIED
    assert all(entry["status"] == DENIED for entry in result["items"])
    assert all(entry["reason"] == "HTTP 403: permiso insuficiente" for entry in result["items"])


def test_dependabot_sin_permiso_no_es_desactivado(api, section):
    api.set(rf"{REPO}/vulnerability-alerts", denied())

    entry = item(section("dependabot"))

    assert entry["status"] == DENIED
    assert entry["state"] == "unknown"


def test_el_permiso_denegado_de_un_repo_no_contamina_a_los_demas(api, section):
    api.set(f"/repos/{ORG}/infra/keys", denied())

    result = section("deploy_keys")

    assert result["status"] == DENIED
    assert item(result, "infra")["status"] == DENIED
    assert item(result, "infra")["count"] is None
    assert item(result, "facturas")["status"] == "SUCCESS"
    assert item(result, "facturas")["count"] == 2


def test_la_lista_de_repositorios_sin_permiso_deja_las_secciones_dependientes_sin_demostrar(
    api, collect
):
    api.set(f"/orgs/{ORG}/repos", denied())

    sections = collect().data["sections"]

    assert sections["repositories"]["status"] == DENIED
    for name in ("secret_scanning", "branch_rules", "dependabot", "collaborators", "actions"):
        assert sections[name]["status"] == DENIED
        assert sections[name]["items"] == []
        assert "depende de repositories" in sections[name]["reason"]


def test_las_instalaciones_de_apps_sin_permiso_son_permission_denied(api, section):
    api.set(f"/orgs/{ORG}/installations", denied())

    result = section("app_installations")

    assert result["status"] == DENIED
    assert result["items"] == []


def test_miembros_sin_2fa_sin_permiso_no_es_cero_miembros(api, section):
    api.set(f"/orgs/{ORG}/members", denied())

    summary = section("organization")["summary"]["members_without_two_factor"]

    assert summary["status"] == DENIED
    assert summary["count"] is None


def test_requisito_2fa_no_visible_es_permission_denied_nunca_false(api, section):
    api.set(f"/orgs/{ORG}", variant("org", two_factor_requirement_enabled=None))

    organization = section("organization")

    assert organization["status"] == DENIED
    assert organization["summary"]["two_factor_requirement"]["status"] == DENIED
    assert organization["summary"]["two_factor_requirement"]["enabled"] is None


def test_requisito_2fa_null_es_permission_denied_nunca_false(api, section):
    api.set(f"/orgs/{ORG}", {**fixture("org"), "two_factor_requirement_enabled": None})

    requirement = section("organization")["summary"]["two_factor_requirement"]

    assert requirement["status"] == DENIED
    assert requirement["enabled"] is None


@pytest.mark.parametrize("value", [True, False])
def test_requisito_2fa_visible_se_recoge_tal_cual(api, section, value):
    api.set(f"/orgs/{ORG}", variant("org", two_factor_requirement_enabled=value))

    requirement = section("organization")["summary"]["two_factor_requirement"]

    assert requirement == {"status": "SUCCESS", "enabled": value}


def test_security_and_analysis_ausente_es_permission_denied_en_secret_scanning(api, section):
    api.set(f"/orgs/{ORG}/repos", [repo_body(security_and_analysis=None)])

    entry = item(section("secret_scanning"))

    assert entry["status"] == DENIED
    assert entry["state"] == "unknown"


def test_secret_scanning_no_disponible_es_not_supported_nunca_enabled(api, section):
    api.set(f"/orgs/{ORG}/repos", [repo_body(security_and_analysis={"advanced_security": {}})])

    result = section("secret_scanning")

    assert result["status"] == "NOT_SUPPORTED"
    assert item(result)["state"] == "unknown"
    assert item(result)["status"] == "NOT_SUPPORTED"


def test_secret_scanning_null_es_not_supported_nunca_enabled(api, section):
    api.set(
        f"/orgs/{ORG}/repos",
        [repo_body(security_and_analysis={"secret_scanning": None})],
    )

    entry = item(section("secret_scanning"))

    assert entry["status"] == "NOT_SUPPORTED"
    assert entry["state"] == "unknown"


@pytest.mark.parametrize("status", ["Enabled", "on", "", "unknown"])
def test_valor_inesperado_de_secret_scanning_es_error_nunca_enabled(api, section, status):
    api.set(
        f"/orgs/{ORG}/repos",
        [repo_body(security_and_analysis={"secret_scanning": {"status": status}})],
    )

    entry = item(section("secret_scanning"))

    assert entry["status"] == "ERROR"
    assert entry["state"] == "unknown"


@pytest.mark.parametrize("status", ["enabled", "disabled"])
def test_secret_scanning_solo_es_enabled_o_disabled_con_el_estado_explicito(api, section, status):
    api.set(
        f"/orgs/{ORG}/repos",
        [repo_body(security_and_analysis={"secret_scanning": {"status": status}})],
    )

    entry = item(section("secret_scanning"))

    assert entry == {"repository": "facturas", "status": "SUCCESS", "state": status}


def test_404_generico_de_proteccion_clasica_no_es_sin_proteccion(api, section):
    api.set(rf"{REPO}/branches/main/protection", not_found())

    classic = item(section("branch_rules"))["classic"]

    assert classic["status"] == DENIED
    assert classic["state"] == "unknown"


def test_404_branch_not_protected_es_la_unica_evidencia_de_rama_sin_proteccion(api, section):
    api.set(rf"{REPO}/branches/main/protection", (404, message("Branch not protected")))

    classic = item(section("branch_rules"))["classic"]

    assert classic == {"status": "SUCCESS", "state": "unprotected"}


@pytest.mark.parametrize("text", ["branch not protected", "Branch not protected.", "Not Found"])
def test_mensajes_404_que_no_son_el_positivo_exacto_no_demuestran_nada(api, section, text):
    api.set(rf"{REPO}/branches/main/protection", (404, message(text)))

    assert item(section("branch_rules"))["classic"]["state"] == "unknown"


def test_403_con_el_mensaje_de_branch_not_protected_no_demuestra_nada(api, section):
    api.set(rf"{REPO}/branches/main/protection", (403, message("Branch not protected")))

    assert item(section("branch_rules"))["classic"]["state"] == "unknown"


def test_204_de_vulnerability_alerts_es_dependabot_enabled(api, section):
    api.set(rf"{REPO}/vulnerability-alerts", (204, b""))

    entry = item(section("dependabot"))

    assert entry == {"repository": "facturas", "status": "SUCCESS", "state": "enabled"}
    assert not any(path.endswith("/dependabot/alerts") for path in api.paths())


def test_200_de_vulnerability_alerts_no_es_enabled(api, section):
    api.set(rf"{REPO}/vulnerability-alerts", (200, {}))

    entry = item(section("dependabot"))

    assert entry["status"] == "ERROR"
    assert entry["state"] == "unknown"


def test_404_generico_de_vulnerability_alerts_no_es_desactivado(api, section):
    api.set(rf"{REPO}/vulnerability-alerts", not_found())
    api.set(rf"{REPO}/dependabot/alerts", not_found())

    entry = item(section("dependabot"))

    assert entry["status"] == DENIED
    assert entry["state"] == "unknown"


def test_404_de_vulnerability_alerts_con_alertas_403_genericas_no_es_desactivado(api, section):
    api.set(rf"{REPO}/vulnerability-alerts", not_found())
    api.set(rf"{REPO}/dependabot/alerts", denied())

    entry = item(section("dependabot"))

    assert entry["status"] == DENIED
    assert entry["state"] == "unknown"


def test_403_dependabot_alerts_are_disabled_es_la_evidencia_negativa(api, section):
    api.set(rf"{FACTURAS}/vulnerability-alerts", not_found())
    api.set(
        rf"{FACTURAS}/dependabot/alerts",
        (403, message("Dependabot alerts are disabled for this repository.")),
    )

    entry = item(section("dependabot"))

    assert entry == {"repository": "facturas", "status": "SUCCESS", "state": "disabled"}


def test_404_de_vulnerability_alerts_con_alertas_200_son_respuestas_contradictorias(api, section):
    api.set(rf"{REPO}/vulnerability-alerts", not_found())
    api.set(rf"{REPO}/dependabot/alerts", (200, []))

    entry = item(section("dependabot"))

    assert entry["status"] == "ERROR"
    assert entry["state"] == "unknown"


def test_los_endpoints_de_dependabot_se_consultan_en_este_orden(api, section):
    api.set(rf"{FACTURAS}/vulnerability-alerts", not_found())
    api.set(
        rf"{FACTURAS}/dependabot/alerts",
        (403, message("Dependabot alerts are disabled for this repository.")),
    )

    section("dependabot")

    paths = [p for p in api.paths() if p.startswith(FACTURAS) and "alerts" in p]
    assert paths == [f"{FACTURAS}/vulnerability-alerts", f"{FACTURAS}/dependabot/alerts"]
