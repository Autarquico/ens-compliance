from datetime import UTC, datetime, timedelta

import pytest

from ens_evidence.categorization import (
    APPROVAL_KEY,
    CATEGORIZATION_KEY,
    CategorizationApprovalRejected,
    CategorizationNotCurrent,
    CategorizationState,
    approve_categorization,
    categorization_status,
    require_current_categorization,
)
from ens_evidence.models.enums import Category
from ens_evidence.scope import ScopeNotApproved, ScopeState, revoke_approval, scope_status

from .helpers import (
    NOW,
    all_dimensions,
    edit,
    information,
    ratings,
    service,
    write_categorization,
    write_category,
)

SERVICE = "svc-facturacion"


def state(store, now=NOW):
    return categorization_status(store, now).state


def errors(store) -> str:
    status = categorization_status(store, NOW)
    return "\n".join([*status.errors, *status.pending])


def test_sin_fichero_la_categorizacion_es_invalida(store):
    assert state(store) is CategorizationState.INVALID
    assert "no existe" in errors(store)


def test_ids_repetidos_son_invalidos(store):
    write_categorization(
        store,
        [service(SERVICE, all_dimensions("BAJO"))],
        [information("inf", all_dimensions("BAJO")), information("inf", all_dimensions("BAJO"))],
    )

    assert state(store) is CategorizationState.INVALID
    assert "id repetido" in errors(store)


def test_un_servicio_del_alcance_sin_valorar_es_invalido(store):
    write_categorization(store, [], [information("inf", all_dimensions("BAJO"))])

    assert state(store) is CategorizationState.INVALID
    assert f"falta valorar el servicio {SERVICE}" in errors(store)


def test_un_servicio_inexistente_es_invalido(store):
    write_categorization(
        store,
        [service(SERVICE, all_dimensions("BAJO")), service("svc-fantasma", all_dimensions("BAJO"))],
    )

    assert state(store) is CategorizationState.INVALID
    assert "svc-fantasma" in errors(store)
    assert "no existe en el inventario" in errors(store)


def test_una_dimension_sin_justificacion_es_invalida(store):
    rated = all_dimensions("BAJO")
    rated["C"]["justification"] = "   "
    write_categorization(store, [service(SERVICE, rated)])

    assert state(store) is CategorizationState.INVALID
    assert "justificación" in errors(store)


def test_not_affected_tambien_exige_justificacion(store):
    rated = all_dimensions("BAJO")
    rated["D"] = {"level": "not_affected"}
    write_categorization(store, [service(SERVICE, rated)])

    assert state(store) is CategorizationState.INVALID
    assert "justification" in errors(store)


def test_not_affected_con_justificacion_es_valido(store):
    rated = all_dimensions("BAJO")
    rated["D"] = {"level": "not_affected", "justification": "No depende de disponibilidad"}
    write_categorization(store, [service(SERVICE, rated)])

    assert state(store) is CategorizationState.NOT_APPROVED


def test_un_servicio_valorado_por_quien_no_es_service_owner_es_invalido(store):
    write_categorization(store, [service(SERVICE, all_dimensions("BAJO"), role="system_owner")])

    assert state(store) is CategorizationState.INVALID
    assert "service_owner" in errors(store)


def test_una_informacion_valorada_por_quien_no_es_information_owner_es_invalida(store):
    write_categorization(
        store,
        [service(SERVICE, all_dimensions("BAJO"))],
        [information("inf", all_dimensions("BAJO"), role="service_owner")],
    )

    assert state(store) is CategorizationState.INVALID
    assert "information_owner" in errors(store)


def test_una_valoracion_con_fecha_futura_es_invalida(store):
    item = service(SERVICE, all_dimensions("BAJO"))
    item["valuation"]["valued_at"] = (NOW + timedelta(days=1)).isoformat()
    write_categorization(store, [item])

    assert state(store) is CategorizationState.INVALID
    assert "futuro" in errors(store)


