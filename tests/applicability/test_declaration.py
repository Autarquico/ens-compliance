import hashlib
from datetime import timedelta

import pytest
import yaml
from pydantic import ValidationError

from ens_evidence.applicability import (
    DECLARATION_KEY,
    Declaration,
    DeclarationRejected,
    DeclarationState,
    approve_declaration,
    declaration_status,
    propose_applicability,
)
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import current_catalog_version
from ens_evidence.categorization import (
    CATEGORIZATION_KEY,
    approve_categorization,
    categorization_status,
)
from ens_evidence.models.enums import Applicability, Category, Role
from ens_evidence.models.readiness import Compensation
from ens_evidence.scope import ScopeNotApproved, revoke_approval, scope_status

from .helpers import (
    ABSENT_REASON,
    LATER,
    NOW,
    absent,
    decide,
    decide_all_undetermined,
    measures_by_id,
)

MEASURES_KEY = "controls/measures.yaml"
SIGNED_AT = LATER + timedelta(hours=1)
COMPENSATION = Compensation(
    replaces=["base"],
    measures=["Segregación de red adicional con monitorización reforzada"],
    justification="Protege igual o mejor frente al riesgo de acceso indebido.",
)


@pytest.fixture
def ready(workspace):
    """Aplicabilidad propuesta y decidida; una exigida compensada y una adicional seleccionada."""
    propose_applicability(workspace, LATER)
    decide_all_undetermined(workspace)
    decide(workspace, "op.pl.1", Applicability.APPLICABLE, "Compensada.", compensation=COMPENSATION)
    decide(workspace, "op.cont.2", Applicability.APPLICABLE, "Seleccionada como adicional.")
    return workspace


@pytest.fixture
def signed(ready):
    return approve_declaration(ready, "Luis Pardo", SIGNED_AT)


def sha256(store, key):
    return hashlib.sha256(store.read_bytes(key)).hexdigest()


def stored(store):
    return yaml.safe_load(store.read_text(DECLARATION_KEY))


def reasons(store):
    return declaration_status(store, SIGNED_AT).errors


def test_sin_firmar_la_declaracion_no_esta_aprobada(ready):
    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.NOT_APPROVED


def test_la_declaracion_se_firma_si_la_aplicabilidad_es_valida(ready):
    declaration = approve_declaration(ready, "Luis Pardo", SIGNED_AT)

    assert declaration.role is Role.SECURITY_OFFICER
    assert declaration.approved_by == "Luis Pardo"
    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.CURRENT


def test_no_se_firma_si_la_aplicabilidad_no_es_valida_y_no_deja_fichero(workspace):
    propose_applicability(workspace, LATER)

    with pytest.raises(DeclarationRejected) as raised:
        approve_declaration(workspace, "Luis Pardo", SIGNED_AT)

    assert any("sin determinar" in e for e in raised.value.errors)
    assert not workspace.exists(DECLARATION_KEY)


def test_no_se_firma_con_una_exigida_not_applicable(ready):
    decide(ready, "op.pl.2", Applicability.NOT_APPLICABLE, "Asumido.")

    with pytest.raises(DeclarationRejected):
        approve_declaration(ready, "Luis Pardo", SIGNED_AT)


def test_no_se_firma_sin_alcance_aprobado(ready):
    revoke_approval(ready, "cambio de alcance", NOW)

    with pytest.raises(ScopeNotApproved):
        approve_declaration(ready, "Luis Pardo", SIGNED_AT)


@pytest.mark.parametrize("role", [r for r in Role if r is not Role.SECURITY_OFFICER])
def test_solo_el_responsable_de_la_seguridad_puede_firmar(signed, role):
    with pytest.raises(ValidationError):
        Declaration.model_validate({**signed.model_dump(mode="json"), "role": role.value})


def test_una_declaracion_guardada_con_otro_rol_es_invalida(ready, signed):
    ready.write_text(
        DECLARATION_KEY,
        yaml.safe_dump({**stored(ready), "role": Role.SYSTEM_OWNER.value}, allow_unicode=True),
    )

    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.INVALID


def test_la_declaracion_lista_las_medidas_seleccionadas_con_su_fundamento(ready, signed):
    measures = measures_by_id(ready)
    selected = {s.id: s for s in signed.selected}

    assert set(selected) == {
        i for i, m in measures.items() if m.applicability is Applicability.APPLICABLE
    }
    assert selected["op.cont.2"].required_by is None
    assert "categoría" in selected["op.pl.1"].required_by
    assert selected["op.pl.1"].required_reinforcements == ["R1"]


def test_la_declaracion_recoge_los_refuerzos_exigidos_y_las_alternativas(ready, signed):
    selected = {s.id: s for s in signed.selected}

    assert selected["op.acc.5"].required_reinforcements == ["R5"]
    assert selected["op.acc.5"].alternatives == [["R2", "R3", "R4"]]


def test_la_declaracion_justifica_cada_medida_no_seleccionada(ready, signed):
    measures = measures_by_id(ready)

    assert signed.not_selected
    for item in signed.not_selected:
        assert measures[item.id].applicability is Applicability.NOT_APPLICABLE
        assert item.justification == measures[item.id].justification
    assert {s.id for s in signed.selected}.isdisjoint(i.id for i in signed.not_selected)


