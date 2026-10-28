import httpx
import pytest

from .fake_api import FACTURAS, ORG, REPO, denied, message
from .test_permissions import item

PRIMARY = (403, message("API rate limit exceeded"), {"x-ratelimit-remaining": "0"})
PRIMARY_SIN_TEXTO = (403, message("Forbidden"), {"x-ratelimit-remaining": "0"})
SECONDARY = (
    403,
    message("You have exceeded a secondary rate limit. Please wait."),
    {"x-ratelimit-remaining": "4000"},
)
RETRY_AFTER = (403, message("Forbidden"), {"retry-after": "60", "x-ratelimit-remaining": "4000"})
TOO_MANY = (429, message("Too many requests"), {"x-ratelimit-remaining": "4000"})
LIMITS = [PRIMARY, PRIMARY_SIN_TEXTO, SECONDARY, RETRY_AFTER, TOO_MANY]
IDS = ["primario", "primario-sin-texto", "secundario-con-cuota", "retry-after", "429"]


@pytest.mark.parametrize("limit", LIMITS, ids=IDS)
def test_limite_de_peticiones_en_una_lectura_por_repo_es_error_nunca_permiso_denegado(
    api, section, limit
):
    api.set(rf"{FACTURAS}/keys", limit)

    result = section("deploy_keys")

    assert result["status"] == "ERROR"
    assert item(result)["status"] == "ERROR"
    assert item(result)["reason"] == f"HTTP {limit[0]}: límite de peticiones alcanzado"
    assert item(result, "infra")["status"] == "SUCCESS"


@pytest.mark.parametrize("limit", LIMITS, ids=IDS)
def test_limite_de_peticiones_en_el_inventario_deja_error_en_las_secciones_dependientes(
    api, collect, limit
):
    api.set(f"/orgs/{ORG}/repos", limit)

    sections = collect().data["sections"]

    for name in ("repositories", "secret_scanning", "branch_rules", "dependabot", "environments"):
        assert sections[name]["status"] == "ERROR", name


@pytest.mark.parametrize("limit", LIMITS, ids=IDS)
def test_limite_de_peticiones_en_dependabot_no_se_interpreta_como_ausencia(api, section, limit):
    api.set(rf"{REPO}/vulnerability-alerts", limit)

    entry = item(section("dependabot"))

    assert entry["status"] == "ERROR"
    assert entry["state"] == "unknown"


@pytest.mark.parametrize("limit", LIMITS, ids=IDS)
def test_limite_de_peticiones_en_la_proteccion_clasica_no_es_sin_proteccion(api, section, limit):
    api.set(rf"{REPO}/branches/main/protection", limit)

    classic = item(section("branch_rules"))["classic"]

    assert classic["status"] == "ERROR"
    assert classic["state"] == "unknown"


def test_403_sin_indicios_de_limite_con_cuota_restante_si_es_permiso_denegado(api, section):
    status, body = denied()
    api.set(rf"{FACTURAS}/keys", (status, body, {"x-ratelimit-remaining": "4999"}))

    assert item(section("deploy_keys"))["status"] == "PERMISSION_DENIED"


def test_limite_de_peticiones_a_mitad_de_paginacion_invalida_el_conjunto(api, section):
    def first_page_then_limit(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(403, json=PRIMARY[1], headers=PRIMARY[2])
        return httpx.Response(
            200,
            json=[{"read_only": True}],
            headers={"Link": f'<https://api.github.com{FACTURAS}/keys?page=2>; rel="next"'},
        )

    api.set(rf"{FACTURAS}/keys", first_page_then_limit)

    entry = item(section("deploy_keys"))

    assert entry["status"] == "ERROR"
    assert entry["count"] is None
