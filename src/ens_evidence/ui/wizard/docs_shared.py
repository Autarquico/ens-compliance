"""Presentación de la documentación organizativa compartida por el asistente y la página."""

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from ens_evidence.models.enums import Role
from ens_evidence.orgdocs import DocumentState, Question, all_statuses, load_templates
from ens_evidence.orgdocs.approval import ROLE_QUESTION
from ens_evidence.ui.store import UiStore

SCOPE_NOT_APPROVED = "El alcance no está aprobado ahora: apruébalo en el paso «Aprobar el alcance»."
ROLE_LABELS = {
    Role.MANAGEMENT: "la dirección",
    Role.SECURITY_OFFICER: "el responsable de la seguridad",
    Role.SYSTEM_OWNER: "el responsable del sistema",
}
STATE_LABELS = {
    DocumentState.NOT_GENERATED: "Sin generar",
    DocumentState.GENERATED: "Generado, pendiente de aprobar",
    DocumentState.APPROVED: "Aprobado",
    DocumentState.OBSOLETE: "Aprobación caducada",
}
STATE_TONES = {
    DocumentState.NOT_GENERATED: "neutral",
    DocumentState.GENERATED: "warn",
    DocumentState.APPROVED: "ok",
    DocumentState.OBSOLETE: "warn",
}


@dataclass(frozen=True)
class Block:
    slug: str
    label: str
    title: str
    intro: str
    question_ids: tuple[str, ...]


BLOCKS = (
    Block(
        "documentos-organizacion",
        "Tu organización",
        "Tu organización y su dirección",
        "Cuenta quién eres, para quién trabajas y cómo organizas la seguridad. Con esto se "
        "redacta la política de seguridad. Responde con lo que de verdad hacéis: la "
        "herramienta no inventa nada por ti.",
        (
            "organizacion", "direccion", "mision", "objetivos_seguridad", "marco_legal",
            "riesgos_datos_personales", "comite", "documentacion_estructura",
            "documentacion_gestion", "documentacion_acceso", "difusion_personal",
            "servicios_externalizados", "poc_servicios",
        ),
    ),
    Block(
        "documentos-responsables",
        "Responsables",
        "Responsables y designación",
        "El ENS pide personas concretas con nombre y apellidos para cada responsabilidad. "
        "Declara primero a las personas y luego elige a cada una en su puesto: el nombre "
        "debe coincidir con el de la lista.",
        (
            "personas", "firmante_direccion", "resp_informacion", "resp_informacion_funciones",
            "resp_servicio", "resp_servicio_funciones", "resp_seguridad",
            "resp_seguridad_funciones", "resp_sistema", "resp_sistema_funciones",
            "relacion_seguridad_sistema", "causa_ausencia_recursos", "medidas_compensatorias",
            "designacion_procedimiento", "renovacion_procedimiento", "coordinacion",
            "resolucion_conflictos", "autoridad_procedimientos",
        ),
    ),
    Block(
        "documentos-normativa",
        "Normativa de uso",
        "Normativa de uso para el personal",
        "Las reglas que debe seguir cualquier persona de la organización al usar equipos, "
        "servicios e instalaciones. Escribe una regla concreta por fila.",
        (
            "uso_equipos", "uso_servicios", "uso_instalaciones", "uso_indebido",
            "derechos_personal", "deberes_personal", "medidas_disciplinarias",
        ),
    ),
    Block(
        "documentos-procedimientos",
        "Procedimientos",
        "Procedimientos operativos",
        "Cómo hacéis en la práctica las tareas de seguridad del día a día. Describir un "
        "procedimiento no demuestra que se aplique: para eso hacen falta evidencias, que "
        "irán después en la matriz.",
        (
            "procedimientos", "anomalias_identificacion", "anomalias_comunicacion",
            "anomalias_registro", "analisis_riesgos", "inventario_activos", "gestion_cuentas",
            "credenciales", "actualizaciones", "codigo_danino", "registro_actividad",
            "perimetro", "puesta_en_produccion", "informacion_deberes", "concienciacion",
            "formacion",
        ),
    ),
    Block(
        "documentos-informacion",
        "Información",
        "Cómo tratáis la información",
        "Acceso, almacenamiento, copias, etiquetado y transmisión de la información. Una "
        "fila por procedimiento.",
        (
            "info_acceso", "info_almacenamiento", "info_copias", "info_etiquetado",
            "info_transmision", "info_otras",
        ),
    ),
    Block(
        "documentos-incidentes",
        "Incidentes",
        "Gestión de incidentes",
        "Qué hacéis cuando algo sale mal: detectarlo, clasificarlo, resolverlo, comunicarlo "
        "y aprender de ello. Algunas preguntas dependen de si prestáis servicios a otra "
        "entidad (se responde en el bloque «Tu organización»).",
        (
            "incidentes_alcance", "incidentes_deteccion", "incidentes_informe_eventos",
            "incidentes_niveles", "incidentes_analisis", "incidentes_comunicacion",
            "incidentes_registro", "incidentes_mejora", "incidentes_datos_personales",
            "incibe_quien", "incibe_como", "incibe_cuando", "incidentes_evidencia",
            "incidentes_eventos_auditables",
        ),
    ),
    Block(
        "documentos-autorizaciones",
        "Autorizaciones",
        "Proceso de autorización",
        "Quién autoriza qué antes de usar instalaciones, equipos, aplicaciones, enlaces, "
        "soportes o servicios de terceros. Donde no tengáis nada de eso, marca «No aplica» "
        "y explica por qué.",
        (
            "aut_instalaciones", "aut_instalaciones_alt", "aut_equipos", "aut_criptografia",
            "aut_aplicaciones", "aut_enlaces", "aut_comunicaciones", "aut_comunicaciones_alt",
            "aut_soportes", "aut_moviles", "aut_terceros",
        ),
    ),
)  # fmt: skip
BLOCK_OF = {qid: block for block in BLOCKS for qid in block.question_ids}