def test_una_valoracion_sin_zona_horaria_es_invalida(store):
    item = service(SERVICE, all_dimensions("BAJO"))
    item["valuation"]["valued_at"] = "2026-10-20T10:00:00"
    write_categorization(store, [item])

    assert state(store) is CategorizationState.INVALID
    assert "valued_at" in errors(store)


def test_dimensiones_sin_valorar_dejan_la_categorizacion_pendiente(store):
    write_categorization(store, [service(SERVICE, ratings(C="MEDIO"))])

    status = categorization_status(store, NOW)

    assert status.state is CategorizationState.PENDING
    assert any("faltan las dimensiones I, T, A, D" in item for item in status.pending)


def test_sin_valoracion_del_responsable_queda_pendiente(store):
    item = service(SERVICE, all_dimensions("BAJO"))
    del item["valuation"]
    write_categorization(store, [item])

    status = categorization_status(store, NOW)

    assert status.state is CategorizationState.PENDING
    assert any("falta la valoración de su responsable" in p for p in status.pending)


def test_todo_not_affected_deja_la_categoria_indeterminada_y_pendiente(store):
    write_categorization(
        store,
        [service(SERVICE, all_dimensions("not_affected"))],
        [information("datos", all_dimensions("not_affected"))],
    )

    status = categorization_status(store, NOW)

    assert status.state is CategorizationState.PENDING
    assert status.computation.category is None
    assert any("indeterminada" in p for p in status.pending)


def test_valorada_y_sin_aprobar_no_esta_aprobada(store):
    write_category(store, "BAJO")

    assert state(store) is CategorizationState.NOT_APPROVED


def test_solo_el_security_officer_aprueba_y_queda_registrado(store):
    write_category(store, "MEDIO")

    approval = approve_categorization(store, "Marta Gil", NOW)

    assert approval.role.value == "security_officer"
    assert approval.category is Category.MEDIA
    assert store.exists(APPROVAL_KEY)
    assert state(store) is CategorizationState.CURRENT


def test_la_aprobacion_no_acepta_otro_rol_en_el_fichero(store):
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW)
    store.write_text(
        APPROVAL_KEY, store.read_text(APPROVAL_KEY).replace("security_officer", "system_owner")
    )

    status = categorization_status(store, NOW)

    assert status.state is CategorizationState.INVALID
    assert "security_officer" in "\n".join(status.errors)


def test_no_se_aprueba_sin_alcance_aprobado(store):
    write_category(store, "MEDIO")
    revoke_approval(store, "prueba", NOW)

    with pytest.raises(ScopeNotApproved):
        approve_categorization(store, "Marta Gil", NOW)

    assert not store.exists(APPROVAL_KEY)


def test_no_se_aprueba_con_pendientes(store):
    write_categorization(store, [service(SERVICE, ratings(C="MEDIO"))])

    with pytest.raises(CategorizationApprovalRejected) as rejected:
        approve_categorization(store, "Marta Gil", NOW)

    assert any("faltan las dimensiones" in error for error in rejected.value.errors)
    assert not store.exists(APPROVAL_KEY)


def test_no_se_aprueba_con_categoria_indeterminada(store):
    write_categorization(
        store,
        [service(SERVICE, all_dimensions("not_affected"))],
        [information("datos", all_dimensions("not_affected"))],
    )

    with pytest.raises(CategorizationApprovalRejected):
        approve_categorization(store, "Marta Gil", NOW)

    assert not store.exists(APPROVAL_KEY)


def test_no_se_aprueba_un_fichero_invalido(store):
    write_categorization(store, [])

    with pytest.raises(CategorizationApprovalRejected):
        approve_categorization(store, "Marta Gil", NOW)


@pytest.mark.parametrize("by", ["", "  "])
def test_no_se_aprueba_sin_nombre(store, by):
    write_category(store, "MEDIO")

    with pytest.raises(CategorizationApprovalRejected):
        approve_categorization(store, by, NOW)

    assert not store.exists(APPROVAL_KEY)


