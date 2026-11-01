"""Cadena de transiciones: integridad, tiempo, precondiciones por estado y escritura."""

from datetime import timedelta

import pytest
import yaml

from ens_evidence.assessment.dossier import transitions as service
from ens_evidence.assessment.dossier.transitions import (
    TransitionRecord,
    TransitionRejected,
    entries,
    record_key,
    record_transition,
    scan_transitions,
    seq_key,
)
from ens_evidence.models.dossier import CorrectiveMeasure, record_sha256
from ens_evidence.models.enums import DossierState, Readiness, Role
from ens_evidence.workspace import AlreadyExists

from .transition_helpers import (  # noqa: F401
    AT,
    CONCLUSIONS,
    DIRECTION,
    KEY,
    RECORDED,
    SECURITY_OFFICER,
    SYSTEM_OWNER,
    analyze,
    approve_designation,
    declare,
    fake_assess,
    generate,
    self_assess,
)
from .worlds import PASSWORD, edit_answers


def path(store, key):
    return store._root / key


def errors_of(call, *args, **kwargs) -> str:
    with pytest.raises(TransitionRejected) as error:
        call(*args, **kwargs)
    return "\n".join(error.value.errors)


def transition_keys(store, dossier) -> list[str]:
    return sorted(k for k in store.list(KEY.format(dossier)) if "/seq/" not in k)


# --- flujo ------------------------------------------------------------------------------------


def test_el_flujo_completo_en_basica_queda_encadenado(docs, stand_ins):
    dossier = generate(docs)

    first = self_assess(docs, dossier)
    second = analyze(docs, dossier)
    third = declare(docs, dossier, declaration_evidence_id=None)

    scan = scan_transitions(docs, dossier)
    assert scan.problems == ()
    assert scan.dossier.state is DossierState.DECLARED
    assert [e.record.to_state for e in scan.entries] == [
        DossierState.SELF_ASSESSED,
        DossierState.ANALYZED,
        DossierState.DECLARED,
    ]
    assert (
        third.entry.record.analysis_sha256
        == second.entry.sha256
        == record_sha256(second.entry.record)
    )
    assert first.notice is None
    assert third.entry.record.readiness.readiness is Readiness.READY
    assert third.entry.record.dossier_sha256 == scan.snapshot_sha256
    assert entries(docs, dossier) == list(scan.entries)


def test_lo_escrito_se_lee_con_las_mismas_huellas(docs, stand_ins):
    dossier = generate(docs)
    written = [self_assess(docs, dossier), analyze(docs, dossier)]

    read = entries(docs, dossier)

    assert [e.sha256 for e in read] == [w.entry.sha256 for w in written]
    assert [e.key for e in read] == [w.entry.key for w in written]
    assert [e.record for e in read] == [w.entry.record for w in written]


