"""Asistente de riesgos y aplicabilidad: formularios que solo ofrecen valores válidos."""

import logging
import re
from datetime import datetime

import pytest
import yaml

import ens_evidence.ui.wizard as wizard
from ens_evidence.applicability import (
    DeclarationState,
    declaration_status,
    validate_applicability,
)
from ens_evidence.applicability.propose import decide
from ens_evidence.catalog.model import Dimension
from ens_evidence.models.enums import Applicability, Role
from ens_evidence.risk import RiskState, risk_status
from ens_evidence.risk.models import METHODOLOGY_KEY, METHODOLOGY_MD_KEY, REGISTER_KEY
from ens_evidence.risk.templates import METHODOLOGY_MD
from ens_evidence.scope import approve_scope
from ens_evidence.scope.sync import MEASURES_KEY
from ens_evidence.ui.wizard.applicability import APPLICABILITY_STEPS
from ens_evidence.ui.wizard.risk import RISK_STEPS, plain_errors

from .conftest import NOW, snapshot_of

CLI_ORDER = re.compile(r"ens-evidence|--confirm|risk init|controls init|applicability propose")
REASON = "No tenemos usuarios externos: el sistema solo lo usa el equipo interno."


@pytest.fixture(autouse=True)
def mounted_steps(monkeypatch):
    steps = (*wizard.STEPS, *RISK_STEPS, *APPLICABILITY_STEPS)
    monkeypatch.setattr(wizard, "STEPS", steps)
    monkeypatch.setattr(wizard, "BY_SLUG", {s.slug: s for s in steps})