def test_cambiar_el_fichero_deja_la_aprobacion_no_vigente(store):
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW)

    store.write_text(CATEGORIZATION_KEY, store.read_text(CATEGORIZATION_KEY) + "# retocado\n")

    status = categorization_status(store, NOW)
    assert status.state is CategorizationState.STALE
    assert any("cambió desde que se aprobó" in e for e in status.errors)


def test_cambiar_la_huella_del_alcance_deja_la_aprobacion_no_vigente(store):
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW)
    before = scope_status(store).fingerprint

    edit(store, "assets", "os: macOS 15", "os: macOS 16")

    assert scope_status(store).fingerprint != before
    assert state(store) is CategorizationState.STALE


def test_revocar_el_alcance_deja_la_aprobacion_no_vigente_aunque_la_huella_no_cambie(store):
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW)
    before = scope_status(store).fingerprint

    revoke_approval(store, "revisión", NOW)

    assert scope_status(store).state is ScopeState.NOT_APPROVED
    assert scope_status(store).fingerprint == before
    status = categorization_status(store, NOW)
    assert status.state is CategorizationState.STALE
    assert any("alcance no está aprobado" in e for e in status.errors)


def test_la_aprobacion_vence_exactamente_un_ano_despues(store):
    write_category(store, "MEDIO")
    approval = approve_categorization(store, "Marta Gil", NOW)

    assert state(store, approval.valid_until - timedelta(seconds=1)) is CategorizationState.CURRENT
    assert state(store, approval.valid_until) is CategorizationState.EXPIRED
    assert approval.valid_until == NOW.replace(year=NOW.year + 1)


def test_require_current_categorization_lanza_si_no_esta_vigente(store):
    write_category(store, "MEDIO")

    with pytest.raises(CategorizationNotCurrent) as raised:
        require_current_categorization(store, NOW)

    assert raised.value.status.state is CategorizationState.NOT_APPROVED


def test_require_current_categorization_devuelve_la_categoria_aprobada(store):
    write_category(store, "ALTO")
    approve_categorization(store, "Marta Gil", NOW)

    status = require_current_categorization(store, NOW)

    assert status.approval.category is Category.ALTA


def test_sin_ninguna_informacion_queda_pendiente_y_no_se_aprueba(store):
    write_categorization(store, [service(SERVICE, all_dimensions("ALTO"))], [])

    status = categorization_status(store, NOW)

    assert status.state is CategorizationState.PENDING
    assert any("no hay informaciones valoradas" in p for p in status.pending)
    with pytest.raises(CategorizationApprovalRejected):
        approve_categorization(store, "Marta Gil", NOW)


@pytest.mark.parametrize("corrupt", ["{: roto", "- una\n- lista\n", "category: MEDIA\n"])
def test_una_aprobacion_ilegible_se_sustituye_al_volver_a_aprobar(store, corrupt):
    write_category(store, "MEDIO")
    store.write_text(APPROVAL_KEY, corrupt)
    assert state(store) is CategorizationState.INVALID

    approve_categorization(store, "Marta Gil", NOW)

    assert state(store) is CategorizationState.CURRENT


def test_una_aprobacion_binaria_se_sustituye_al_volver_a_aprobar(store):
    write_category(store, "MEDIO")
    store.write_bytes(APPROVAL_KEY, b"\xff\xfe\x00")

    approve_categorization(store, "Marta Gil", NOW)

    assert state(store) is CategorizationState.CURRENT


def test_aprobada_un_29_de_febrero_vence_el_28_de_febrero_siguiente(store):
    leap_day = datetime(2028, 2, 29, 9, 0, tzinfo=UTC)
    write_category(store, "MEDIO")

    approval = approve_categorization(store, "Marta Gil", leap_day)

    assert approval.valid_until == datetime(2029, 2, 28, 9, 0, tzinfo=UTC)
    assert (
        state(
            store,
            approval.valid_until - timedelta(seconds=1),
        )
        is CategorizationState.CURRENT
    )
    assert state(store, approval.valid_until) is CategorizationState.EXPIRED


def test_una_aprobacion_con_fecha_futura_no_esta_vigente(store):
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW + timedelta(days=1))

    assert state(store, NOW) is CategorizationState.EXPIRED
