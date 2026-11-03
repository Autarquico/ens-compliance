"""Cadena de transiciones: integridad, tiempo, precondiciones por estado y escritura.

Parte de los árboles reales de `stages.py` (mundo READY BÁSICA, una copia por paso): nada de
sustituir métodos privados del servicio. Los rechazos que no necesitan reconstruir son rápidos;
solo las transiciones que se escriben pagan la reconstrucción.
"""

import hashlib
import shutil
from datetime import timedelta

import pytest
import yaml
from matrix.evaluate_helpers import NOW

from ens_evidence import chain
from ens_evidence.assessment.dossier.derived import save_view
from ens_evidence.assessment.dossier.package import save_package
from ens_evidence.assessment.dossier.store import ASSESSMENTS_PREFIX, MARKDOWN_NAME
from ens_evidence.assessment.dossier.transitions import (
    TransitionRejected,
    entries,
    record_key,
    record_transition,
    scan_transitions,
    seq_key,
)
from ens_evidence.assessment.dossier.view import TransitionRecord
from ens_evidence.evidence.importer import import_manual
from ens_evidence.evidence.layout import CHECKS_PREFIX
from ens_evidence.models.dossier import CorrectiveMeasure, record_sha256
from ens_evidence.models.enums import DossierState, Readiness, Role
from ens_evidence.workspace import AlreadyExists

from . import transition_helpers as helpers
from .stages import STEP_AT, basic, restore
from .transition_helpers import (
    CONCLUSIONS,
    DIRECTION,
    KEY,
    SECURITY_OFFICER,
    SYSTEM_OWNER,
    approve_designation,
)
from .worlds import PASSWORD, edit_answers

SELF, ANALYSIS, DECLARATION = (STEP_AT[n] for n in ("self-assess", "analyze", "declare"))


def at(step, **kwargs):
    """Momento del acto y del registro de cada paso, como en los árboles de `stages.py`."""
    return {"at": step, "now": step} | kwargs


def self_assess(store, dossier, **kwargs):
    return helpers.self_assess(store, dossier, **at(SELF) | kwargs)


def analyze(store, dossier, **kwargs):
    return helpers.analyze(store, dossier, **at(ANALYSIS) | kwargs)


def declare(store, dossier, **kwargs):
    return helpers.declare(store, dossier, **at(DECLARATION) | kwargs)


def path(store, key):
    return store._root / key


def errors_of(call, *args, **kwargs) -> str:
    with pytest.raises(TransitionRejected) as error:
        call(*args, **kwargs)
    return "\n".join(error.value.errors)


def transition_keys(store, dossier) -> list[str]:
    return sorted(k for k in store.list(KEY.format(dossier)) if "/seq/" not in k)


def stage(name, tmp_path, monkeypatch):
    stages = basic()
    return restore(stages, name, tmp_path, monkeypatch).store, stages.dossier


@pytest.fixture
def generated(tmp_path, monkeypatch):
    return stage("generated", tmp_path, monkeypatch)


@pytest.fixture
def self_assessed(tmp_path, monkeypatch):
    return stage("self-assess", tmp_path, monkeypatch)


@pytest.fixture
def analyzed(tmp_path, monkeypatch):
    return stage("analyze", tmp_path, monkeypatch)


@pytest.fixture
def declared(tmp_path, monkeypatch):
    return stage("declare", tmp_path, monkeypatch)


# --- flujo ------------------------------------------------------------------------------------


def test_el_flujo_completo_en_basica_queda_encadenado(generated):
    store, dossier = generated

    first = self_assess(store, dossier)
    second = analyze(store, dossier)
    third = declare(store, dossier)

    scan = scan_transitions(store, dossier)
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
    assert entries(store, dossier) == list(scan.entries)


def test_lo_leido_conserva_las_huellas_de_cada_registro(analyzed):
    store, dossier = analyzed

    read = entries(store, dossier)

    assert [e.record.to_state for e in read] == [
        DossierState.SELF_ASSESSED,
        DossierState.ANALYZED,
    ]
    assert [e.sha256 for e in read] == [record_sha256(e.record) for e in read]
    assert [e.key for e in read] == transition_keys(store, dossier)


def test_declarar_sin_analizar_nombra_al_responsable_de_la_seguridad(self_assessed):
    store, dossier = self_assessed

    message = errors_of(declare, store, dossier)

    assert "ANALYZED" in message
    assert "responsable de la seguridad" in message
    assert len(transition_keys(store, dossier)) == 1