def test_declarar_sin_analizar_nombra_al_responsable_de_la_seguridad(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    message = errors_of(declare, docs, dossier)

    assert "ANALYZED" in message
    assert "responsable de la seguridad" in message
    assert len(transition_keys(docs, dossier)) == 1


def test_sin_autoevaluar_se_nombra_al_administrador(docs, stand_ins):
    dossier = generate(docs)

    assert "administrador del sistema" in errors_of(analyze, docs, dossier)


def test_un_expediente_declarado_no_admite_mas(declared_world):
    store, dossier = declared_world
    declare(store, dossier)

    assert "ya está declarado" in errors_of(declare, store, dossier)


def test_el_rol_de_cada_estado_se_exige(docs, stand_ins):
    dossier = generate(docs)
    wrong = errors_of(
        record_transition,
        docs,
        dossier,
        DossierState.SELF_ASSESSED,
        by="Ana",
        role=Role.MANAGEMENT,
        at=AT,
        now=RECORDED,
    )
    self_assess(docs, dossier)

    assert "administrador del sistema" in wrong
    assert "security_officer" in errors_of(
        record_transition,
        docs,
        dossier,
        DossierState.ANALYZED,
        by=SECURITY_OFFICER,
        role=Role.SYSTEM_OWNER,
        at=AT,
        now=RECORDED,
        conclusions=CONCLUSIONS,
    )


# --- SELF_ASSESSED -----------------------------------------------------------------------------


def test_autoevaluar_exige_una_revision_en_cada_fila(docs):
    dossier = generate(docs)

    message = errors_of(self_assess, docs, dossier)

    assert "una revisión en cada fila" in message
    assert transition_keys(docs, dossier) == []


# --- ANALYZED ----------------------------------------------------------------------------------


def test_analizar_sin_medidas_correctoras_registra_la_elevacion(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    recorded = analyze(docs, dossier, corrective_measures=[])

    record = recorded.entry.record
    assert record.corrective_measures == []
    assert record.elevated_to == SYSTEM_OWNER
    assert record.elevation_confirmed
    assert record.elevated_at == record.at


def test_analizar_con_medidas_correctoras_dirigidas_al_responsable_del_sistema(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)
    measure = CorrectiveMeasure(description="Cifrar las copias.", addressee=Role.SYSTEM_OWNER)

    recorded = analyze(docs, dossier, corrective_measures=[measure])

    assert recorded.entry.record.corrective_measures == [measure]


def test_sin_confirmar_la_elevacion_se_rechaza(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    assert "--elevated" in errors_of(analyze, docs, dossier, elevation_confirmed=False)


def test_la_elevacion_va_a_la_persona_del_responsable_del_sistema(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    message = errors_of(analyze, docs, dossier, elevated_to=SECURITY_OFFICER)

    assert "--elevated-to" in message
    assert "resp_sistema" in message


def test_analiza_solo_la_persona_declarada_como_responsable_de_la_seguridad(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    message = errors_of(analyze, docs, dossier, by="Otra Persona")

    assert "no es la persona declarada en resp_seguridad" in message
    assert len(transition_keys(docs, dossier)) == 1


def test_el_nombre_se_normaliza_al_declarado(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    recorded = analyze(docs, dossier, by="lucia navarro gil")

    assert recorded.entry.record.actor == SECURITY_OFFICER


def test_sin_designacion_vigente_no_se_analiza(docs, stand_ins, monkeypatch):
    dossier = generate(docs)
    self_assess(docs, dossier)
    monkeypatch.setattr(
        service, "document_status", lambda store, name: type("S", (), {"current": False})()
    )

    assert "designacion-responsables no está aprobada" in errors_of(analyze, docs, dossier)


def test_una_designacion_cambiada_bloquea(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)
    edit_answers(docs, "resp_seguridad", "Otra Persona")

    message = errors_of(analyze, docs, dossier, by="Otra Persona")

    assert "ya no describe el estado actual" in message
    assert len(transition_keys(docs, dossier)) == 1


def test_unas_conclusiones_con_contrasena_se_rechazan_sin_el_valor(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    message = errors_of(
        analyze, docs, dossier, conclusions=f"Se anotó password={PASSWORD} en el acta"
    )

    assert "conclusiones" in message
    assert PASSWORD not in message
    assert len(transition_keys(docs, dossier)) == 1


def test_una_medida_correctora_con_secreto_se_rechaza(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)
    measure = CorrectiveMeasure(
        description=f"Cambiar password={PASSWORD}", addressee=Role.SYSTEM_OWNER
    )

    message = errors_of(analyze, docs, dossier, corrective_measures=[measure])

    assert "medidas correctoras[0]" in message
    assert PASSWORD not in message


def test_un_nombre_con_correo_se_rechaza(docs, stand_ins):
    dossier = generate(docs)

    assert "--by" in errors_of(self_assess, docs, dossier, by="ana@example.org")


# --- DECLARED ----------------------------------------------------------------------------------


def test_declarar_la_registra_la_direccion_firmando_la_persona_declarada(declared_world):
    store, dossier = declared_world

    recorded = declare(store, dossier, by="marta ibanez soler")

    assert recorded.entry.record.actor == DIRECTION
    assert recorded.entry.record.role is Role.MANAGEMENT


def test_declarar_exige_la_persona_de_firmante_direccion(declared_world):
    store, dossier = declared_world

    message = errors_of(declare, store, dossier, by=SECURITY_OFFICER)

    assert "firmante_direccion" in message


def test_declarar_con_otro_rol_se_rechaza(declared_world):
    store, dossier = declared_world

    message = errors_of(
        record_transition,
        store,
        dossier,
        DossierState.DECLARED,
        by=DIRECTION,
        role=Role.SECURITY_OFFICER,
        at=AT + timedelta(minutes=10),
        now=RECORDED,
    )

    assert "management" in message


def test_una_instantanea_not_ready_impide_declarar(docs, reviewed):
    """Sin sustituir la preparación: el mundo ligero es NOT_READY."""
    dossier = generate(docs)
    self_assess(docs, dossier)
    analyze(docs, dossier)

    message = errors_of(declare, docs, dossier)

    assert "la instantánea no estaba READY" in message
    assert len(transition_keys(docs, dossier)) == 2


def test_una_evaluacion_actual_not_ready_impide_declarar(declared_world, monkeypatch):
    store, dossier = declared_world
    monkeypatch.setattr(service, "assess", fake_assess(Readiness.NOT_READY))

    message = errors_of(declare, store, dossier)

    assert "la evaluación actual no está READY" in message
    assert "MISSING_REVIEW:op.exp.1" in message


def test_una_evaluacion_actual_con_errores_impide_declarar(declared_world, monkeypatch):
    store, dossier = declared_world
    monkeypatch.setattr(service, "assess", fake_assess(errors=["controls/matrix.yaml: ilegible"]))

    assert "error(es) de lectura" in errors_of(declare, store, dossier)


def test_la_preparacion_la_calcula_el_servicio_y_nunca_el_llamador(declared_world):
    store, dossier = declared_world

    with pytest.raises(TypeError):
        declare(store, dossier, readiness=object())

    recorded = declare(store, dossier)
    assert recorded.entry.record.readiness.pending_human_review is True


def test_el_documento_enlazado_se_verifica(declared_world):
    store, dossier = declared_world

    message = errors_of(declare, store, dossier, declaration_evidence_id="EV-20260101-deadbeef")

    assert "--evidence" in message
    assert len(transition_keys(store, dossier)) == 2


def test_un_documento_enlazado_integro_se_registra(declared_world, monkeypatch):
    store, dossier = declared_world
    monkeypatch.setattr(service, "verify_one", lambda s, i: type("R", (), {"problems": []})())

    recorded = declare(store, dossier, declaration_evidence_id="EV-20260101-deadbeef")

    assert recorded.entry.record.declaration_evidence_id == "EV-20260101-deadbeef"


# --- tiempo ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("at", "fragment"),
    [
        (RECORDED + timedelta(seconds=1), "futuro"),
        (AT - timedelta(hours=2), "anterior a la generación"),
    ],
)
def test_at_fuera_de_rango_se_rechaza(docs, stand_ins, at, fragment):
    dossier = generate(docs)

    assert fragment in errors_of(self_assess, docs, dossier, at=at)
    assert transition_keys(docs, dossier) == []


def test_at_anterior_a_la_transicion_previa_se_rechaza(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier, at=AT + timedelta(minutes=30))

    assert "anterior a la transición previa" in errors_of(analyze, docs, dossier, at=AT)


def test_at_igual_a_la_generacion_y_a_ahora_se_admite(docs, stand_ins):
    dossier = generate(docs)

    self_assess(docs, dossier, at=RECORDED, now=RECORDED)


# --- integridad --------------------------------------------------------------------------------


@pytest.fixture
def chain(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)
    analyze(docs, dossier)
    return docs, dossier


def broken(store, dossier) -> str:
    scan = scan_transitions(store, dossier)
    assert scan.dossier is None
    return "\n".join(scan.problems)


def test_el_ultimo_registro_borrado_con_su_reserva_viva_bloquea(chain):
    store, dossier = chain
    last = transition_keys(store, dossier)[-1]
    path(store, last).unlink()

    assert "reserva de la secuencia 2 sin registro" in broken(store, dossier)
    assert "está roto" in errors_of(declare, store, dossier)


def test_un_registro_sin_reserva_bloquea(chain):
    store, dossier = chain
    path(store, seq_key(dossier, 2)).unlink()

    assert "registro 2 sin reserva" in broken(store, dossier)


def test_un_registro_truncado_bloquea(chain):
    store, dossier = chain
    last = path(store, transition_keys(store, dossier)[-1])
    last.write_bytes(last.read_bytes()[:40])

    assert broken(store, dossier)
    assert "está roto" in errors_of(declare, store, dossier)


def test_un_hueco_en_la_secuencia_bloquea(chain):
    store, dossier = chain
    path(store, transition_keys(store, dossier)[0]).unlink()

    problems = broken(store, dossier)

    assert "falta el registro 1" in problems
    assert "reserva de la secuencia 1 sin registro" in problems


def test_las_claves_yaml_duplicadas_bloquean(chain):
    store, dossier = chain
    last = path(store, transition_keys(store, dossier)[-1])
    last.write_bytes(last.read_bytes() + b"seq: 2\n")

    assert "clave repetida" in broken(store, dossier)


def test_un_nombre_inesperado_bloquea(chain):
    store, dossier = chain
    store.write_text(f"{KEY.format(dossier)}/notas.txt", "x")

    assert "clave inesperada" in broken(store, dossier)


def test_un_contenido_cambiado_no_coincide_con_su_clave(chain):
    store, dossier = chain
    first = path(store, transition_keys(store, dossier)[0])
    first.write_bytes(first.read_bytes().replace(b"Ana", b"Eva"))

    assert "la clave no coincide con el contenido" in broken(store, dossier)


def test_un_fallo_entre_la_reserva_y_el_registro_bloquea_lo_siguiente(docs, stand_ins):
    dossier = generate(docs)

    class Failing:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def create_bytes(self, key, data):
            if key.endswith(".yaml") and "/transitions/" in key:
                raise OSError("disco lleno")
            self._inner.create_bytes(key, data)

    with pytest.raises(OSError):
        self_assess(Failing(docs), dossier)

    assert "reserva de la secuencia 1 sin registro" in broken(docs, dossier)
    assert "está roto" in errors_of(self_assess, docs, dossier)


def test_dos_transiciones_simultaneas_solo_escriben_una(docs, stand_ins):
    dossier = generate(docs)

    class Racing:
        """Otra orden gana la reserva justo antes que esta."""

        def __init__(self, inner):
            self._inner = inner
            self.raced = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def create_bytes(self, key, data):
            if "/seq/" in key and not self.raced:
                self.raced = True
                self_assess(self._inner, dossier)
            self._inner.create_bytes(key, data)

    message = errors_of(self_assess, Racing(docs), dossier)

    assert "otra orden reservó antes la secuencia 1" in message
    assert len(transition_keys(docs, dossier)) == 1
    assert scan_transitions(docs, dossier).problems == ()


def _rewrite(store, dossier, seq, **changes):
    """Sustituye el registro `seq` por otro coherente consigo mismo (clave nueva, reserva viva)."""
    key = next(k for k in transition_keys(store, dossier) if f"/{seq:06d}-" in k)
    stored = TransitionRecord.model_validate(yaml.safe_load(store.read_text(key)))
    transition = stored.transition.model_copy(update=changes.pop("transition", {}))
    stored = stored.model_copy(update={"transition": transition, **changes})
    data = service._render(stored)
    path(store, key).unlink()
    store.create_bytes(record_key(stored, data), data)
    return stored


def test_la_huella_de_otro_dossier_bloquea(chain):
    store, dossier = chain
    _rewrite(store, dossier, 1, transition={"dossier_sha256": "b" * 64})

    assert "lleva la huella de otro dossier.json" in broken(store, dossier)


def test_un_registro_de_otro_expediente_bloquea(chain):
    store, dossier = chain
    key = transition_keys(store, dossier)[0]
    stored = TransitionRecord.model_validate(yaml.safe_load(store.read_text(key)))
    foreign = stored.model_copy(update={"dossier_id": "20260101T000000000000Z-aaaaaaaaaaaa"})
    path(store, key).unlink()
    store.create_bytes(key, service._render(foreign))  # una copia ajena bajo la clave de este

    assert "es de otro expediente" in broken(store, dossier)


def test_cambiar_un_eslabon_rompe_la_cadena(chain):
    store, dossier = chain
    _rewrite(store, dossier, 1, transition={"actor": "Eva"})

    assert "la cadena previous_sha256 está rota" in broken(store, dossier)


def test_el_analisis_sustituido_entre_analyzed_y_declared_bloquea(declared_world):
    store, dossier = declared_world
    declare(store, dossier)
    stored = _rewrite(store, dossier, 2, transition={"conclusions": "Otras conclusiones."})
    # Se reencadena el registro 3 para que solo quede la discrepancia con el análisis citado.
    key = next(k for k in transition_keys(store, dossier) if "/000003-" in k)
    third = TransitionRecord.model_validate(yaml.safe_load(store.read_text(key)))
    after = service._render(stored)
    import hashlib

    third = third.model_copy(update={"previous_sha256": hashlib.sha256(after).hexdigest()})
    data = service._render(third)
    path(store, key).unlink()
    store.create_bytes(record_key(third, data), data)

    assert "analysis_sha256 does not match the ANALYZED record" in broken(store, dossier)


def test_un_declared_falsificado_sobre_una_instantanea_not_ready_bloquea(
    declared_world, monkeypatch
):
    store, dossier = declared_world
    declare(store, dossier)
    assert scan_transitions(store, dossier).problems == ()

    monkeypatch.undo()  # el mundo ligero es NOT_READY: el DECLARED ya no se sostiene

    assert "DECLARED sobre una instantánea que no estaba READY" in broken(store, dossier)


def test_at_posterior_a_recorded_at_bloquea(chain):
    store, dossier = chain
    _rewrite(store, dossier, 1, recorded_at=AT - timedelta(minutes=1))

    assert "at es posterior a recorded_at" in broken(store, dossier)


def test_un_estado_fuera_de_orden_no_se_reconstruye(chain):
    store, dossier = chain
    _rewrite(store, dossier, 2, transition={"from_state": DossierState.GENERATED})

    assert "cannot go from" in broken(store, dossier)


def test_un_expediente_inexistente_no_admite_transiciones(docs, stand_ins):
    message = errors_of(self_assess, docs, "20260101T000000000000Z-aaaaaaaaaaaa")

    assert "no existe" in message


def test_reservar_dos_veces_la_misma_secuencia_falla(docs, stand_ins):
    dossier = generate(docs)
    self_assess(docs, dossier)

    with pytest.raises(AlreadyExists):
        docs.create_bytes(seq_key(dossier, 1), b"x")


# --- MEDIA/ALTA --------------------------------------------------------------------------------


def test_en_media_la_revision_interna_se_permite_y_declarar_se_rechaza(ready):
    approve_designation(ready.store)
    dossier = generate(ready.store)
    first = self_assess(ready.store, dossier)
    second = analyze(ready.store, dossier)

    assert first.notice and "revisión interna" in first.notice.lower()
    assert second.notice == first.notice
    message = errors_of(declare, ready.store, dossier)
    assert "auditoría de certificación (art. 38.1)" in message
    assert len(transition_keys(ready.store, dossier)) == 2
