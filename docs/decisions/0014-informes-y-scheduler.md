# 0014 — Informes diario y mensual y scheduler dentro de la app

- Estado: aceptada
- Fecha: 2026-11-01

## Contexto

La caja ejecuta cada día recogida, checks e informes dentro del contenedor de
la app, sin systemd ni cron (`docs/vision.md`; ADR 0004). La guía
(`docs/ens-compliance-guide.md:905-941`) fija el contenido de los informes
diario y mensual. Todo informe debe identificar sus fuentes y timestamps, y la
herramienta nunca declara conformidad (`CLAUDE.md`).

## Decisión

### Informes

- **Fuentes de cada dato.** Cada dato lleva sus fuentes: clave del
  workspace, sha256 e instante propio. Si no hay fuente, el dato es «no
  disponible» con su motivo, nunca un 0. Una lista vacía cita lo consultado.
- **Recuentos de checks.** Salen del último intento utilizable de cada
  conector (ADR 0012). Sin él, «no disponible». La preparación y los bloqueos
  salen de la misma evaluación que `assessment status`.
- **«Sin cambios».** Solo se afirma con una base verificable: el último
  diario legible del periodo anterior. Los agregados del mensual indican su
  cobertura de días y si son parciales.
- **Formato y almacenamiento.**
  - JSON y HTML deterministas.
  - El HTML se genera solo a partir del modelo: escapado único, sin JS, sin
    enlaces y con CSP.
  - Se guardan como solo adición en `evidence/reports/{daily,monthly}/`.
  - Nunca se borran solos.

### Ciclo

- **Etapas.** Recogida → checks → informe → copia (`backup run`, activada
  por defecto). Cada etapa deja un marcador en `evidence/reports/cycles/`.
  Un ciclo cortado se reanuda solo en las etapas pendientes. El trabajo
  mensual solo genera el informe del mes anterior.
- **Fallos de recogida.** Cada recogida corre en un subproceso con timeout.
  Un fallo (carga del plugin, preparación, recogida, timeout o resultado
  rechazado) se registra como un resultado en ERROR generado por el
  orquestador, con su fase y sin datos observados, para que cuente como
  último intento y nunca reaparezca un PASS anterior. De las excepciones
  solo se guarda el tipo.
- **Frontera de secretos.** Antes de importar un resultado se buscan
  secretos en todo su contenido. Si aparece alguno, el resultado no se
  guarda y queda un ERROR que nombra la ruta, nunca el valor.
- **Lock.**
  - Un `flock` local por workspace, en `ENS_STATE_DIR`. Lo toman las entradas
    externas: scheduler, `report --cycle`, `collect`, `check --all` y
    `backup run`. Las etapas del ciclo lo heredan.
  - Si está ocupado, la CLI sale con 2.
  - No protege frente a varias máquinas sobre un mismo workspace S3, que ya
    exige un único editor (ADR 0006).
- **Copia.** Si falla o le faltan credenciales, queda en ERROR y el ciclo
  termina igualmente. El siguiente informe lo muestra.

### Scheduler

- **Implementación.** Un bucle propio, sin dependencias de planificación,
  con reloj inyectable. Despierta cada minuto como máximo y recarga
  `reports/schedule.yaml`. Si la configuración es inválida, mantiene la
  última válida.
- **Cambio de hora.** Con `zoneinfo` y `tzdata`. Una hora inexistente se
  desplaza por el salto y una ambigua usa su primera ocurrencia. Se
  deduplica por periodo local.
- **Recuperación.** Lo que está hecho se deriva del workspace. Tras un
  reinicio se ejecuta el periodo actual si falta. Los días pasados nunca se
  rellenan; el mensual muestra los días sin informe.
- **Valores por defecto** (decididos por el usuario):
  - diario a las 06:00, Europe/Madrid;
  - mensual el día 1;
  - avisos de caducidad: 30 días para revisiones, aprobaciones, pruebas de
    restauración y copia externa; 14 días para evidencia manual; 7 días para
    evidencia técnica;
  - conservar todos los informes.

## Consecuencias

- Una recogida colgada se termina por timeout. Un hijo que sobreviva a un
  SIGKILL del scheduler no hereda el lock.
- Leer la configuración cada minuto y listar manifiestos tiene un coste en
  S3 que aún no se ha medido.
- Las alertas críticas (perfil de detección, v0.3), las vulnerabilidades y
  las revisiones de acceso salen como «no disponible» hasta que existan sus
  fuentes.