def test_sin_autoevaluar_se_nombra_al_administrador(generated):
    store, dossier = generated

    assert "administrador del sistema" in errors_of(analyze, store, dossier)


def test_un_expediente_declarado_no_admite_mas(declared):
    store, dossier = declared

    assert "ya está declarado" in errors_of(declare, store, dossier)


def test_el_rol_de_cada_estado_se_exige(self_assessed):
    store, dossier = self_assessed

    assert "security_officer" in errors_of(
        record_transition,
        store,
        dossier,
        DossierState.ANALYZED,
        by=SECURITY_OFFICER,
        role=Role.SYSTEM_OWNER,
        at=ANALYSIS,
        now=ANALYSIS,
        conclusions=CONCLUSIONS,
    )


def test_autoexaminar_con_otro_rol_se_rechaza(generated):
    store, dossier = generated

    message = errors_of(
        record_transition,
        store,
        dossier,
        DossierState.SELF_ASSESSED,
        by="Ana",
        role=Role.MANAGEMENT,
        at=SELF,
        now=SELF,
    )

    assert "administrador del sistema" in message


# --- SELF_ASSESSED -----------------------------------------------------------------------------


def test_autoevaluar_exige_una_revision_en_cada_fila(tmp_path, monkeypatch):
    """Mundo real con todo enlazado pero sin revisiones ni ejecuciones."""
    from checks.acceptance_helpers import make_world

    world = make_world(tmp_path, monkeypatch, "linked", level="BAJO")
    approve_designation(world.store)
    dossier = helpers.generate(world.store)

    message = errors_of(helpers.self_assess, world.store, dossier)

    assert "una revisión en cada fila" in message
    assert transition_keys(world.store, dossier) == []


# --- ANALYZED ----------------------------------------------------------------------------------


def test_analizar_registra_la_elevacion_y_normaliza_el_nombre(self_assessed):
    store, dossier = self_assessed

    recorded = analyze(store, dossier, by="lucia navarro gil", corrective_measures=[])

    record = recorded.entry.record
    assert record.actor == SECURITY_OFFICER
    assert record.corrective_measures == []
    assert record.elevated_to == SYSTEM_OWNER
    assert record.elevation_confirmed
    assert record.elevated_at == record.at


def test_analizar_con_medidas_correctoras_dirigidas_al_responsable_del_sistema(self_assessed):
    store, dossier = self_assessed
    measure = CorrectiveMeasure(description="Cifrar las copias.", addressee=Role.SYSTEM_OWNER)

    recorded = analyze(store, dossier, corrective_measures=[measure])

    assert recorded.entry.record.corrective_measures == [measure]


def test_en_basica_analizar_sin_revision_del_juicio_manual_se_rechaza_en_castellano(
    self_assessed,
):
    store, dossier = self_assessed

    message = errors_of(analyze, store, dossier, manual_judgment_review=False)

    assert "--manual-judgment-review" in message
    assert "sin ella no se podrá declarar" in message
    assert len(transition_keys(store, dossier)) == 1


def test_sin_confirmar_la_elevacion_se_rechaza(self_assessed):
    store, dossier = self_assessed

    assert "--elevated" in errors_of(analyze, store, dossier, elevation_confirmed=False)


def test_la_elevacion_va_a_la_persona_del_responsable_del_sistema(self_assessed):
    store, dossier = self_assessed

    message = errors_of(analyze, store, dossier, elevated_to=SECURITY_OFFICER)

    assert "--elevated-to" in message
    assert "resp_sistema" in message


def test_analiza_solo_la_persona_declarada_como_responsable_de_la_seguridad(self_assessed):
    store, dossier = self_assessed

    message = errors_of(analyze, store, dossier, by="Otra Persona")

    assert "no es la persona declarada en resp_seguridad" in message
    assert len(transition_keys(store, dossier)) == 1


def test_una_designacion_cambiada_ya_no_esta_vigente_y_no_se_analiza(self_assessed):
    store, dossier = self_assessed
    edit_answers(store, "resp_seguridad", "Otra Persona")

    message = errors_of(analyze, store, dossier, by="Otra Persona")

    assert "el expediente ya no describe el estado actual" in message
    assert "APPROVED → OBSOLETE" in message
    assert len(transition_keys(store, dossier)) == 1


