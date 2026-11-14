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
CONTRASENAS_IMAGE = (
    "vaultwarden/server:1.37.5-alpine"
    "@sha256:bbe684a02d6b0db54256ff88eac9754f4519284a7b26314afc241fd7d0918191"
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
    on_box: bool = True
    check_ids: tuple[str, ...] = ()


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
            mib=24,
            measured_on="2026-11-12",
            image=COPIAS_IMAGE,
            conditions="Backrest (13 MiB, medido el 2026-11-10) y su proxy de salida (11 MiB) en"
            " reposo, sin repositorio conectado ni copia en curso, Docker Desktop arm64",
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
    KitTool(
        id="contrasenas",
        name="Contraseñas del equipo",
        purpose=(
            "Un gestor de contraseñas propio (Vaultwarden) para que el equipo genere, guarde y "
            "cambie sus credenciales sin hojas de cálculo ni notas, con segundo factor en las "
            "cuentas del propio gestor."
        ),
        connector="passwords",
        measures=("op.acc.6",),
        measures_note=(
            "La app no lee los cofres: solo un resumen de las cuentas del gestor (sin correos)."
        ),
        covers=(
            "Por sí solo no cubre ninguna cláusula de op.acc.6. El gestor ayuda a generar "
            "contraseñas largas y a cambiarlas sin esfuerzo. En cada recogida la herramienta "
            "comprueba el segundo factor y la inactividad de las cuentas del propio gestor; "
            "detectar una cuenta inactiva no la suspende: eso lo hace una persona."
        ),
        not_covers=(
            "No mide cada cuánto se cambian las credenciales de tus sistemas (op.acc.6.4), ni su "
            "complejidad (op.acc.6.r1.2), ni el segundo factor de acceso a esos sistemas "
            "(op.acc.6.r2.1): el segundo factor que se comprueba es el de las cuentas del gestor. "
            "Tampoco decide que la medida esté cubierta: eso lo valora una persona."
        ),
        covers_clauses=(),
        partial_clauses=(),
        not_covered_clauses=("op.acc.6.4", "op.acc.6.r1.2", "op.acc.6.r2.1"),
        wizard_url="/herramientas/contrasenas",
        cta="Añadir las contraseñas",
        help_slug="kit-contrasenas",
        help_label="Qué hacer: runbook de las contraseñas",
        hints={
            "down": (
                "La app no ha podido leer el resumen del gestor, o es demasiado viejo. Comprueba "
                "que el exportador y Vaultwarden están en marcha."
            ),
            "problems": (
                "Alguna comprobación ha fallado: una cuenta del gestor sin segundo factor, "
                "inactiva, o una instantánea de la base que no es reciente."
            ),
        },
        ram=MeasuredRam(
            mib=81,
            measured_on="2026-11-11",
            image=CONTRASENAS_IMAGE,
            conditions="Vaultwarden (30 MiB) y su exportador (51 MiB) en reposo, base de prueba con"
            " tres usuarios, Docker Desktop arm64",
        ),
    ),
    KitTool(
        id="antimalware",
        name="Protección frente a código dañino",
        purpose=(
            "Instala ClamAV en tus servidores Linux (Debian, Ubuntu) y lee, con osquery, el "
            "antimalware de cada equipo: ClamAV en Linux, Defender en Windows, XProtect y "
            "Gatekeeper en macOS. No hay nada que desplegar en la caja: se instala en cada equipo "
            "con el instalador de agentes."
        ),
        connector="endpoint",
        measures=("op.exp.6",),
        measures_note="Instalar el antimalware no cumple la medida: lo valora una persona.",
        covers=(
            "En cada recogida comprueba que las firmas están al día (op.exp.6.4: en Linux la "
            "fecha de los ficheros de ClamAV y que freshclam esté activo; en macOS XProtect; en "
            "Windows el estado de Defender) y que los puestos tienen protección en tiempo real "
            "(op.exp.6.5: Gatekeeper y XProtect en macOS, Defender en Windows). Cubre una parte "
            "de esas dos cláusulas; la fecha es la del fichero, no la versión de las firmas. "
            "Windows se evalúa de extremo a extremo cuando llegue el colector de osquery; por "
            "ahora solo está probado con ejemplos."
        ),
        not_covers=(
            "No es un EDR ni responde a incidentes, y no lanza análisis bajo demanda. No analiza "
            "los ficheros que llegan de fuera antes de abrirlos (op.exp.6.3), ni comprueba el "
            "escaneo periódico, el análisis al arrancar, la lista blanca de aplicaciones ni el "
            "EDR de los niveles MEDIO y ALTO. En servidores RPM el instalador no añade "
            "repositorios: ClamAV se instala a mano. En Linux de puesto no hay protección en "
            "tiempo real evidenciable. No decide que la medida esté cubierta: eso lo valora una "
            "persona."
        ),
        covers_clauses=(),
        partial_clauses=("op.exp.6.4", "op.exp.6.5"),
        not_covered_clauses=(
            "op.exp.6.3",
            "op.exp.6.r1.1",
            "op.exp.6.r2.1",
            "op.exp.6.r3.1",
            "op.exp.6.r4.1",
        ),
        wizard_url="/ayuda/kit-antimalware",
        cta="Ver cómo se instala",
        help_slug="kit-antimalware",
        help_label="Qué hacer cuando falla: runbook de la protección frente a código dañino",
        hints={
            "down": (
                "La app no ha podido evaluar las firmas ni la protección de ningún equipo: no "
                "hay evidencia reciente o falta alguna consulta."
            ),
            "problems": (
                "Alguna comprobación de firmas o de tiempo real ha fallado o no se ha podido "
                "evaluar en algún equipo."
            ),
        },
        ram=None,
        on_box=False,
        check_ids=("HOST-008", "HOST-009"),
    ),
)


def tool_for(measure_id: str) -> KitTool | None:
    return next((t for t in KIT_TOOLS if measure_id in t.measures), None)
