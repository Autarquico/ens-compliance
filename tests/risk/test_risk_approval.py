import hashlib
import re
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from ens_evidence.categorization import approve_categorization
from ens_evidence.models.enums import Role
from ens_evidence.risk import (
    RiskApprovalRejected,
    RiskState,
    approve_risk,
    risk_status,
    validate_risk,
)
from ens_evidence.risk.models import APPROVAL_KEY, RISK_FILES
from ens_evidence.risk.templates import METHODOLOGY_MD as METHODOLOGY_MD_TEMPLATE
from ens_evidence.scope import ScopeNotApproved, approve_scope, revoke_approval

from .helpers import METHODOLOGY_MD, NOW, PLAN, REGISTER, risk, write_category, write_risks


def sha256(store, key: str) -> str:
    return hashlib.sha256(store.read_text(key).encode("utf-8")).hexdigest()


def test_sin_aprobacion_el_analisis_no_esta_aprobado(store):
    assert risk_status(store, NOW).state is RiskState.NOT_APPROVED


def test_el_security_officer_aprueba_con_el_sha256_de_cada_fichero(store):
    approval = approve_risk(store, "Marta Gil", NOW)

    assert approval.files == {key: sha256(store, key) for key in RISK_FILES}
    assert store.exists(APPROVAL_KEY)
    assert risk_status(store, NOW).state is RiskState.APPROVED


@pytest.mark.parametrize("by", ["", "  "])
def test_no_se_aprueba_sin_nombre(store, by):
    with pytest.raises(RiskApprovalRejected):
        approve_risk(store, by, NOW)

    assert not store.exists(APPROVAL_KEY)


def test_no_se_aprueba_un_analisis_invalido(store):
    write_risks(store, [risk(assets=["fantasma"])])

    with pytest.raises(RiskApprovalRejected) as rejected:
        approve_risk(store, "Marta Gil", NOW)

    assert any("no existe en el inventario" in error for error in rejected.value.errors)
    assert not store.exists(APPROVAL_KEY)


def test_la_aprobacion_vale_un_ano(store):
    approval = approve_risk(store, "Marta Gil", NOW)

    assert approval.valid_until == NOW.replace(year=NOW.year + 1)
    just_before = approval.valid_until - timedelta(seconds=1)
    assert risk_status(store, just_before).state is RiskState.APPROVED
    assert risk_status(store, approval.valid_until).state is not RiskState.APPROVED


@pytest.mark.parametrize("key", RISK_FILES)
def test_cambiar_cualquier_fichero_de_risk_deja_la_aprobacion_obsoleta(store, key):
    approve_risk(store, "Marta Gil", NOW)

    store.write_text(key, store.read_text(key) + "\n# retocado\n")

    assert risk_status(store, NOW).state is RiskState.STALE


def test_sin_methodology_md_validate_y_approve_dicen_cual_falta(store):
    (store._root / METHODOLOGY_MD).unlink()
    expected = "risk/methodology.md: no existe; créalo con ens-evidence risk init"

    _, errors = validate_risk(store, NOW)
    with pytest.raises(RiskApprovalRejected) as raised:
        approve_risk(store, "Marta Gil", NOW)

    assert expected in errors
    assert expected in raised.value.errors


def test_borrar_un_fichero_aprobado_deja_la_aprobacion_obsoleta(store):
    approve_risk(store, "Marta Gil", NOW)

    (store._root / REGISTER).unlink()

    assert risk_status(store, NOW).state is RiskState.STALE


def test_una_aprobacion_con_otro_rol_en_el_fichero_es_invalida(store):
    approve_risk(store, "Marta Gil", NOW)
    store.write_text(
        APPROVAL_KEY, store.read_text(APPROVAL_KEY).replace("security_officer", "system_owner")
    )

    status = risk_status(store, NOW)

    assert status.state is RiskState.INVALID
    assert "security_officer" in "\n".join(status.errors)


def test_una_aprobacion_sin_zona_horaria_es_invalida(store):
    approve_risk(store, "Marta Gil", NOW)
    text = store.read_text(APPROVAL_KEY)
    store.write_text(APPROVAL_KEY, re.sub(r"(Z|\+00:00)", "", text))

    assert risk_status(store, NOW).state is RiskState.INVALID


def test_una_aprobacion_con_yaml_roto_es_invalida(store):
    store.write_text(APPROVAL_KEY, "files: [\n")

    assert risk_status(store, NOW).state is RiskState.INVALID


def test_el_plan_forma_parte_de_lo_aprobado(store):
    approve_risk(store, "Marta Gil", NOW)

    store.write_text(PLAN, "actions: []\n")

    assert risk_status(store, NOW).state is RiskState.STALE


def test_approve_exige_el_alcance_aprobado(store):
    revoke_approval(store, "prueba", NOW)

    with pytest.raises(ScopeNotApproved):
        approve_risk(store, "Marta Gil", NOW)

    assert not store.exists(APPROVAL_KEY)