def test_un_cambio_organizativo_tras_generar_se_rechaza_por_deriva(self_assessed):
    store, dossier = self_assessed
    edit_answers(store, "mision", "Otra misión distinta de la registrada.")

    message = errors_of(analyze, store, dossier)

    assert "ya no describe el estado actual" in message
    assert len(transition_keys(store, dossier)) == 1


def test_unas_conclusiones_con_contrasena_se_rechazan_sin_el_valor(self_assessed):
    store, dossier = self_assessed

    message = errors_of(analyze, store, dossier, conclusions=f"Se anotó password={PASSWORD} en el")

    assert "conclusiones: contiene una credencial" in message
    assert ": :" not in message
    assert PASSWORD not in message
    assert len(transition_keys(store, dossier)) == 1


def test_una_medida_correctora_con_secreto_se_rechaza(self_assessed):
    store, dossier = self_assessed
    measure = CorrectiveMeasure(
        description=f"Cambiar password={PASSWORD}", addressee=Role.SYSTEM_OWNER
    )

    message = errors_of(analyze, store, dossier, corrective_measures=[measure])

    assert "medidas correctoras[0]" in message
    assert PASSWORD not in message


def test_un_nombre_con_correo_se_rechaza(generated):
    store, dossier = generated

    assert "--by" in errors_of(self_assess, store, dossier, by="ana@example.org")


# --- DECLARED ----------------------------------------------------------------------------------


def test_declarar_la_registra_la_direccion_y_reconoce_las_secciones_incompletas(analyzed):
    store, dossier = analyzed
    incomplete = [
        s.number
        for s in scan_transitions(store, dossier).snapshot.sections
        if s.state.value == "INCOMPLETE"
    ]

    with pytest.raises(TypeError):
        declare(store, dossier, readiness=object())  # la preparación nunca la aporta el llamador
    recorded = declare(store, dossier, by="marta ibanez soler")

    record = recorded.entry.record
    assert incomplete, "el mundo de prueba debe tener alguna sección INCOMPLETE"
    assert record.actor == DIRECTION
    assert record.role is Role.MANAGEMENT
    assert record.acknowledged_incomplete == incomplete
    assert record.readiness.readiness is Readiness.READY
    assert record.readiness.pending_human_review is True


def test_declarar_con_secciones_incompletas_exige_reconocerlas(analyzed):
    store, dossier = analyzed

    message = errors_of(declare, store, dossier, acknowledge_incomplete=False)

    assert "secciones incompletas" in message
    assert "«Riesgos»" in message
    assert "--acknowledge-incomplete" in message
    assert len(transition_keys(store, dossier)) == 2


def test_declarar_exige_la_persona_de_firmante_direccion(analyzed):
    store, dossier = analyzed

    assert "firmante_direccion" in errors_of(declare, store, dossier, by=SECURITY_OFFICER)


def test_declarar_con_otro_rol_se_rechaza(analyzed):
    store, dossier = analyzed

    message = errors_of(
        record_transition,
        store,
        dossier,
        DossierState.DECLARED,
        by=DIRECTION,
        role=Role.SECURITY_OFFICER,
        at=DECLARATION,
        now=DECLARATION,
    )

    assert "management" in message


def _without_check_runs(store):
    runs = store._root / CHECKS_PREFIX
    hidden = store._root.parent / "hidden-runs"
    shutil.move(runs, hidden)
    return lambda: shutil.move(hidden, runs)


def test_una_instantanea_not_ready_impide_declarar(generated):
    """Sin ejecuciones al generar el sistema no está READY; con ellas de vuelta no hay deriva."""
    store, _ = generated
    restore_runs = _without_check_runs(store)
    dossier = helpers.generate(store)
    restore_runs()
    self_assess(store, dossier)
    analyze(store, dossier)

    message = errors_of(declare, store, dossier)

    assert "el sistema no estaba preparado al generar este expediente" in message
    assert len(transition_keys(store, dossier)) == 2


def test_una_evaluacion_actual_not_ready_impide_declarar_y_nombra_las_medidas(analyzed):
    store, dossier = analyzed
    _without_check_runs(store)

    message = errors_of(declare, store, dossier)

    assert "la evaluación actual no está preparada" in message
    assert "falta evidencia (" in message
    assert "MISSING_EVIDENCE" not in message
    assert len(transition_keys(store, dossier)) == 2


