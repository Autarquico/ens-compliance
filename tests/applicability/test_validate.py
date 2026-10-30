import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.applicability import propose_applicability, validate_applicability
from ens_evidence.applicability.absent import absent_asset_errors, absent_asset_types
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import load_measures, save_measures
from ens_evidence.categorization import CategorizationNotCurrent
from ens_evidence.models.enums import Applicability
from ens_evidence.models.readiness import AbsentAssets, Compensation, Measure
from ens_evidence.scope import ScopeNotApproved, revoke_approval
from ens_evidence.scope.models import Asset, AssetType, Deployment

from .helpers import (
    ABSENT_REASON,
    LATER,
    NOW,
    absent,
    categorize,
    decide,
    decide_all_undetermined,
    measures_by_id,
    prepared_workspace,
    uniform,
)

MEASURES_KEY = "controls/measures.yaml"
COMPENSATION = Compensation(
    replaces=["base"],
    measures=["Segregación de red adicional con monitorización reforzada"],
    justification="Protege igual o mejor frente al riesgo de acceso indebido.",
)


@pytest.fixture
def proposed(workspace):
    propose_applicability(workspace, LATER)
    return workspace


@pytest.fixture
def decided(proposed):
    decide_all_undetermined(proposed)
    return proposed


def errors_of(store):
    return validate_applicability(store, LATER).errors


def test_la_aplicabilidad_pasa_si_toda_medida_tiene_estado_y_justificacion(decided):
    validation = validate_applicability(decided, LATER)

    assert validation.errors == []
    assert validation.measures is not None


def test_falla_mientras_queden_medidas_sin_determinar(proposed):
    errors = errors_of(proposed)

    assert any("sin determinar" in e for e in errors)
    assert all("sin determinar" in e or "chosen_reinforcements" in e for e in errors)


def test_falla_si_una_medida_decidida_no_tiene_justificacion(decided):
    decide(decided, "op.cont.2", Applicability.NOT_APPLICABLE, None)

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.cont.2" in errors[0]
    assert "falta la justificación" in errors[0]


def test_falla_si_la_justificacion_es_solo_espacios(decided):
    decide(decided, "op.cont.2", Applicability.NOT_APPLICABLE, "   ")

    assert any("falta la justificación" in e for e in errors_of(decided))


def test_falla_si_falta_una_medida_del_catalogo(decided):
    document = load_measures(decided)
    save_measures(decided, document.model_copy(update={"measures": document.measures[1:]}))

    errors = errors_of(decided)

    assert any(document.measures[0].id in e and "falta la medida" in e for e in errors)


def test_falla_si_la_version_del_catalogo_no_es_la_vigente(decided):
    document = yaml.safe_load(decided.read_text(MEASURES_KEY))
    document["catalog_version"]["snapshot_sha256"] = "b" * 64
    decided.write_text(MEASURES_KEY, yaml.safe_dump(document, allow_unicode=True))

    assert any("versión del catálogo" in e for e in errors_of(decided))


def test_una_medida_exigida_marcada_not_applicable_es_siempre_un_error(decided):
    decide(decided, "op.pl.1", Applicability.NOT_APPLICABLE, "Riesgo asumido por la dirección.")

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.pl.1" in errors[0]
    assert "no puede excluirse" in errors[0]


def test_una_medida_exigida_puede_reemplazarse_por_compensatorias(decided):
    decide(
        decided,
        "op.pl.1",
        Applicability.APPLICABLE,
        "Sustituida por compensatorias (art. 28.3).",
        compensation=COMPENSATION,
    )

    assert errors_of(decided) == []


def test_una_compensacion_sin_justificacion_es_un_error(decided):
    decide(decided, "op.pl.1", Applicability.APPLICABLE, "Sustituida.", compensation=COMPENSATION)
    document = yaml.safe_load(decided.read_text(MEASURES_KEY))
    entry = next(m for m in document["measures"] if m["id"] == "op.pl.1")
    entry["compensation"]["justification"] = ""
    decided.write_text(MEASURES_KEY, yaml.safe_dump(document, allow_unicode=True))

    errors = errors_of(decided)

    assert errors
    assert any("justificación" in e for e in errors)


