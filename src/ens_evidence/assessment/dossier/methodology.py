"""Texto fijo y versionado de la metodología del expediente, con citas literales del BOE."""

import hashlib
import json

METHODOLOGY_VERSION = "1"

# RD 311/2022: texto copiado del BOE; un test comprueba que aparece literalmente.
QUOTES = {
    "art. 38.1": (
        "Los sistemas de información comprendidos en el ámbito del artículo 2 serán objeto de "
        "un proceso para determinar su conformidad con el ENS. A tal efecto, los sistemas de "
        "categoría MEDIA o ALTA precisarán de una auditoría para la certificación de su "
        "conformidad, sin perjuicio de la auditoría de la seguridad prevista en el artículo 31 "
        "que podrá servir asimismo para los fines de la certificación, mientras que los "
        "sistemas de categoría BÁSICA solo requerirán de una autoevaluación para su "
        "declaración de la conformidad, sin perjuicio de que se puedan someter igualmente a "
        "una auditoria de certificación."
    ),
    "anexo III 2.1.a": (
        "Los sistemas de información de categoría BÁSICA no necesitarán realizar una "
        "auditoría. Bastará una autoevaluación realizada por el mismo personal que administra "
        "el sistema de información o en quien éste delegue.\n"
        "El resultado de la autoevaluación debe estar documentado, indicando si cada medida de "
        "seguridad está implantada y sujeta a revisión regular, así como las evidencias que "
        "sustentan la valoración anterior."
    ),
    "anexo III 2.1.b": (
        "Los informes de autoevaluación serán analizados por el responsable de la seguridad "
        "competente, que elevará las conclusiones al responsable del sistema para que adopte "
        "las medidas correctoras adecuadas."
    ),
}

READINESS_RULE = (
    "Regla de preparación (ADR 0002): función pura sobre las medidas, los hallazgos abiertos "
    "y la fecha de evaluación. READY solo con cero bloqueos. Una medida implantada sin "
    "revisión humana vigente no cuenta como satisfecha. La ausencia de evidencia nunca "
    "es PASS."
)
VALID_SOURCES = (
    "Fuentes válidas (ADR 0011): evidencia manual con el artefacto íntegro; la última "
    "ejecución completa de un check, con todos los sujetos esperados en PASS y evidencia "
    "reciente; documento organizativo aprobado y vigente. La implantación sale solo de una "
    "revisión humana registrada."
)
CHECKS = (
    "Checks (ADR 0012): leen la configuración y la evidencia ya guardada; no contactan con "
    "los servicios. Ante evidencia ilegible o ausente el resultado es UNKNOWN, y cuenta el "
    "último intento de cada conector."
)
SCOPE_NOTE = (
    "Este expediente recoge la autoevaluación y su análisis. La declaración de conformidad "
    "es un acto de la organización; la herramienta no la emite."
)


def methodology_text() -> str:
    quotes = "\n\n".join(f"{source}: «{text}»" for source, text in QUOTES.items())
    return "\n\n".join([SCOPE_NOTE, quotes, READINESS_RULE, VALID_SOURCES, CHECKS])


def methodology_sha256() -> str:
    payload = json.dumps(
        {"version": METHODOLOGY_VERSION, "text": methodology_text()},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