def test_el_documento_enlazado_se_verifica(analyzed):
    store, dossier = analyzed

    message = errors_of(declare, store, dossier, declaration_evidence_id="EV-20260101-deadbeef")

    assert "--evidence" in message
    assert len(transition_keys(store, dossier)) == 2


def test_un_documento_enlazado_integro_se_registra(analyzed):
    store, dossier = analyzed
    manifest = import_manual(
        store, b"declaracion firmada", asset_id="declaracion", submitted_by="Marta", now=SELF
    )

    recorded = declare(store, dossier, declaration_evidence_id=manifest.id)

    assert recorded.entry.record.declaration_evidence_id == manifest.id


# --- tiempo ------------------------------------------------------------------------------------


def test_at_en_el_futuro_se_rechaza(generated):
    store, dossier = generated

    message = errors_of(self_assess, store, dossier, at=SELF + timedelta(seconds=1), now=SELF)

    assert "futuro" in message
    assert transition_keys(store, dossier) == []


def test_at_anterior_a_la_generacion_se_rechaza(generated):
    store, dossier = generated

    message = errors_of(self_assess, store, dossier, at=NOW - timedelta(days=1))

    assert "anterior a la generación" in message
    assert transition_keys(store, dossier) == []


def test_at_anterior_a_la_transicion_previa_se_rechaza(self_assessed):
    store, dossier = self_assessed

    assert "anterior a la transición previa" in errors_of(
        analyze, store, dossier, at=SELF - timedelta(minutes=1), now=ANALYSIS
    )


def test_un_reloj_que_retrocede_no_escribe_un_registro_que_rompa_la_cadena(self_assessed):
    store, dossier = self_assessed

    message = errors_of(analyze, store, dossier, at=SELF, now=SELF - timedelta(minutes=1))

    assert "el reloj va por detrás del último registro" in message
    assert scan_transitions(store, dossier).problems == ()
    assert len(transition_keys(store, dossier)) == 1


def test_at_igual_a_la_generacion_y_a_ahora_se_admite(generated):
    store, dossier = generated

    self_assess(store, dossier, at=NOW, now=NOW)


# --- integridad --------------------------------------------------------------------------------


@pytest.fixture
def chained(analyzed):
    return analyzed


def broken(store, dossier) -> str:
    scan = scan_transitions(store, dossier)
    assert scan.dossier is None
    return "\n".join(scan.problems)


def test_el_ultimo_registro_borrado_con_su_reserva_viva_bloquea(chained):
    store, dossier = chained
    path(store, transition_keys(store, dossier)[-1]).unlink()

    assert "reserva de la secuencia 2 sin registro" in broken(store, dossier)
    assert "está roto" in errors_of(declare, store, dossier)


def test_un_registro_sin_reserva_bloquea(chained):
    store, dossier = chained
    path(store, seq_key(dossier, 2)).unlink()

    assert "registro 2 sin reserva" in broken(store, dossier)


def test_un_registro_truncado_bloquea(chained):
    store, dossier = chained
    last = path(store, transition_keys(store, dossier)[-1])
    last.write_bytes(last.read_bytes()[:40])

    assert broken(store, dossier)
    assert "está roto" in errors_of(declare, store, dossier)


def test_un_hueco_en_la_secuencia_bloquea(chained):
    store, dossier = chained
    path(store, transition_keys(store, dossier)[0]).unlink()

    problems = broken(store, dossier)

    assert "falta el registro 1" in problems
    assert "reserva de la secuencia 1 sin registro" in problems


def test_las_claves_yaml_duplicadas_bloquean(chained):
    store, dossier = chained
    last = path(store, transition_keys(store, dossier)[-1])
    last.write_bytes(last.read_bytes() + b"seq: 2\n")

    assert "clave repetida" in broken(store, dossier)


def test_un_nombre_inesperado_bloquea(chained):
    store, dossier = chained
    store.write_text(f"{KEY.format(dossier)}/notas.txt", "x")

    assert "clave inesperada" in broken(store, dossier)


def test_un_contenido_cambiado_no_coincide_con_su_clave(chained):
    store, dossier = chained
    first = path(store, transition_keys(store, dossier)[0])
    first.write_bytes(first.read_bytes().replace(b"Ana", b"Eva"))

    assert "la clave no coincide con el contenido" in broken(store, dossier)