@pytest.fixture
def basic(session, example_store):
    """Alcance aprobado y todo valorado BAJO: categoría BÁSICA, con medidas sin exigir."""
    approve_scope(example_store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    post_ok(session, "informacion", {"info_id": "datos", "description": "Datos de clientes"})
    form = {}
    for kind, element in (("information", "datos"), ("service", "svc-facturacion")):
        for dimension in Dimension:
            base = f"rating~{kind}~{element}~{dimension.value}"
            form |= {f"{base}~level": "BAJO", f"{base}~justification": "Perjuicio limitado"}
        form[f"valued_by~{kind}~{element}"] = "Ana Ruiz"
    post_ok(session, "valoraciones", form)
    post_ok(session, "aprobacion-categorizacion", {"accion": "aprobar", "approved_by": "Marta Gil"})
    return session


def post_ok(session, step: str, data: dict):
    response = session.post(f"/asistente/{step}", data)
    assert response.status_code == 303, response.text
    return response


def adopt(session):
    post_ok(
        session,
        "riesgos-metodologia",
        {
            "name": "MAGERIT v3",
            "reference": "Guía MAGERIT",
            "adaptation": "Escalas de tres niveles",
        },
    )


def risk_form(**overrides) -> dict:
    form = {
        "accion": "anadir-riesgo",
        "risk_id": "R-001",
        "threat": "Acceso no autorizado a las facturas",
        "risk_assets": ["app-facturas"],
        "risk_services": ["svc-facturacion"],
        "risk_dimensions": ["C"],
        "valuation~C": "ALTO",
        "likelihood": "media",
        "impact": "alto",
        "safeguards": ["op.acc.1"],
        "residual": "bajo",
        "treatment": "mitigate",
        "owner": "Ana Ruiz",
    }
    return form | overrides


def document(store, key):
    return yaml.safe_load(store.read_text(key))


def measures(store) -> dict:
    return {m["id"]: m for m in document(store, MEASURES_KEY)["measures"]}


# --- metodología -------------------------------------------------------------------------------


def test_metodologia_se_explica_y_muestra_las_escalas(completed):
    page = completed.get("/asistente/riesgos-metodologia")

    assert page.status_code == 200
    assert "14.2" in page.text and "metodología reconocida" in page.text
    assert "Riesgo inherente" in page.text


def test_adoptar_la_metodologia_escribe_el_yaml_y_documenta_el_md(completed, example_store):
    adopt(completed)

    doc = document(example_store, METHODOLOGY_KEY)
    assert (doc["adopted"], doc["name"]) == (True, "MAGERIT v3")
    md = example_store.read_text(METHODOLOGY_MD_KEY)
    assert md != METHODOLOGY_MD and "MAGERIT v3" in md


def test_metodologia_exige_los_tres_campos_y_no_escribe(completed, example_store):
    before = snapshot_of(example_store)

    response = completed.post("/asistente/riesgos-metodologia", {"name": "X"})

    assert response.status_code == 422
    assert 'id="err-reference"' in response.text and 'id="err-adaptation"' in response.text
    assert snapshot_of(example_store) == before


# --- riesgos -----------------------------------------------------------------------------------


def test_riesgos_pide_adoptar_la_metodologia_primero(completed):
    assert "Antes de añadir riesgos" in completed.get("/asistente/riesgos").text

    response = completed.post("/asistente/riesgos", risk_form())

    assert response.status_code == 422
    assert "Primero adopta la metodología" in response.text


def test_formulario_de_riesgos_solo_ofrece_activos_servicios_y_escalas_validos(completed):
    adopt(completed)

    page = completed.get("/asistente/riesgos").text

    assert 'name="risk_assets" value="lap-nuevo"' in page
    assert 'name="risk_assets" value="lap-personal"' not in page
    assert 'name="risk_services" value="svc-soporte"' in page
    assert 'name="safeguards" value="op.acc.1"' in page
    assert '<option value="media">' in page and '<option value="ALTO">' in page


def test_el_riesgo_inherente_lo_calcula_la_matriz(completed, example_store):
    adopt(completed)

    post_ok(completed, "riesgos", risk_form())

    risk = document(example_store, REGISTER_KEY)["risks"][0]
    assert risk["inherent"] == "alto"
    assert "acceptance" not in risk
    page = completed.get("/asistente/riesgos").text
    assert "Riesgo inherente" in page and "Acceso no autorizado a las facturas" in page


def test_un_residual_no_minimo_exige_aceptacion(completed, example_store):
    adopt(completed)
    before = snapshot_of(example_store)

    response = completed.post("/asistente/riesgos", risk_form(residual="medio"))

    assert response.status_code == 422
    assert 'id="err-accepted_by"' in response.text and 'id="err-accepted_role"' in response.text
    assert snapshot_of(example_store) == before

    post_ok(
        completed,
        "riesgos",
        risk_form(residual="medio", accepted_by="Ana Ruiz", accepted_role="system_owner"),
    )
    acceptance = document(example_store, REGISTER_KEY)["risks"][0]["acceptance"]
    assert (acceptance["accepted_by"], acceptance["role"]) == ("Ana Ruiz", "system_owner")
    assert datetime.fromisoformat(str(acceptance["accepted_at"])) == NOW


def test_valores_fuera_de_la_lista_se_rechazan_sin_escribir(completed, example_store):
    adopt(completed)
    before = snapshot_of(example_store)

    response = completed.post(
        "/asistente/riesgos",
        risk_form(likelihood="enorme", risk_assets=[], risk_services=[], **{"valuation~C": ""}),
    )

    assert response.status_code == 422
    for field in ("likelihood", "risk_assets", "valuation~C"):
        assert f'id="err-{field}"' in response.text
    assert snapshot_of(example_store) == before


def test_un_activo_inventado_se_rechaza_en_llano(completed):
    adopt(completed)

    response = completed.post("/asistente/riesgos", risk_form(risk_assets=["no-existe"]))

    assert response.status_code == 422
    assert "no existe en el inventario" in response.text
    assert not CLI_ORDER.search(response.text)


def test_acciones_y_quitar_respetan_las_dependencias(completed, example_store):
    adopt(completed)
    post_ok(completed, "riesgos", risk_form())
    action = {
        "accion": "anadir-accion",
        "action_id": "A-001",
        "action_risk": "R-001",
        "action_description": "Activar el doble factor",
        "action_owner": "Ana Ruiz",
        "action_due": "2026-12-31",
        "action_status": "pending",
    }
    bad_date = completed.post("/asistente/riesgos", action | {"action_due": "mañana"})
    assert bad_date.status_code == 422 and 'id="err-action_due"' in bad_date.text

    post_ok(completed, "riesgos", action)
    blocked = completed.post("/asistente/riesgos", {"accion": "quitar-riesgo", "item_id": "R-001"})
    assert blocked.status_code == 422 and "quítalas antes" in blocked.text

    post_ok(completed, "riesgos", {"accion": "quitar-accion", "item_id": "A-001"})
    post_ok(completed, "riesgos", {"accion": "quitar-riesgo", "item_id": "R-001"})
    assert document(example_store, REGISTER_KEY)["risks"] == []


def test_cada_escritura_de_riesgos_deja_su_linea_de_auditoria(completed, caplog):
    adopt(completed)
    with caplog.at_level(logging.INFO, logger="ens_evidence.ui.audit"):
        post_ok(completed, "riesgos", risk_form())

    line = next(r.message for r in caplog.records if "step=riesgos " in r.message)
    assert "risk/register.yaml" in line and "identity=ana@example.com" in line


# --- aprobación de los riesgos -----------------------------------------------------------------


def test_aprobar_riesgos_lista_lo_que_falta_y_deja_vacio_quien_aprueba(completed, example_store):
    page = completed.get("/asistente/aprobacion-riesgos").text

    assert 'name="approved_by" value=""' in page
    assert "Antes de aprobar" in page and " disabled" in page
    response = completed.post(
        "/asistente/aprobacion-riesgos", {"accion": "aprobar", "approved_by": "Marta Gil"}
    )
    assert response.status_code == 422 and not CLI_ORDER.search(response.text)
    assert risk_status(example_store, NOW).state is RiskState.NOT_APPROVED


def approved_risks(session):
    adopt(session)
    post_ok(session, "riesgos", risk_form())
    post_ok(session, "aprobacion-riesgos", {"accion": "aprobar", "approved_by": "Marta Gil"})


def test_aprobar_riesgos_como_responsable_de_la_seguridad(completed, example_store):
    approved_risks(completed)

    status = risk_status(example_store, NOW)
    assert status.state is RiskState.APPROVED
    assert status.approval.approved_by == "Marta Gil"
    assert status.approval.role.value == "security_officer"
    assert "Marta Gil" in completed.get("/asistente/aprobacion-riesgos").text


# --- aplicabilidad -----------------------------------------------------------------------------


def test_aplicabilidad_sin_alcance_aprobado_lo_dice_en_llano(session):
    page = session.get("/asistente/aplicabilidad")

    assert page.status_code == 200
    assert "alcance no está aprobado" in page.text


def test_aplicabilidad_sin_medidas_ofrece_preparar_y_ver_no_escribe(completed, example_store):
    before = snapshot_of(example_store)

    page = completed.get("/asistente/aplicabilidad")

    assert "Preparar las medidas" in page.text
    assert snapshot_of(example_store) == before


def prepared(session):
    post_ok(session, "aplicabilidad", {"accion": "preparar"})
    return session


def test_preparar_crea_las_medidas_y_propone_lo_que_exige_el_anexo(basic, example_store):
    prepared(basic)

    by_id = measures(example_store)
    proposed = [m for m in by_id.values() if m.get("proposed")]
    undecided = [m for m in by_id.values() if m["applicability"] == "UNDETERMINED"]
    assert proposed and undecided
    assert not any(m["applicability"] == "NOT_APPLICABLE" for m in by_id.values())


def test_las_exigidas_se_muestran_plegadas_y_las_pendientes_con_su_formulario(
    completed,
):
    prepared(completed)

    page = completed.get("/asistente/aplicabilidad").text

    assert "Pendientes de tu decisión" in page
    assert re.search(r"<details class=\"section\">\s*<summary><h3>Exigidas por el anexo II", page)
    assert 'name="justification"' in page and "«no aplica» o «n/a» no vale" in page


def form_of(page: str, measure_id: str):
    return re.search(rf'name="measure_id" value="{re.escape(measure_id)}">(.*?)</form>', page, re.S)


def first_pending(session, state: str = "undecided") -> str:
    from ens_evidence.ui.wizard.applicability import _medidas_context
    from ens_evidence.ui.wizard.base import StepInput

    store = session._client.app.state.store
    context = _medidas_context(StepInput(store=store, now=NOW, email="x"))
    return next(r["id"] for r in context["pending"] if r["state"] == state)


def test_no_aplica_con_razon_generica_se_rechaza_sin_escribir(basic, example_store):
    prepared(basic)
    measure_id = first_pending(basic)
    before = snapshot_of(example_store)

    for reason in ("n/a", "No aplica.", "porque no"):
        response = basic.post(
            "/asistente/aplicabilidad",
            {
                "accion": "decidir",
                "measure_id": measure_id,
                "decision": "not_applicable",
                "justification": reason,
            },
        )
        assert response.status_code == 422
        assert "razón propia" in response.text
    assert snapshot_of(example_store) == before


def test_no_aplica_con_razon_propia_queda_decidida(basic, example_store):
    prepared(basic)
    measure_id = first_pending(basic)

    post_ok(
        basic,
        "aplicabilidad",
        {
            "accion": "decidir",
            "measure_id": measure_id,
            "decision": "not_applicable",
            "justification": REASON,
        },
    )

    decided = measures(example_store)[measure_id]
    assert (decided["applicability"], decided["justification"]) == ("NOT_APPLICABLE", REASON)
    assert decided["selection"] is not None
    assert first_pending(basic) != measure_id


def test_una_exigida_no_ofrece_no_aplica_ni_se_acepta_por_la_puerta_de_atras(
    completed, example_store
):
    prepared(completed)
    required_id = next(i for i, m in measures(example_store).items() if m.get("proposed"))
    before = snapshot_of(example_store)

    response = completed.post(
        "/asistente/aplicabilidad",
        {
            "accion": "decidir",
            "measure_id": required_id,
            "decision": "not_applicable",
            "justification": REASON,
        },
    )

    assert response.status_code == 422
    assert snapshot_of(example_store) == before
    page = completed.get("/asistente/aplicabilidad").text
    form = re.search(
        rf'name="measure_id" value="{re.escape(required_id)}">(.*?)</form>', page, re.S
    )
    assert form and 'value="not_applicable"' not in form.group(1)


def test_medida_desconocida_se_rechaza(completed):
    prepared(completed)

    response = completed.post(
        "/asistente/aplicabilidad",
        {
            "accion": "decidir",
            "measure_id": "xx.nope.9",
            "decision": "applies",
            "justification": "x",
        },
    )

    assert response.status_code == 422 and "no existe en el catálogo" in response.text


def test_el_refuerzo_alternativo_pendiente_se_elige_en_el_formulario(completed, example_store):
    prepared(completed)
    measure_id = first_pending(completed, "reinforcement")
    group = measures(example_store)[measure_id]["selection"]["alternatives"][0]
    page = completed.get("/asistente/aplicabilidad").text
    assert f'name="reinforcement~0" value="{group[0]}"' in page

    post_ok(
        completed,
        "aplicabilidad",
        {"accion": "decidir", "measure_id": measure_id, "decision": "applies",
         "reinforcement~0": group[1]},
    )  # fmt: skip

    chosen = measures(example_store)[measure_id]["chosen_reinforcements"]
    assert chosen == [group[1]]


def test_un_refuerzo_ajeno_al_grupo_se_rechaza(completed, example_store):
    prepared(completed)
    measure_id = first_pending(completed, "reinforcement")
    before = snapshot_of(example_store)

    response = completed.post(
        "/asistente/aplicabilidad",
        {"accion": "decidir", "measure_id": measure_id, "decision": "applies",
         "reinforcement~0": "R99"},
    )  # fmt: skip

    assert response.status_code == 422
    assert snapshot_of(example_store) == before


def absent_candidate(store) -> str:
    return next(
        i for i in ("op.acc.5", "mp.eq.4", "mp.info.1", "mp.s.1") if measures(store)[i]["proposed"]
    )


def test_ausencia_de_activo_solo_donde_la_lista_cerrada_lo_permite(completed, example_store):
    prepared(completed)
    allowed = absent_candidate(example_store)
    page = completed.get("/asistente/aplicabilidad").text
    chunk = form_of(page, allowed)
    assert chunk and 'value="absent"' in chunk.group(1)
    not_in_list = next(
        i for i, m in measures(example_store).items() if m.get("proposed") and i.startswith("org.")
    )
    other = re.search(
        rf'name="measure_id" value="{re.escape(not_in_list)}">(.*?)</form>', page, re.S
    )
    assert other and 'value="absent"' not in other.group(1)
    before = snapshot_of(example_store)
    forced = completed.post(
        "/asistente/aplicabilidad",
        {"accion": "decidir", "measure_id": not_in_list, "decision": "absent",
         "absent_asset_type": "usuarios externos", "justification": REASON},
    )  # fmt: skip
    assert forced.status_code == 422 and snapshot_of(example_store) == before


def test_ausencia_de_activo_se_registra_y_se_lista_antes_de_firmar(completed, example_store):
    prepared(completed)
    measure_id = absent_candidate(example_store)
    asset_type = absent_asset_type(measure_id)

    post_ok(
        completed,
        "aplicabilidad",
        {"accion": "decidir", "measure_id": measure_id, "decision": "absent",
         "absent_asset_type": asset_type, "justification": REASON},
    )  # fmt: skip

    decided = measures(example_store)[measure_id]
    assert decided["applicability"] == "NOT_APPLICABLE"
    assert decided["absent_assets"] == {"asset_type": asset_type, "justification": REASON}
    page = completed.get("/asistente/aprobacion-aplicabilidad").text
    assert "Exclusiones por ausencia de activo" in page and REASON in page and asset_type in page


def absent_asset_type(measure_id: str) -> str:
    from ens_evidence.applicability.absent import absent_asset_types

    return absent_asset_types()[measure_id][0]


def test_compensacion_exige_sus_tres_partes(completed, example_store):
    prepared(completed)
    required_id = next(i for i, m in measures(example_store).items() if m.get("proposed"))
    before = snapshot_of(example_store)

    response = completed.post(
        "/asistente/aplicabilidad",
        {"accion": "decidir", "measure_id": required_id, "decision": "applies",
         "comp_measures": "Revisión semanal por el responsable"},
    )  # fmt: skip

    assert response.status_code == 422
    assert "Marca qué obligaciones" in response.text and "protegen igual o mejor" in response.text
    assert snapshot_of(example_store) == before


def test_compensacion_completa_se_registra(completed, example_store):
    prepared(completed)
    measure_id = next(
        i
        for i, m in measures(example_store).items()
        if m.get("proposed") and not m["selection"]["alternatives"]
    )

    post_ok(
        completed,
        "aplicabilidad",
        {"accion": "decidir", "measure_id": measure_id, "decision": "applies",
         "comp_measures": "Revisión semanal por el responsable", "comp_replaces": ["base"],
         "comp_justification": "La revisión semanal detecta lo mismo en menos tiempo."},
    )  # fmt: skip

    compensation = measures(example_store)[measure_id]["compensation"]
    assert compensation["replaces"] == ["base"]


def test_errores_por_htmx_se_devuelven_en_el_mismo_paso(completed):
    response = completed.post("/asistente/riesgos-metodologia", {"name": ""}, htmx=True)

    assert response.status_code == 200
    assert "No se ha guardado" in response.text


# --- firmar la Declaración ---------------------------------------------------------------------


def decide_everything_pending(store):
    """Decide por la API lo que queda sin decidir, para llegar a la firma."""
    for measure_id, m in measures(store).items():
        if m["applicability"] == "UNDETERMINED":
            decide(store, NOW, measure_id, Applicability.NOT_APPLICABLE, REASON)


def test_la_firma_pide_arreglar_lo_pendiente_y_no_usa_ordenes_de_cli(completed, example_store):
    prepared(completed)

    page = completed.get("/asistente/aprobacion-aplicabilidad").text
    response = completed.post(
        "/asistente/aprobacion-aplicabilidad", {"accion": "aprobar", "approved_by": "Marta Gil"}
    )

    assert "Antes de firmar" in page and not CLI_ORDER.search(page)
    assert response.status_code == 422 and not CLI_ORDER.search(response.text)
    assert declaration_status(example_store, NOW).state is DeclarationState.NOT_APPROVED


def test_flujo_completo_hasta_la_firma_de_la_declaracion(completed, example_store):
    approved_risks(completed)
    prepared(completed)
    # las salvaguardas del riesgo y los refuerzos alternativos, decididos por la web
    while True:
        try:
            measure_id = first_pending(completed, "reinforcement")
        except StopIteration:
            break
        group = measures(example_store)[measure_id]["selection"]["alternatives"]
        form = {"accion": "decidir", "measure_id": measure_id, "decision": "applies"}
        form |= {f"reinforcement~{i}": g[0] for i, g in enumerate(group)}
        post_ok(completed, "aplicabilidad", form)
    decide_everything_pending(example_store)

    assert not validate_applicability(example_store, NOW).errors
    post_ok(
        completed, "aprobacion-aplicabilidad", {"accion": "aprobar", "approved_by": "Marta Gil"}
    )

    status = declaration_status(example_store, NOW)
    assert status.state is DeclarationState.CURRENT
    assert status.declaration.approved_by == "Marta Gil"
    assert status.declaration.role.value == "security_officer"


def test_un_correo_en_la_firma_de_la_declaracion_se_rechaza_y_no_escribe(completed, example_store):
    approved_risks(completed)
    prepared(completed)
    decide_everything_pending(example_store)

    response = completed.post(
        "/asistente/aprobacion-aplicabilidad",
        {"accion": "aprobar", "approved_by": "marta@example.com"},
    )

    assert response.status_code == 422 and "indica solo un nombre" in response.text
    assert declaration_status(example_store, NOW).state is DeclarationState.NOT_APPROVED


# --- lenguaje llano ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "controls/measures.yaml › op.acc.1: decisión sin registrar; confírmala con "
        "`applicability propose --confirm op.acc.1`",
        "risk/methodology.yaml: no existe; créalo con risk init",
        "el alcance no está aprobado ahora (scope approve)",
        "controls/measures.yaml: falta la medida del catálogo; ejecuta controls init",
    ],
)
def test_los_motivos_salen_sin_ordenes_de_cli(raw):
    (plain,) = plain_errors([raw])

    assert not CLI_ORDER.search(plain) and "(scope approve)" not in plain and "`" not in plain
    assert plain[0].isupper()


def test_un_termino_entre_comillas_no_hace_perder_la_idea():
    (plain,) = plain_errors(
        ["op.acc.1: la justificación propuesta se editó; quita `proposed` para conservarla"]
    )

    assert "`" not in plain and "conservarla" in plain and "decídela otra vez" in plain


def test_los_pasos_exportados_tienen_las_rutas_fijadas():
    assert [s.slug for s in RISK_STEPS] == ["riesgos-metodologia", "riesgos", "aprobacion-riesgos"]
    assert [s.slug for s in APPLICABILITY_STEPS] == ["aplicabilidad", "aprobacion-aplicabilidad"]
