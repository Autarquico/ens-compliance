"""Registro del kit: qué herramientas ofrece la caja y todo lo que la UI cuenta de cada una.

Es solo descripción. El estado sale de la última evidencia del conector, nunca de este registro, y
ninguna herramienta cuenta como cumplimiento por estar listada o instalada.
"""

from dataclasses import dataclass

COPIAS_IMAGE = (
    "ghcr.io/garethgeorge/backrest:v1.14.1-scratch"
    "@sha256:2f5accac8df1dd625c73d43feb36437dd8e9ef582f8687c53d275bcb8aca0d10"
)
CLAVES_IMAGE = (
    "ghcr.io/openbao/openbao-distroless:2.7.2"
    "@sha256:67406e08c6eecae98c7a10f7326515489468f90386f4f2214b2a0498f22c3aa6"
)


@dataclass(frozen=True)
class MeasuredRam:
    """Memoria medida con una imagen concreta; si la imagen cambia, la medida deja de valer."""

    mib: int
    measured_on: str
    image: str
    conditions: str


@dataclass(frozen=True)
class KitTool:
    id: str
    name: str
    purpose: str
    connector: str
    measures: tuple[str, ...]
    measures_note: str
    covers: str
    not_covers: str
    covers_clauses: tuple[str, ...]
    partial_clauses: tuple[str, ...]
    not_covered_clauses: tuple[str, ...]
    wizard_url: str
    cta: str
    help_slug: str
    help_label: str
    hints: dict[str, str]
    ram: MeasuredRam | None


KIT_TOOLS: tuple[KitTool, ...] = (
    KitTool(
        id="copias",
        name="Copias de seguridad",
        purpose=(
            "Hace copias cifradas de tus datos a un almacenamiento externo y deja que la "
            "herramienta compruebe que existen y se pueden leer."
        ),
        connector="backups",
        measures=("mp.info.6",),
        measures_note="La prueba de restauración la hace y registra una persona.",
        covers=(
            "Hace copias cifradas a un almacenamiento externo con bloqueo, y en cada recogida "
            "comprueba que las copias existen, son recientes y el repositorio se puede leer."
        ),
        not_covers=(
            "La prueba de restauración (se pide desde el nivel MEDIO) la hace y la registra "
            "una persona; el kit no la sustituye. Tampoco decide que la medida esté "
            "cubierta: eso lo valora una persona."
        ),
        covers_clauses=(),
        partial_clauses=("mp.info.6.1",),
        not_covered_clauses=("mp.info.6.2", "mp.info.6.r1.1"),
        wizard_url="/herramientas/copias",
        cta="Añadir las copias",
        help_slug="kit-copias",
        help_label="Qué hacer cuando las copias fallan: runbook de las copias",
        hints={
            "down": (
                "La app no ha podido leer el repositorio; las copias pueden estar haciéndose. "
                "Revisa las credenciales de lectura y el destino."
            ),
            "problems": (
                "Alguna comprobación ha fallado: copia antigua, integridad o prueba de "
                "restauración vencida."
            ),
        },
        ram=MeasuredRam(
            mib=13,
            measured_on="2026-11-10",
            image=COPIAS_IMAGE,
            conditions="en reposo, sin copia en curso, Docker Desktop arm64",
        ),
    ),
    KitTool(
        id="claves",
        name="Claves y secretos",
        purpose=(
            "Guarda cifradas las claves y los secretos de la organización en un almacén propio "
            "(OpenBao), con auditoría de accesos y desbloqueo repartido entre varios custodios."
        ),
        connector="keystore",
        measures=("op.exp.10",),
        measures_note="Las partes de desbloqueo nunca se teclean en la web.",
        covers=(
            "Custodia cifrada de claves y secretos, con auditoría de accesos, control de acceso "
            "por políticas, inventario de claves, rotación según la política que fijes y "
            "desbloqueo por quórum de custodios. Es una parte de op.exp.10.1; en cada recogida "
            "se comprueba que el almacén está desbloqueado, auditado y con las claves rotadas."
        ),
        not_covers=(
            "No cubre op.exp.10.2 ni op.exp.10.3: OpenBao vive en el mismo servidor que la "
            "explotación, así que no hay aislamiento de medios. Tampoco la R1 (algoritmos y "
            "parámetros autorizados por el CCN): la valora una persona con el inventario de "
            "tipos de clave. No cubre las claves que queden fuera de OpenBao, ni decide que la "
            "medida esté cubierta: eso lo valora una persona."
        ),
        covers_clauses=(),
        partial_clauses=("op.exp.10.1",),
        not_covered_clauses=("op.exp.10.2", "op.exp.10.3", "op.exp.10.r1.1"),
        wizard_url="/herramientas/claves",
        cta="Añadir las claves",
        help_slug="kit-claves",
        help_label="Qué hacer: runbook de las claves",
        hints={
            "sealed": (
                "Sellado: hacen falta M de N custodios para desbloquearlo. Mientras siga "
                "sellado nadie puede leer los secretos."
            ),
            "down": (
                "La app no ha podido leer el almacén. Comprueba que el servicio está en marcha "
                "y que el token de la app sigue vigente."
            ),
            "problems": (
                "Alguna comprobación ha fallado: auditoría, rotación de claves, token raíz, "
                "permisos del token o registro de custodios."
            ),
        },
        ram=MeasuredRam(
            mib=27,
            measured_on="2026-11-10",
            image=CLAVES_IMAGE,
            conditions="en reposo, inicializado y desbloqueado, Docker Desktop arm64",
        ),
    ),
)


def tool_for(measure_id: str) -> KitTool | None:
    return next((t for t in KIT_TOOLS if measure_id in t.measures), None)