def test_un_fallo_entre_la_reserva_y_el_registro_bloquea_lo_siguiente(generated):
    store, dossier = generated

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
        self_assess(Failing(store), dossier)

    assert "reserva de la secuencia 1 sin registro" in broken(store, dossier)
    assert "está roto" in errors_of(self_assess, store, dossier)


def test_dos_transiciones_simultaneas_solo_escriben_una(generated):
    store, dossier = generated

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

    message = errors_of(self_assess, Racing(store), dossier)

    assert "otra orden reservó antes la secuencia 1" in message
    assert len(transition_keys(store, dossier)) == 1
    assert scan_transitions(store, dossier).problems == ()


def _render(stored) -> bytes:
    return chain.render_record(stored.model_dump(mode="json"))


def _rewrite(store, dossier, seq, **changes):
    """Sustituye el registro `seq` por otro coherente consigo mismo (clave nueva, reserva viva)."""
    key = next(k for k in transition_keys(store, dossier) if f"/{seq:06d}-" in k)
    stored = TransitionRecord.model_validate(yaml.safe_load(store.read_text(key)))
    transition = stored.transition.model_copy(update=changes.pop("transition", {}))
    stored = stored.model_copy(update={"transition": transition, **changes})
    data = _render(stored)
    path(store, key).unlink()
    store.create_bytes(record_key(stored, data), data)
    return stored


def test_la_huella_de_otro_dossier_bloquea(chained):
    store, dossier = chained
    _rewrite(store, dossier, 1, transition={"dossier_sha256": "b" * 64})

    assert "lleva la huella de otro dossier.json" in broken(store, dossier)


def test_un_registro_de_otro_expediente_bloquea(chained):
    store, dossier = chained
    key = transition_keys(store, dossier)[0]
    stored = TransitionRecord.model_validate(yaml.safe_load(store.read_text(key)))
    foreign = stored.model_copy(update={"dossier_id": "20260101T000000000000Z-aaaaaaaaaaaa"})
    path(store, key).unlink()
    store.create_bytes(key, _render(foreign))  # una copia ajena bajo la clave de este

    assert "es de otro expediente" in broken(store, dossier)


def test_cambiar_un_eslabon_rompe_la_cadena(chained):
    store, dossier = chained
    _rewrite(store, dossier, 1, transition={"actor": "Eva"})

    assert "la cadena previous_sha256 está rota" in broken(store, dossier)


def test_el_analisis_sustituido_entre_analyzed_y_declared_bloquea(declared):
    store, dossier = declared
    stored = _rewrite(store, dossier, 2, transition={"conclusions": "Otras conclusiones."})
    # Se reencadena el registro 3 para que solo quede la discrepancia con el análisis citado.
    key = next(k for k in transition_keys(store, dossier) if "/000003-" in k)
    third = TransitionRecord.model_validate(yaml.safe_load(store.read_text(key)))
    third = third.model_copy(
        update={"previous_sha256": hashlib.sha256(_render(stored)).hexdigest()}
    )
    data = _render(third)
    path(store, key).unlink()
    store.create_bytes(record_key(third, data), data)

    assert "analysis_sha256 does not match the ANALYZED record" in broken(store, dossier)


def test_un_declared_que_no_reconoce_las_secciones_incompletas_bloquea(declared):
    store, dossier = declared
    assert scan_transitions(store, dossier).problems == ()

    _rewrite(store, dossier, 3, transition={"acknowledged_incomplete": []})

    assert "DECLARED no reconoce exactamente las secciones INCOMPLETE" in broken(store, dossier)


def test_unas_conclusiones_con_secreto_escritas_a_mano_bloquean_al_leer(chained):
    store, dossier = chained
    _rewrite(store, dossier, 2, transition={"conclusions": f"Se anotó password={PASSWORD} aquí"})

    problems = broken(store, dossier)

    assert "conclusiones" in problems
    assert PASSWORD not in problems


def test_un_actor_con_correo_escrito_a_mano_bloquea_al_leer(chained):
    store, dossier = chained
    _rewrite(store, dossier, 2, transition={"actor": "lucia@example.org"})

    assert "actor" in broken(store, dossier) or "correo" in broken(store, dossier)


def test_at_posterior_a_recorded_at_bloquea(chained):
    store, dossier = chained
    _rewrite(store, dossier, 1, recorded_at=SELF - timedelta(minutes=1))

    assert "at es posterior a recorded_at" in broken(store, dossier)


