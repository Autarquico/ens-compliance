# Evidencia de endpoints con script guiado

En v0.1 no hay agente ni servidor osquery. La evidencia del Mac y de los
servidores se obtiene ejecutando en cada equipo un script que lanza un
conjunto fijo de consultas osquery y entrega un paquete que se importa en la
caja. Los checks HOST-001…007 se evalúan sobre ese paquete.

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
| HOST-002 agente activo | — (siempre `UNKNOWN` en v0.1: no hay agente) | — |
| HOST-003 SO soportado | la release del equipo tiene entrada en `supported_os`, cumple su versión mínima y no ha pasado su fin de soporte | versión menor que la mínima de su release o fuera de soporte |
| HOST-004 cifrado | el volumen raíz (FileVault en macOS) está cifrado | el volumen raíz no está cifrado |
| HOST-005 firewall | macOS: firewall activo | macOS: firewall desactivado |
| HOST-006 actualizaciones | una preferencia gestionada activa la instalación automática | la preferencia gestionada la desactiva |
| HOST-007 inventario de software | hay inventario de software (con su recuento) | — |

Todo lo que no se puede demostrar da `UNKNOWN`: consulta sin filas, tabla no
disponible en ese sistema, sin privilegios, cifrado `undefined`, mapeo del
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

## Sin verificar

Las tablas y columnas de osquery del paquete v1 (marcadas `verified: false`
en `src/ens_evidence/endpoint/queries/v1/pack.yaml`) no se han contrastado
contra un osquery real ni contra todas las versiones de macOS y Linux en uso.
El script no se ha pasado por `shellcheck`.
