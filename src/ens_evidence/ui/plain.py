"""Los mensajes del dominio en castellano de la web: una sola tabla de órdenes de CLI → acción.

La web no enseña órdenes: cada orden se sustituye por lo que la persona hace en la pantalla
(o se quita si la propia pantalla ya lo ofrece). Los mensajes del dominio no cambian; las vistas
añaden después su acabado (mayúscula inicial, punto final, restos de frase).
"""

import re

_STATUS_COMMANDS = (
    r"matrix (?:review|link|set|unlink|init|status)"
    r"|docs (?:approve|status|build|init)"
    r"|evidence (?:import|verify)"
    r"|incident (?:verify|new|update|close)"
    r"|preaudit (?:review|show)"
    r"|applicability (?:status|propose)"
    r"|catalog show"
    r"|collect [a-z]+"
    r"|check --all"
)

# (orden en el texto del dominio, lo que lee la persona). Se aplican en este orden: lo específico
# antes que el recorte genérico de las órdenes entre comillas.
COMMAND_TRANSLATIONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), web)
    for pattern, web in (
        # asistente de riesgos y aplicabilidad
        (
            r"\s*\((?:ens-evidence )?(?:scope|categorization|risk|applicability|controls)"
            r"(?: [\w-]+)+\)",
            "",
        ),
        (
            r"(?:revísala y )?confírmala con `?applicability propose --confirm [\w.]+`?",
            "decídela otra vez con el botón de su ficha",
        ),
        (
            r"; (?:créalo|crea las plantillas) con (?:ens-evidence )?risk init",
            "; adopta la metodología en el paso «Metodología»",
        ),
        (r"; adóptala con adopted: true", "; adóptala en el paso «Metodología»"),
        (
            r"; ejecuta controls init",
            "; vuelve a preparar las medidas en el paso «Aplicabilidad»",
        ),
        (r"`chosen_reinforcements`", "los refuerzos elegidos"),
        (r"`absent_assets`", "la ausencia de activo"),
        (r"`compensation`", "la compensación"),
        (r"^[\w/.-]+\.yaml › ", ""),
        # pre-auditoría
        (r"--by:", "Quién lo hace:"),
        (r"--evidence:", "La evidencia:"),
        (r"--reason:", "El motivo:"),
        (r"--decision:", "La decisión:"),
        (r"`preaudit`", "la pre-auditoría"),
        # estado: las órdenes sueltas que la pantalla ya ofrece como botón
        (
            rf"\s*[:(]?\s*(?:ens-evidence\s+)?\b(?:{_STATUS_COMMANDS})\b"
            r"(?:[^.;]|\.(?=\S))*\)?",
            "",
        ),
        # cualquier otra orden entre comillas
        (r"\s*[(;,]?\s*(?:ver|ejecuta|usa)?\s*`[^`]*`\)?\.?", ""),
    )
)

CLI_TRACE = re.compile(r"`|--|ens-evidence")


def translate(message: str) -> str:
    """El mensaje con cada orden de CLI traducida a su acción en la web."""
    for pattern, web in COMMAND_TRANSLATIONS:
        message = pattern.sub(web, message)
    return message