def test_un_estado_fuera_de_orden_no_se_reconstruye(chained):
    store, dossier = chained
    _rewrite(store, dossier, 2, transition={"from_state": DossierState.GENERATED})

    assert "cannot go from" in broken(store, dossier)


def test_un_expediente_inexistente_no_admite_transiciones(generated):
    store, _ = generated

    message = errors_of(self_assess, store, "20260101T000000000000Z-aaaaaaaaaaaa")

    assert "no existe" in message


def test_reservar_dos_veces_la_misma_secuencia_falla(self_assessed):
    store, dossier = self_assessed

    with pytest.raises(AlreadyExists):
        store.create_bytes(seq_key(dossier, 1), b"x")


# --- MEDIA/ALTA --------------------------------------------------------------------------------


def test_en_media_la_revision_interna_se_permite_y_declarar_se_rechaza(ready):
    approve_designation(ready.store)
    dossier = helpers.generate(ready.store)
    first = helpers.self_assess(ready.store, dossier)
    second = helpers.analyze(ready.store, dossier)

    assert first.notice and "revisión interna" in first.notice.lower()
    assert second.notice == first.notice
    message = errors_of(helpers.declare, ready.store, dossier)
    assert "auditoría de certificación (art. 38.1)" in message
    assert len(transition_keys(ready.store, dossier)) == 2


# --- derivados alterados ----------------------------------------------------------------------


def derived_key(dossier, name):
    return f"{ASSESSMENTS_PREFIX}/{dossier}/{name}"


def test_declarar_con_el_markdown_editado_se_rechaza_y_no_escribe(analyzed):
    store, dossier = analyzed
    markdown = path(store, derived_key(dossier, MARKDOWN_NAME))
    markdown.write_text(markdown.read_text() + "\nTodo correcto.\n")
    before = transition_keys(store, dossier)

    message = errors_of(declare, store, dossier)

    assert "alterados a mano" in message
    assert MARKDOWN_NAME in message
    assert transition_keys(store, dossier) == before


def test_el_rechazo_por_markdown_alterado_propone_restaurar_y_no_solo_render(analyzed):
    store, dossier = analyzed
    path(store, derived_key(dossier, MARKDOWN_NAME)).write_text("falso\n")

    message = errors_of(declare, store, dossier)

    assert "desde la copia" in message
    assert f"borra {MARKDOWN_NAME} y recréalo con `assessment dossier render {dossier}`" in message
    assert "regenéral" not in message


def test_declarar_con_el_paquete_editado_se_rechaza_y_no_escribe(analyzed):
    store, dossier = analyzed
    scan = scan_transitions(store, dossier)
    save_package(store, dossier, scan.snapshot)
    file = next(k for k in store.list(derived_key(dossier, "package")) if "index" not in k)
    path(store, file).write_text("falso\n")
    before = transition_keys(store, dossier)

    message = errors_of(declare, store, dossier)

    assert "alterados a mano" in message
    assert file in message
    assert transition_keys(store, dossier) == before


def test_analizar_con_el_markdown_editado_tambien_se_rechaza(self_assessed):
    store, dossier = self_assessed
    markdown = path(store, derived_key(dossier, MARKDOWN_NAME))
    markdown.write_text("falso\n")

    assert "alterados a mano" in errors_of(analyze, store, dossier)


@pytest.mark.parametrize("fmt", ["md", "json"])
def test_declarar_con_una_vista_guardada_editada_se_rechaza(analyzed, fmt):
    store, dossier = analyzed
    scan = scan_transitions(store, dossier)
    key, _ = save_view(store, dossier, scan.snapshot, list(scan.entries), fmt)
    path(store, key).write_text("falso\n")

    message = errors_of(declare, store, dossier)

    assert "alterados a mano" in message
    assert key in message


def test_declarar_con_una_vista_guardada_intacta_se_registra(analyzed):
    store, dossier = analyzed
    scan = scan_transitions(store, dossier)
    save_view(store, dossier, scan.snapshot, list(scan.entries), "md")

    declare(store, dossier)

    assert scan_transitions(store, dossier).dossier.state is DossierState.DECLARED


def test_un_markdown_que_falta_no_bloquea_porque_render_lo_regenera(analyzed):
    store, dossier = analyzed
    path(store, derived_key(dossier, MARKDOWN_NAME)).unlink()

    declare(store, dossier)

    assert scan_transitions(store, dossier).dossier.state is DossierState.DECLARED