def test_una_compensacion_sin_medidas_compensatorias_es_un_error(decided):
    decide(decided, "op.pl.1", Applicability.APPLICABLE, "Sustituida.", compensation=COMPENSATION)
    document = yaml.safe_load(decided.read_text(MEASURES_KEY))
    next(m for m in document["measures"] if m["id"] == "op.pl.1")["compensation"]["measures"] = []
    decided.write_text(MEASURES_KEY, yaml.safe_dump(document, allow_unicode=True))

    assert errors_of(decided)


def test_compensar_una_medida_que_el_anexo_no_exige_es_un_error(decided):
    decide(
        decided,
        "op.cont.2",
        Applicability.APPLICABLE,
        "Adicional.",
        compensation=COMPENSATION,
    )

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.cont.2" in errors[0]
    assert "compensation" in errors[0]


def test_una_medida_not_applicable_no_admite_compensacion(decided):
    document = yaml.safe_load(decided.read_text(MEASURES_KEY))
    entry = next(m for m in document["measures"] if m["id"] == "op.cont.2")
    entry["compensation"] = COMPENSATION.model_dump()
    decided.write_text(MEASURES_KEY, yaml.safe_dump(document, allow_unicode=True))

    assert errors_of(decided)


def test_una_medida_adicional_no_exigida_puede_seleccionarse(decided):
    decide(decided, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")

    assert errors_of(decided) == []


def test_si_cambia_la_categoria_la_decision_humana_queda_en_revision_pendiente(decided):
    decide(decided, "op.pl.1", Applicability.APPLICABLE, "Texto escrito por la organización.")
    propose_applicability(decided, LATER)
    assert errors_of(decided) == []

    categorize(decided, uniform("ALTO"), at=LATER)
    propose_applicability(decided, LATER)
    errors = errors_of(decided)

    assert any("op.pl.1" in e and "revisión pendiente" in e for e in errors)
    assert not any("op.cont.1" in e for e in errors)


def test_si_cambia_la_version_del_catalogo_la_decision_queda_en_revision_pendiente(decided):
    decide(decided, "op.pl.1", Applicability.APPLICABLE, "Texto escrito por la organización.")
    propose_applicability(decided, LATER)
    document = load_measures(decided)
    stale = measures_by_id(decided)["op.pl.1"].selection.model_copy(
        update={"catalog_snapshot_sha256": "c" * 64}
    )
    updated = [
        m.model_copy(update={"selection": stale}) if m.id == "op.pl.1" else m
        for m in document.measures
    ]
    save_measures(decided, document.model_copy(update={"measures": updated}))

    errors = errors_of(decided)

    assert any("op.pl.1" in e and "revisión pendiente" in e for e in errors)


def test_validate_exige_el_alcance_aprobado(decided):
    revoke_approval(decided, "cambio de alcance", NOW)

    with pytest.raises(ScopeNotApproved):
        validate_applicability(decided, LATER)


def test_validate_exige_la_categorizacion_vigente(decided):
    with pytest.raises(CategorizationNotCurrent):
        validate_applicability(decided, NOW.replace(year=NOW.year + 2))


def test_el_flujo_de_propuesta_y_decision_no_produce_ningun_estado_de_conformidad(decided):
    measures = measures_by_id(decided).values()

    assert {m.assessment.value for m in measures} == {"NOT_ASSESSED"}
    assert all(m.review is None and not m.evidence for m in measures)


ABSENT = ABSENT_REASON
EXTERNAL_USERS = absent("usuarios externos")


def test_op_acc_5_exigida_sin_usuarios_externos_es_valida_con_absent_assets(decided):
    decide(decided, "op.acc.5", Applicability.NOT_APPLICABLE, ABSENT, absent_assets=EXTERNAL_USERS)

    validation = validate_applicability(decided, LATER)

    assert validation.errors == []
    assert [(e.id, e.asset_type, e.justification) for e in validation.exclusions] == [
        ("op.acc.5", "usuarios externos", ABSENT)
    ]
    assert validation.exclusions[0].name == "Mecanismo de autenticación (usuarios externos)"


def test_op_acc_5_exigida_not_applicable_sin_absent_assets_es_un_error(decided):
    decide(decided, "op.acc.5", Applicability.NOT_APPLICABLE, ABSENT)

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.acc.5" in errors[0]
    assert "absent_assets" in errors[0]


@pytest.mark.parametrize(
    "value", ["", "   ", "N/A", "n/a", "na", "No aplica", "  NO APLICA. ", "-", "No hay usuarios"]
)
def test_la_justificacion_de_absent_assets_es_especifica(value):
    with pytest.raises(ValidationError, match="debe explicar por qué no existe"):
        absent("usuarios externos", value)


def test_absent_assets_solo_cabe_en_una_medida_not_applicable():
    with pytest.raises(ValidationError, match="absent_assets"):
        Measure(id="op.acc.5", applicability=Applicability.APPLICABLE, absent_assets=EXTERNAL_USERS)


@pytest.mark.parametrize("measure_id", ["org.1", "op.pl.1", "op.exp.1", "mp.per.3"])
def test_absent_assets_no_excluye_una_medida_organizativa_o_de_proceso(decided, measure_id):
    decide(decided, measure_id, Applicability.NOT_APPLICABLE, ABSENT, absent_assets=EXTERNAL_USERS)

    errors = errors_of(decided)

    assert any(
        measure_id in e and "no se refiere a un tipo de activo que pueda faltar" in e
        for e in errors
    )
    assert any("art. 28.3" in e for e in errors)


def test_absent_assets_con_texto_generico_no_excluye_una_medida_exigida(decided):
    generic = AbsentAssets.model_construct(asset_type="usuarios externos", justification="N/A")
    decide(decided, "op.acc.5", Applicability.NOT_APPLICABLE, "N/A", absent_assets=generic)

    assert errors_of(decided)


def test_absent_assets_declara_un_tipo_de_activo_de_la_lista_de_la_medida(decided):
    decide(
        decided, "op.acc.5", Applicability.NOT_APPLICABLE, ABSENT, absent_assets=absent("correo")
    )

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.acc.5" in errors[0]
    assert "usuarios externos" in errors[0]


@pytest.mark.parametrize(
    ("measure_id", "asset_type"),
    [
        ("mp.eq.3", "dispositivos portátiles"),
        ("mp.s.2", "servicios y aplicaciones web"),
        ("mp.sw.1", "desarrollo propio de aplicaciones"),
        ("op.ext.1", "servicios prestados por terceros"),
    ],
)
def test_absent_assets_no_excluye_lo_que_el_inventario_dice_que_existe(
    decided, measure_id, asset_type
):
    decide(
        decided,
        measure_id,
        Applicability.NOT_APPLICABLE,
        ABSENT,
        absent_assets=absent(asset_type),
    )

    errors = [e for e in errors_of(decided) if measure_id in e]

    assert any("el inventario dice que ese tipo de activo existe" in e for e in errors)


@pytest.mark.parametrize("measure_id", ["mp.si.1", "mp.si.5", "mp.info.3"])
def test_absent_assets_no_excluye_una_medida_que_no_trata_de_un_activo_que_pueda_faltar(
    decided, measure_id
):
    decide(
        decided,
        measure_id,
        Applicability.NOT_APPLICABLE,
        ABSENT,
        absent_assets=absent("soportes de información removibles"),
    )

    errors = [e for e in errors_of(decided) if measure_id in e]

    assert any("no se refiere a un tipo de activo que pueda faltar" in e for e in errors)


def test_un_alojamiento_de_codigo_desplegado_desmiente_la_exclusion_de_mp_sw_1():
    hosting = Asset(
        id="gh",
        type=AssetType.CODE_HOSTING,
        deployment=Deployment.DEPLOYED,
        account="taller",
        repos=["taller/app"],
        actions="activadas",
    )
    measure = Measure(
        id="mp.sw.1",
        applicability=Applicability.NOT_APPLICABLE,
        justification=ABSENT,
        absent_assets=absent("desarrollo propio de aplicaciones"),
    )

    errors = absent_asset_errors(measure, "mp.sw.1", [hosting])

    assert any("el inventario dice que ese tipo de activo existe" in e for e in errors)


def test_el_aviso_de_servidor_es_uno_por_servidor_con_todas_las_mp_if_excluidas(decided):
    for measure_id in ("mp.if.1", "mp.if.2"):
        decide(
            decided,
            measure_id,
            Applicability.NOT_APPLICABLE,
            ABSENT,
            absent_assets=absent("instalaciones propias que alberguen el sistema"),
        )

    validation = validate_applicability(decided, LATER)

    server_warnings = [w for w in validation.warnings if "servidor desplegado" in w]
    assert len(server_warnings) == 1
    assert "mp.if.1, mp.if.2" in server_warnings[0]


def test_un_servidor_desplegado_avisa_sin_invalidar_la_exclusion_de_mp_if(decided):
    decide(
        decided,
        "mp.if.1",
        Applicability.NOT_APPLICABLE,
        ABSENT,
        absent_assets=absent("instalaciones propias que alberguen el sistema"),
    )

    validation = validate_applicability(decided, LATER)

    assert [e for e in validation.errors if "mp.if.1" in e] == []
    assert any(
        "mp.if.1" in w
        and "hay un servidor desplegado (srv-app, Oficina, Gijón)" in w
        and "confirma que no está en instalaciones propias" in w
        for w in validation.warnings
    )


def test_la_lista_de_absent_assets_no_incluye_marco_ni_proceso():
    forbidden = ("org.", "op.pl.", "op.exp.", "op.cont.", "op.mon.", "mp.per.")

    assert not [i for i in absent_asset_types() if i.startswith(forbidden)]


def test_toda_medida_de_la_lista_de_absent_assets_esta_en_el_catalogo():
    known = {card.id for card in load_catalog().measures}

    assert set(absent_asset_types()) <= known


def test_absent_assets_no_excluye_una_medida_que_el_anexo_no_exige(decided):
    decide(decided, "op.cont.2", Applicability.NOT_APPLICABLE, ABSENT, absent_assets=EXTERNAL_USERS)

    errors = errors_of(decided)

    assert any("op.cont.2" in e and "solo excluye una medida exigida" in e for e in errors)


def test_una_compensacion_no_puede_describir_la_ausencia_de_medidas():
    with pytest.raises(ValidationError):
        Compensation(measures=[], justification="No hay usuarios externos.")


def test_un_refuerzo_alternativo_sin_elegir_es_un_error(decided):
    decide(decided, "op.acc.5", Applicability.APPLICABLE, "Seleccionada.", chosen_reinforcements=[])

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.acc.5" in errors[0]
    assert "R2, R3, R4" in errors[0]


def test_elegir_un_refuerzo_de_cada_grupo_es_valido(decided):
    decide(
        decided, "op.acc.5", Applicability.APPLICABLE, "Seleccionada.", chosen_reinforcements=["R3"]
    )

    assert errors_of(decided) == []


@pytest.mark.parametrize("chosen", [["R2", "R3"], ["R3", "R5"], ["R9"]])
def test_elegir_mas_de_uno_o_uno_ajeno_al_grupo_es_un_error(decided, chosen):
    decide(
        decided, "op.acc.5", Applicability.APPLICABLE, "Seleccionada.", chosen_reinforcements=chosen
    )

    assert any("op.acc.5" in e for e in errors_of(decided))


def write_register(store, safeguards):
    risk = {
        "id": "R-001",
        "assets": ["app-facturas"],
        "services": ["svc-facturacion"],
        "threat": "Parada prolongada del servicio",
        "dimensions": ["D"],
        "valuation": {"D": "ALTO"},
        "likelihood": "media",
        "impact": "alto",
        "inherent": "alto",
        "safeguards": safeguards,
        "residual": "bajo",
        "treatment": "mitigate",
        "owner": "Ana Ruiz",
    }
    store.write_text("risk/register.yaml", yaml.safe_dump({"risks": [risk]}))


def test_un_riesgo_con_una_salvaguarda_no_seleccionada_es_un_error(decided):
    write_register(decided, ["op.cont.2"])

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.cont.2" in errors[0]
    assert "R-001" in errors[0]


def test_un_riesgo_con_la_salvaguarda_seleccionada_es_valido(decided):
    decide(decided, "op.cont.2", Applicability.APPLICABLE, "Mitiga R-001.")
    write_register(decided, ["op.cont.2"])

    assert errors_of(decided) == []


def test_un_registro_de_riesgos_ilegible_impide_validar_las_salvaguardas(decided):
    decided.write_text("risk/register.yaml", "{: bad")

    assert any("risk/register.yaml" in e for e in errors_of(decided))


def test_sin_registro_de_riesgos_avisa_sin_fallar(decided):
    validation = validate_applicability(decided, LATER)

    assert validation.errors == []
    assert any("risk/register.yaml" in w for w in validation.warnings)


def test_una_decision_sin_fundamento_queda_en_revision_pendiente(decided):
    decide(
        decided, "op.pl.1", Applicability.APPLICABLE, "Texto de la organización.", selection=None
    )

    errors = errors_of(decided)

    assert len(errors) == 1
    assert "op.pl.1" in errors[0]
    assert "decisión sin registrar" in errors[0]
    assert "applicability propose --confirm op.pl.1" in errors[0]


def test_una_propuesta_editada_a_mano_con_proposed_activo_es_un_error(proposed):
    decide_all_undetermined(proposed)
    measure = measures_by_id(proposed)["op.pl.1"]
    edited = measure.model_copy(update={"proposed": True, "justification": "Reescrita."})
    document = load_measures(proposed)
    updated = [edited if m.id == "op.pl.1" else m for m in document.measures]
    save_measures(proposed, document.model_copy(update={"measures": updated}))

    assert any("op.pl.1" in e and "proposed" in e for e in errors_of(proposed))


def _compensate(store, measure_id: str, replaces: list[str], chosen: list[str]) -> None:
    decide(
        store,
        measure_id,
        Applicability.APPLICABLE,
        "Sustituida por compensatorias (art. 28.3).",
        compensation=COMPENSATION.model_copy(update={"replaces": replaces}),
        chosen_reinforcements=chosen,
    )


@pytest.fixture
def high_category(tmp_path):
    store = prepared_workspace(tmp_path / "ws-alta", uniform("ALTO"))
    propose_applicability(store, LATER)
    decide_all_undetermined(store)
    return store


def test_una_medida_compensada_integramente_no_exige_elegir_refuerzo_alternativo(high_category):
    _compensate(high_category, "mp.com.4", ["base", "R4", "R2"], [])

    assert [e for e in errors_of(high_category) if "mp.com.4" in e] == []


def test_una_compensacion_parcial_sigue_exigiendo_elegir_refuerzo_alternativo(high_category):
    _compensate(high_category, "mp.com.4", ["base"], [])

    errors = [e for e in errors_of(high_category) if "mp.com.4" in e]

    assert any("elige exactamente un refuerzo" in e for e in errors)


def test_la_compensacion_solo_sustituye_obligaciones_de_la_medida(high_category):
    _compensate(high_category, "mp.com.4", ["base", "R9"], [])

    errors = [e for e in errors_of(high_category) if "mp.com.4" in e]

    assert any("compensation.replaces: R9" in e for e in errors)


def test_la_compensacion_debe_decir_que_sustituye():
    with pytest.raises(ValidationError, match="replaces"):
        Compensation(measures=["x"], justification="y")


def test_un_sistema_con_discos_fijos_y_copias_sin_usb_no_excluye_mp_si_2_ni_mp_si_3(decided):
    for measure_id in ("mp.si.2", "mp.si.3", "mp.si.4"):
        decide(
            decided,
            measure_id,
            Applicability.NOT_APPLICABLE,
            ABSENT,
            absent_assets=absent("soportes de información removibles"),
        )

    errors = errors_of(decided)

    for measure_id in ("mp.si.2", "mp.si.3", "mp.si.4"):
        assert any(measure_id in e and "no se refiere a un tipo de activo" in e for e in errors)