def test_la_declaracion_dice_que_refuerzo_alternativo_adopta_la_organizacion(ready):
    decide(
        ready, "op.acc.5", Applicability.APPLICABLE, "Seleccionada.", chosen_reinforcements=["R3"]
    )

    signed = approve_declaration(ready, "Luis Pardo", SIGNED_AT)

    selected = {s.id: s for s in signed.selected}
    assert selected["op.acc.5"].chosen_reinforcements == ["R3"]


def test_la_declaracion_lista_aparte_lo_exigido_que_no_aplica_por_ausencia_de_activos(ready):
    reason = ABSENT_REASON
    decide(
        ready,
        "op.acc.5",
        Applicability.NOT_APPLICABLE,
        reason,
        absent_assets=absent("usuarios externos"),
    )

    signed = approve_declaration(ready, "Luis Pardo", SIGNED_AT)

    assert [r.id for r in signed.required_but_absent_assets] == ["op.acc.5"]
    record = signed.required_but_absent_assets[0]
    assert record.asset_type == "usuarios externos"
    assert record.justification == reason
    assert record.required_by == "las dimensiones C, I, T, A en nivel MEDIO"
    assert "op.acc.5" not in {s.id for s in signed.selected}
    assert "op.acc.5" not in {n.id for n in signed.not_selected}


def test_no_se_firma_con_un_riesgo_mitigado_por_una_medida_no_seleccionada(ready):
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
        "safeguards": ["op.cont.3"],
        "residual": "bajo",
        "treatment": "mitigate",
        "owner": "Ana Ruiz",
    }
    ready.write_text("risk/register.yaml", yaml.safe_dump({"risks": [risk]}))
    decide(ready, "op.cont.3", Applicability.NOT_APPLICABLE, "No se selecciona.")

    with pytest.raises(DeclarationRejected) as raised:
        approve_declaration(ready, "Luis Pardo", SIGNED_AT)

    assert any("op.cont.3" in e and "R-001" in e for e in raised.value.errors)


def test_la_declaracion_detalla_la_correspondencia_de_las_compensatorias(signed):
    assert len(signed.compensations) == 1
    record = signed.compensations[0]
    assert record.replaces == "op.pl.1"
    assert record.measures == COMPENSATION.measures
    assert record.obligations == COMPENSATION.replaces
    assert record.justification == COMPENSATION.justification


def test_la_declaracion_fija_los_hashes_la_categoria_la_version_y_la_huella(ready, signed):
    approval = categorization_status(ready, SIGNED_AT).approval

    assert signed.measures_sha256 == sha256(ready, MEASURES_KEY)
    assert signed.categorization_sha256 == approval.file_sha256
    assert signed.categorization_sha256 == sha256(ready, CATEGORIZATION_KEY)
    assert signed.category is Category.MEDIA
    assert signed.catalog_version == current_catalog_version(load_catalog())
    assert signed.scope_fingerprint == scope_status(ready).fingerprint
    assert Declaration.model_validate(stored(ready)) == signed


def test_la_declaracion_sigue_vigente_si_no_cambia_nada(ready, signed):
    status = declaration_status(ready, SIGNED_AT + timedelta(days=30))

    assert status.state is DeclarationState.CURRENT
    assert status.errors == []


def test_deja_de_estar_vigente_si_cambia_measures_yaml(ready, signed):
    decide(ready, "op.cont.3", Applicability.NOT_APPLICABLE, "Justificación reescrita.")

    status = declaration_status(ready, SIGNED_AT)

    assert status.state is DeclarationState.STALE
    assert any(MEASURES_KEY in r for r in status.errors)


def test_deja_de_estar_vigente_si_cambia_la_categorizacion_aprobada(ready, signed):
    ready.write_text(
        CATEGORIZATION_KEY,
        ready.read_text(CATEGORIZATION_KEY).replace("anexo I, 3.", "anexo I, 3 (revisado).", 1),
    )
    approve_categorization(ready, "Luis Pardo", SIGNED_AT)

    status = declaration_status(ready, SIGNED_AT)

    assert status.state is DeclarationState.STALE
    assert any("categorización cambió" in r for r in status.errors)


def test_deja_de_estar_vigente_si_la_categorizacion_deja_de_estar_vigente(ready, signed):
    status = declaration_status(ready, NOW + timedelta(days=400))

    assert status.state is DeclarationState.STALE
    assert any("categorización no está vigente" in r for r in status.errors)


def test_deja_de_estar_vigente_si_cambia_la_version_del_catalogo(ready, signed):
    document = stored(ready)
    document["catalog_version"]["snapshot_sha256"] = "d" * 64
    ready.write_text(DECLARATION_KEY, yaml.safe_dump(document, allow_unicode=True))

    status = declaration_status(ready, SIGNED_AT)

    assert status.state is DeclarationState.STALE
    assert any("versión del catálogo" in r for r in status.errors)