@cache
def questions() -> dict[str, Question]:
    seen: dict[str, Question] = {}
    for loaded in load_templates():
        for question in loaded.template.questions:
            seen.setdefault(question.id, question)
    return seen


@cache
def titles() -> dict[str, str]:
    return {loaded.id: loaded.template.title for loaded in load_templates()}


def plain(message: str) -> str:
    """El mensaje de una API sin ids de pregunta ni órdenes de línea de comandos."""
    for question_id in sorted(questions(), key=len, reverse=True):
        label = f"«{questions()[question_id].text}»"
        pattern = (
            rf"\b{question_id}\b"
            if "_" in question_id
            else rf"^{question_id}\b|(?<=declarada en ){question_id}\b"
        )
        message = re.sub(pattern, lambda _, label=label: label, message)
    message = re.sub(r"[;,]? ?(revisa los cambios y )?ejecuta `docs build`", "", message)
    message = message.replace("`docs build`", "el paso «Generar los documentos»")
    return message.replace("`", "").strip()


def issue_groups(report) -> list[dict[str, Any]]:
    """Lo que falta o está mal, agrupado por bloque del cuestionario."""
    grouped: dict[str, list[str]] = {}
    unplaced: list[str] = []

    def add(question_id: str, text: str) -> None:
        block = BLOCK_OF.get(question_id)
        (grouped.setdefault(block.slug, []) if block else unplaced).append(text)

    for pending in report.pending:
        add(pending.question.id, f"Falta responder: {pending.question.text}")
    for problem in report.problems:
        owner = problem.split(":", 1)[0]
        add(owner, plain(problem))
    groups = [
        {"slug": b.slug, "label": b.title, "items": grouped[b.slug]}
        for b in BLOCKS
        if b.slug in grouped
    ]
    if unplaced:
        groups.append({"slug": None, "label": "Otros avisos", "items": unplaced})
    return groups


def document_rows(store: UiStore) -> list[dict[str, Any]]:
    return [
        {
            "name": status.name,
            "title": titles()[status.name],
            "state": status.state.value,
            "state_label": STATE_LABELS[status.state],
            "tone": STATE_TONES[status.state],
            "reasons": [plain(r) for r in status.reasons],
        }
        for status in all_statuses(store)
    ]


def signatory(role: Role | None, values: dict) -> str:
    holder = values.get(ROLE_QUESTION[role]) if role else None
    return holder if isinstance(holder, str) else ""