def test_cambiar_el_alcance_deja_la_aprobacion_obsoleta(store):
    approve_risk(store, "Marta Gil", NOW)
    assets = yaml.safe_load(store.read_text("scope/assets.yaml"))
    assets["assets"][0]["usage"] += " (cambiado)"
    store.write_text("scope/assets.yaml", yaml.safe_dump(assets, allow_unicode=True))
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)

    status = risk_status(store, NOW)

    assert status.state is RiskState.STALE
    assert any("alcance cambió" in reason for reason in status.errors)


def test_quitar_del_inventario_un_activo_del_registro_deja_la_aprobacion_obsoleta(store):
    approve_risk(store, "Marta Gil", NOW)
    assets = yaml.safe_load(store.read_text("scope/assets.yaml"))
    assets["assets"] = [a for a in assets["assets"] if a["id"] != "app-facturas"]
    store.write_text("scope/assets.yaml", yaml.safe_dump(assets, allow_unicode=True))

    status = risk_status(store, NOW)

    assert status.state is RiskState.STALE
    assert any("alcance no está aprobado" in reason for reason in status.errors)


def test_cambiar_la_version_del_catalogo_deja_la_aprobacion_obsoleta(store, monkeypatch):
    approval = approve_risk(store, "Marta Gil", NOW)
    other = approval.catalog_version.model_copy(update={"consolidation_date": "2000-01-01"})
    monkeypatch.setattr("ens_evidence.risk.approval.current_catalog_version", lambda _: other)

    status = risk_status(store, NOW)

    assert status.state is RiskState.STALE
    assert any("catálogo" in reason for reason in status.errors)


def test_cambiar_la_categorizacion_vigente_deja_la_aprobacion_obsoleta(store):
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW)
    approval = approve_risk(store, "Marta Gil", NOW)
    assert approval.categorization_sha256 is not None

    write_category(store, "ALTO")
    approve_categorization(store, "Marta Gil", NOW)

    status = risk_status(store, NOW)
    assert status.state is RiskState.STALE
    assert any("categorización" in reason for reason in status.errors)


def test_sin_categorizacion_vigente_no_se_aprueba(store):
    write_category(store, "ALTO")

    with pytest.raises(RiskApprovalRejected) as raised:
        approve_risk(store, "Marta Gil", NOW)

    assert any("categorización" in reason for reason in raised.value.errors)
    assert not store.exists(APPROVAL_KEY)


def test_un_registro_antiguo_sin_huella_de_categorizacion_esta_obsoleto(store):
    approve_risk(store, "Marta Gil", NOW)
    record = yaml.safe_load(store.read_text(APPROVAL_KEY))
    record.pop("categorization_sha256")
    store.write_text(APPROVAL_KEY, yaml.safe_dump(record))

    status = risk_status(store, NOW)

    assert status.state is RiskState.STALE
    assert any("categorización" in reason for reason in status.errors)


def test_aprobada_un_29_de_febrero_vence_el_28_de_febrero_siguiente(store):
    leap_day = datetime(2028, 2, 29, 9, 0, tzinfo=UTC)
    approve_categorization(store, "Marta Gil", leap_day)

    approval = approve_risk(store, "Marta Gil", leap_day)

    assert approval.valid_until == datetime(2029, 2, 28, 9, 0, tzinfo=UTC)
    assert risk_status(store, approval.valid_until).state is not RiskState.APPROVED


def test_una_aprobacion_con_fecha_futura_no_esta_vigente(store):
    approve_risk(store, "Marta Gil", NOW + timedelta(days=1))

    assert risk_status(store, NOW).state is RiskState.EXPIRED


def test_una_vigencia_editada_a_mano_que_no_es_de_un_ano_es_invalida(store):
    approve_risk(store, "Marta Gil", NOW)
    record = yaml.safe_load(store.read_text(APPROVAL_KEY))
    record["valid_until"] = datetime(2099, 1, 1, tzinfo=UTC)
    store.write_text(APPROVAL_KEY, yaml.safe_dump(record))

    status = risk_status(store, NOW)

    assert status.state is RiskState.INVALID
    assert "valid_until" in "\n".join(status.errors)


def test_una_aprobacion_ilegible_se_sustituye_al_volver_a_aprobar(store):
    store.write_text(APPROVAL_KEY, "{: roto")
    assert risk_status(store, NOW).state is RiskState.INVALID

    approve_risk(store, "Marta Gil", NOW)

    assert risk_status(store, NOW).state is RiskState.APPROVED


def test_una_metodologia_que_no_es_utf8_no_rompe_la_aprobacion(store):
    store.write_bytes(METHODOLOGY_MD, b"\xff\xfe metodolog\xed\xada")

    approval = approve_risk(store, "Marta Gil", NOW)

    assert (
        approval.files[METHODOLOGY_MD] == hashlib.sha256(b"\xff\xfe metodolog\xed\xada").hexdigest()
    )
    assert risk_status(store, NOW).state is RiskState.APPROVED


def test_no_se_aprueba_con_la_metodologia_sin_documentar(store):
    store.write_text(METHODOLOGY_MD, METHODOLOGY_MD_TEMPLATE)

    with pytest.raises(RiskApprovalRejected) as rejected:
        approve_risk(store, "Marta Gil", NOW)

    assert any("plantilla" in error for error in rejected.value.errors)
    assert not store.exists(APPROVAL_KEY)
