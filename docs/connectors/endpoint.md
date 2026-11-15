# Evidencia de endpoints: script guiado y agente osquery

La caja ya trae servidor y agentes osquery ([agentes osquery](../runbooks/agentes-osquery.md)):
sus resultados programados y sus contactos alimentan estos checks junto al paquete del script
(véase «Fuentes: script y agente»).
La evidencia del Mac y de los servidores para los checks HOST se obtiene ejecutando en cada equipo un script que lanza un
conjunto fijo de consultas osquery y entrega un paquete que se importa en la
caja. Los checks HOST-001…009 se evalúan sobre ese paquete y sobre los resultados
del agente (véase «Fuentes: script y agente»).

Spec: [endpoint-evidence](../specs/endpoint-evidence.md).

## Qué es y qué no es

El paquete es una **atestación manual con control de integridad**: los hashes
(`SHA256SUMS` y el del paquete de consultas) detectan que un fichero se
corrompió o no corresponde a las consultas de la caja, pero **no prueban
autenticidad**. Quien ejecuta el script podría editar el resultado y
regenerar los hashes. Por eso cada resultado de los checks dice "según
atestación manual del host" y la evidencia queda con procedencia `manual` y
el nombre de quien la aporta.

## Requisitos en el equipo

- `osqueryi` instalado (https://osquery.io). Nada más: ni Python ni `jq`.
- macOS 15 o posterior necesita osquery 5.14.1 o superior para leer el
  firewall; con una versión anterior HOST-005 queda `UNKNOWN`.
- El script es de solo lectura: no usa `sudo`, ni configuración, ni
  extensiones de osquery. Si una tabla necesita privilegios, lo avisa y esa
  consulta queda sin datos.

## Paso a paso

1. **Inscribe los equipos** en `connectors/endpoint.yaml` del workspace (lo
   hace la organización, una vez por equipo):

   ```yaml
   max_evidence_age_days: 30          # antigüedad máxima: decisión de la organización
   max_signature_age_days: 7          # antigüedad máxima de las firmas antimalware (opcional; 7 por defecto)
   max_agent_silence_hours: 24        # silencio máximo del agente osquery para HOST-002 (opcional; 24 por defecto)
   max_xprotect_age_days: 30          # antigüedad máxima de XProtect en macOS (opcional; 30 por defecto)
   supported_os:                      # releases soportadas: decisión de la organización
     - {platform: darwin, name: macOS, release: "15", min_version: "15.3"}
     - {platform: darwin, name: macOS, release: "26"}
     - {platform: linux, name: Ubuntu, release: "22.04", end_of_support: 2027-04-30}
     - {platform: linux, name: Debian GNU/Linux, release: "12"}
   hosts:
     portatil-ana:                    # activo laptop o server de scope/assets.yaml
       hardware_uuid: 00000000-0000-0000-0000-000000000000
       hostname: portatil-ana
   ```

   El UUID de hardware (clave `hardware_uuid` en esta configuración) es la
   columna `uuid` de osquery: se ve con `osqueryi "select uuid from system_info"`.
   Los valores del ejemplo son ilustrativos: no hay valores por defecto.

   Cada entrada de `supported_os` es **una release** (en macOS y Debian, la
   versión mayor: `"15"`; en Ubuntu, `YY.MM`: `"22.04"`), con una versión
   mínima opcional dentro de esa release y una fecha de fin de soporte
   opcional. Una release sin entrada no se da por soportada: HOST-003 queda
   `UNKNOWN` hasta que se declare. Dos entradas para la misma release son un
   error de configuración. `name` es el `name` de la tabla `os_version` de
   osquery, sin distinguir mayúsculas (`macOS`, `Ubuntu`, `Debian GNU/Linux`):
   compruébalo con `osqueryi "select name, version from os_version"`. En
   Debian osquery no da la versión menor, así que una `min_version` como
   `"12.5"` deja HOST-003 en `UNKNOWN`; declara solo la release.

2. **Genera el script** con el paquete de consultas actual:

   ```bash
   ens-evidence endpoint script --out ./script-endpoints
   ```

3. **Ejecútalo en el equipo** (copia la carpeta entera):

   ```bash
   sh ./script-endpoints/collect-endpoint.sh
   ```

   Deja un fichero `<hostname>-<fecha>.endpoint.tar.gz`.

4. **Impórtalo en la caja**:

   ```bash
   ens-evidence evidence import portatil-ana-….endpoint.tar.gz \
     --source endpoint --asset portatil-ana --by "Nombre Apellido"
   ```

   La importación rechaza, sin guardar nada, un paquete con ficheros de más o
   de menos, enlaces, rutas raras, hashes que no cuadran, consultas distintas
   de las de la caja, fecha sin zona o futura, o un equipo que no coincide
   con el inscrito.

5. **Evalúa**:

   ```bash
   ens-evidence endpoint check [--at 2026-01-31T10:00:00+00:00]
   ```

   Lee la configuración, el inventario y la evidencia del workspace, evalúa
   HOST-001…009 con la fecha actual (`--at` fija otra, con zona horaria) e
   imprime una línea por activo y check con su estado y motivo. Si la
   evidencia de un activo está caducada, lo indica al final: bloquearía la
   preparación. Salida: `0` sin FAIL ni evidencia caducada; `4` si hay algún
   FAIL o evidencia caducada; `2` si la configuración, el inventario o los
   argumentos no son válidos.

El `.tar.gz` original se conserva en el workspace sin cambios junto al resultado
normalizado (para que `evidence verify` lo reconozca y se pueda auditar). Como no
se modifica, el script deja las cabeceras del tar sin usuario ni grupo (uid/gid 0)
y la importación rechaza los paquetes que traigan propietario. Por eso
las consultas solo piden lo que algún check o la inscripción necesitan: de la
identidad, `hostname` y `uuid`; de las políticas de actualización, solo `user_scoped` (1 si la preferencia es de un usuario), sin `username`.
No contiene nombres de usuario de las políticas; el `hostname` sí se guarda (hace
falta para atar el paquete al equipo inscrito) y a menudo incluye el nombre de
la persona, así que trátalo como dato personal. Importar dos
veces el mismo paquete para un activo se rechaza e indica la evidencia existente.

La evidencia caduca a los `max_evidence_age_days` de su recogida; caducada,
bloquea la preparación (`EXPIRED_EVIDENCE`) hasta que se vuelva a ejecutar.

## Checks

| Check | `PASS` solo si | `FAIL` si |
|-------|----------------|-----------|
| HOST-001 inventariado | el equipo del alcance está inscrito y tiene evidencia vigente | no tiene evidencia o no está inscrito |
| HOST-002 agente activo | el equipo tiene alta no revocada y su último contacto autenticado del alta vigente (evidencia `osquery-agent-contact`) es de hace `max_agent_silence_hours` o menos (24 h por defecto); cita esa evidencia | el último contacto es más antiguo que el umbral, o no hay contacto del alta vigente y esa alta (con fecha registrada) es más antigua que el umbral (el motivo da la fecha) |
| HOST-003 SO soportado | la release del equipo tiene entrada en `supported_os`, cumple su versión mínima y no ha pasado su fin de soporte | versión menor que la mínima de su release o fuera de soporte |
| HOST-004 cifrado | el volumen raíz (FileVault en macOS) está cifrado | el volumen raíz no está cifrado |
| HOST-005 firewall | macOS: firewall activo | macOS: firewall desactivado |
| HOST-006 actualizaciones | una preferencia gestionada activa la instalación automática | la preferencia gestionada la desactiva |
| HOST-007 inventario de software | hay inventario de software (con su recuento) | — |
| HOST-008 firmas al día (op.exp.6.4; laptops y servidores) | Linux: `daily.*` de ClamAV no vacío, dentro de `max_signature_age_days` y `clamav-freshclam` activo. macOS: XProtect con versión y dentro de la política. Windows: único antivirus activo con `signatures_up_to_date` = 1 | ficheros más antiguos que la política (el motivo da la fecha) o Windows con `signatures_up_to_date` = 0 |
| HOST-009 protección en tiempo real (op.exp.6.5; solo laptops) | macOS: Gatekeeper activo y XProtect presente y al día (protección integrada del sistema, cuya adecuación valora una persona). Windows: algún antivirus `On` en el Centro de seguridad | macOS: Gatekeeper desactivado. Windows: ningún antivirus `On` (todos `Off`, `Snoozed` o `Expired`) |

La antigüedad de XProtect (macOS, HOST-008 y HOST-009) usa `max_xprotect_age_days`, no `max_signature_age_days`, porque Apple publica sus actualizaciones de forma irregular. El valor por defecto de 30 días no está verificado contra la cadencia real de Apple: ajústalo con datos de tus equipos.

HOST-008 y HOST-009 con matices:

- HOST-009 no se emite para servidores. En un portátil Linux es siempre `UNKNOWN`
  (`not_supported`): ClamAV no analiza en acceso.
- La fecha de las firmas es la del fichero (`mtime`) en el momento de la recogida,
  no la versión de las firmas: osquery no lee el contenido de las bases. Fichero
  vacío, fecha futura, fecha ilegible, sin `daily.*` o `clamav-freshclam` no activo
  dan `UNKNOWN`, nunca `PASS`.
- En Windows la evidencia es la vista del Centro de seguridad
  (`windows_security_products`), no el indicador de tiempo real de Defender; que
  desactivar el tiempo real se refleje como un estado distinto de `On` no está
  verificado. Con tabla vacía (Windows Server) da `UNKNOWN`. La lógica está probada
  sobre fixtures. Con agente osquery, HOST-003…009 de Windows siguen en `UNKNOWN`
  (`not_supported`, pendientes); véase «Fuentes: script y agente».

Todo lo que no se puede demostrar da `UNKNOWN`: consulta sin filas, tabla no
disponible en ese sistema (el script marca `unsupported_hint` en `meta.json`
cuando `osqueryi` responde que la tabla o columna no existe; esa consulta
queda `NOT_SUPPORTED` y el check `UNKNOWN` con motivo `not_supported`), sin privilegios, cifrado `undefined`, mapeo del
volumen raíz ambiguo, Linux en firewall y actualizaciones (sin consulta
concluyente en v1). En HOST-007, la tabla `apps` de macOS cubre las
aplicaciones conocidas, no todo el software instalado.

Dos casos de `UNKNOWN` que afectan a varios equipos y cómo salir de ellos:

- **`unreadable_evidence`:** algún manifiesto de evidencia del workspace no se
  puede leer (fichero dañado o a medias). Como no se sabe de qué equipo es,
  todos los equipos quedan en `UNKNOWN` hasta repararlo: ejecuta
  `ens-evidence evidence verify --all` y restaura o retira el manifiesto
  dañado.
- **`no_scope`** (HOST-006): una política de actualización llegó sin saber si
  es de un usuario o de todo el equipo. Vuelve a ejecutar el script con el
  paquete de consultas actual.

## Fuentes: script y agente

Cada check HOST-003…009 se evalúa sobre una sola fuente por equipo: la que tenga la
observación más reciente de las consultas que ese check lee. Gana la más nueva
aunque dé `FAIL`; no se vuelve a la otra si la elegida está caducada (`UNKNOWN`,
`evidence_expired`). En empate gana el script. El motivo lo dice: «Según atestación
manual del host» (script) o «Según el agente osquery». HOST-001 da `PASS` si alguna
fuente está vigente y cita las que usa.

- **Caducidad por consulta.** Cada consulta del agente caduca a los
  `max_evidence_age_days` de su propia recogida o al vencimiento de su manifiesto
  (lo primero). Primero se toma la última lectura y después se valida: nunca se
  recupera una anterior. Las fechas de firmas (XProtect, ClamAV) comparan con la
  recogida de su propia consulta.
- **Consulta sin lectura del agente.** Si el agente aún no ha enviado una consulta
  que el check necesita: `UNKNOWN` con motivo `no_data`.
- **Consultas en vivo excluidas.** Las respuestas a `agent query run` no alimentan
  ningún check; solo cuentan los resultados programados.
- **Evidencia del agente sin identidad, ignorada.** La anterior a la identidad
  verificable (sin `identity_sha256`) no se puede atar a un equipo y no se usa; lo
  mismo si la identidad no coincide con el `hardware_uuid` inscrito.
- **Cambio solo de hostname.** La evaluación no lo ve; lo detecta el alta
  (`agent admit` exige uuid y hostname inscritos) cuando se vuelve a dar de alta.
- **HOST-002 solo existe por agente.** Un equipo solo con script no tiene agente:
  `UNKNOWN` con motivo `no_agent` (no se demuestra su ausencia). Sin contacto y con
  alta reciente, `UNKNOWN` (`no_contact`); contacto con fecha futura, `UNKNOWN`.
  La evidencia de contacto se escribe como mucho cada 6 h por equipo con la hora
  del servidor, así que el último contacto puede ir hasta 6 h por detrás del real.
  Por eso `max_agent_silence_hours` (por defecto 24) debe ser holgadamente mayor.
- **Readmisión.** Solo cuentan los contactos de la generación del alta vigente: tras
  `agent revoke` y `agent admit`, los contactos anteriores no cuentan y HOST-002 queda
  `UNKNOWN` (`no_contact`) aunque el equipo hablara hace minutos, hasta que el equipo
  se reinscribe; si pasan más de `max_agent_silence_hours` desde el alta nueva sin
  contacto, `FAIL` (`agent_silent`, «sin contacto desde el alta del …»).
- **Alta sin fecha.** Un registro anterior a que se guardara `last_admission_at` no tiene
  fecha de alta: sin contacto, HOST-002 se queda en `UNKNOWN` (`no_contact`) y nunca
  llega a `FAIL`, porque no se puede medir el silencio. Readmite el equipo
  (`agent revoke` y `agent admit`) para que quede fechada.
- **Windows con agente.** HOST-003…009 dan `UNKNOWN` (`not_supported`, «checks de
  Windows pendientes»); HOST-001 y HOST-002 sí se evalúan. El script de Windows no
  cambia.
- **Alcance de la lectura.** `endpoint check` y `checks run` (y con él
  `assessment status`) leen el agente: resultados, contactos y altas del registro.
  Cada ejecución de `checks run` fija (pin) el registro de altas que usó: tras revocar o
  readmitir un agente, `assessment status` da esa ejecución por desfasada
  (`check_run_missing`) hasta volver a ejecutar `checks run`. Una ejecución guardada
  antes de este cambio no lleva el registro fijado y también cuenta como desfasada: basta
  repetir `checks run` una vez.

### Ver un equipo: `agent hosts show`

```bash
ens-evidence agent hosts show <activo>
```

Solo lee. Para un activo inscrito imprime: su alta (registro); el último contacto
en evidencia (con su id) y el del estado del servicio, que se rotula como no
evidencia; la última lectura programada de cada consulta del paquete (fecha de
recogida, número de filas, id de evidencia, o «sin datos»); y el resultado de
HOST-001…009 del activo con su motivo. `ens-evidence agent hosts` sin argumentos
sigue listando los equipos como antes. Un activo no inscrito sale con código `2`.

## Sin verificar

El comportamiento de los checks con evidencia del agente se prueba sobre fixtures;
no está verificado en equipos reales (macOS, Linux ni Windows).

Las tablas y columnas de osquery del paquete v1 (marcadas `verified: false`
en `src/ens_evidence/endpoint/queries/v1/pack.yaml`) no se han contrastado
contra un osquery real ni contra todas las versiones de macOS y Linux en uso.
El script no se ha pasado por `shellcheck`.