def test_deja_de_estar_vigente_si_cambia_la_huella_del_alcance(ready, signed):
    document = stored(ready)
    document["scope_fingerprint"] = "e" * 64
    ready.write_text(DECLARATION_KEY, yaml.safe_dump(document, allow_unicode=True))

    status = declaration_status(ready, SIGNED_AT)

    assert status.state is DeclarationState.STALE
    assert any("alcance cambió" in r for r in status.errors)


def test_deja_de_estar_vigente_si_se_edita_el_inventario_del_alcance(ready, signed):
    text = ready.read_text("scope/system.yaml")
    ready.write_text(
        "scope/system.yaml", text.replace("Sistema de Taller Norte", "Sistema nuevo", 1)
    )

    status = declaration_status(ready, SIGNED_AT)

    assert status.state is DeclarationState.STALE
    assert any("alcance" in r for r in status.errors)


def test_deja_de_estar_vigente_si_el_alcance_se_revoca(ready, signed):
    revoke_approval(ready, "cambio de alcance", SIGNED_AT)

    status = declaration_status(ready, SIGNED_AT)

    assert status.state is DeclarationState.STALE
    assert any("alcance no está aprobado" in r for r in status.errors)


def test_deja_de_estar_vigente_si_desaparece_measures_yaml(ready, signed, tmp_path):
    (tmp_path / "ws" / MEASURES_KEY).unlink()

    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.STALE


def test_firmar_de_nuevo_tras_un_cambio_la_devuelve_a_vigente(ready, signed):
    decide(ready, "op.cont.3", Applicability.NOT_APPLICABLE, "Justificación reescrita.")
    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.STALE

    approve_declaration(ready, "Luis Pardo", SIGNED_AT)

    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.CURRENT


REGISTER_WITH_SAFEGUARD = {
    "risks": [
        {
            "id": "R-001",
            "assets": ["app-facturas"],
            "services": ["svc-facturacion"],
            "threat": "Parada prolongada del servicio",
            "dimensions": ["D"],
            "valuation": {"D": "ALTO"},
            "likelihood": "media",
            "impact": "alto",
            "inherent": "alto",
            "safeguards": ["op.cont.3"],
            "residual": "bajo",
            "treatment": "mitigate",
            "owner": "Ana Ruiz",
        }
    ]
}


def test_un_riesgo_nuevo_con_una_salvaguarda_no_seleccionada_deja_obsoleta_la_declaracion(ready):
    decide(ready, "op.cont.3", Applicability.NOT_APPLICABLE, "No se selecciona.")
    approve_declaration(ready, "Luis Pardo", SIGNED_AT)
    assert declaration_status(ready, SIGNED_AT).state is DeclarationState.CURRENT

    ready.write_text("risk/register.yaml", yaml.safe_dump(REGISTER_WITH_SAFEGUARD))

    status = declaration_status(ready, SIGNED_AT)
    assert status.state is DeclarationState.STALE
    assert any("op.cont.3" in e and "R-001" in e for e in status.errors)


def test_validate_y_approve_por_cli_muestran_las_exclusiones_por_ausencia_de_activo(ready, ens_cli):
    decide(
        ready,
        "op.acc.5",
        Applicability.NOT_APPLICABLE,
        ABSENT_REASON,
        absent_assets=absent("usuarios externos"),
    )

    validated = ens_cli("applicability", "validate")
    signed = ens_cli("applicability", "approve", "--by", "Luis Pardo")

    assert validated.exit_code == 0, validated.output
    assert "op.acc.5" in validated.output
    assert "usuarios externos" in validated.output
    assert ABSENT_REASON in validated.output
    assert signed.exit_code == 0, signed.output
    assert 0 <= signed.output.index("op.acc.5") < signed.output.index("Declaración firmada")
    assert ABSENT_REASON in signed.output


def test_la_pista_de_una_declaracion_obsoleta_incluye_risk_approve(ready, ens_cli):
    approve_declaration(ready, "Luis Pardo", SIGNED_AT)
    ready.write_text(MEASURES_KEY, ready.read_text(MEASURES_KEY) + "\n# cambio\n")

    result = ens_cli("applicability", "status")

    assert result.exit_code == 1
    assert "risk approve" in result.output


def test_approve_por_cli_muestra_el_aviso_de_servidor_desplegado(ready, ens_cli):
    decide(
        ready,
        "mp.if.1",
        Applicability.NOT_APPLICABLE,
        ABSENT_REASON,
        absent_assets=absent("instalaciones propias que alberguen el sistema"),
    )

    signed = ens_cli("applicability", "approve", "--by", "Luis Pardo")

    assert signed.exit_code == 0, signed.output
    assert "hay un servidor desplegado" in signed.output


def test_la_declaracion_por_cli_se_firma_con_by_y_no_admite_otro_rol(ready, ens_cli):
    refused = ens_cli("applicability", "approve", "--by", "Luis Pardo", "--role", "system_owner")
    signed_ok = ens_cli("applicability", "approve", "--by", "Luis Pardo")

    assert refused.exit_code == 2
    assert signed_ok.exit_code == 0, signed_ok.output
    assert "security_officer" in signed_ok.output
