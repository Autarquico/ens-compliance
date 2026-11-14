# Agentes osquery: alta, operación y recuperación

Servidor de agentes (ADR 0020, [modelo de amenaza](../security/servidor-osquery.md)).
El despliegue del servicio y el enrutado de Caddy están en el
[runbook de la caja](caja.md#41-servidor-de-agentes-perfil-agents).

Los comandos `ens-evidence agent ...` se ejecutan dentro de la caja, con el
workspace montado. `admit` y `serve` necesitan los secretos de agentes, que solo
tiene el servicio `agent` (`deploy/agent.env`): ejecútalos ahí. `hosts`, `revoke`
y `query` no leen secretos y funcionan igual en `agent`, que usamos en todo el
runbook por uniformidad. Desde el directorio de la release y con `ENS_VERSION`
exportada (como en [caja.md](caja.md)):

```bash
export ENS_VERSION=v0.1.0   # la versión instalada (deploy/box.sh status)
docker compose -p ens-box --profile agents exec -T agent ens-evidence agent hosts
```

`-T` evita el pseudo-terminal, para poder capturar la salida (el token de
`admit` va solo a la salida estándar). Ayuda de cada comando:
`ens-evidence agent <comando> --help`.

## 1. Preparar el servidor

1. Genera los secretos (distintos, de al menos 32 bytes):

   ```bash
   cp deploy/agent.env.example deploy/agent.env
   chmod 600 deploy/agent.env
   openssl rand -base64 32   # pega en ENS_AGENT_ENROLL_KEY
   openssl rand -base64 32   # pega en ENS_AGENT_NODE_KEY_SECRET
   ```

   Guarda una copia fuera de la caja (gestor de contraseñas).
2. Con `deploy/agent.env` presente, `box.sh` activa el perfil `agents`:

   ```bash
   deploy/box.sh restart agent
   deploy/box.sh restart caddy
   ```
3. Publica el hostname de agentes (p. ej. `agentes.example.org`) en el túnel,
   sin Access (caja.md, 4.1).
4. Comprueba desde dentro de la red `edge` (no sale a Internet):

   ```bash
   docker compose -p ens-box exec caddy wget -q -O - \
     --header 'Content-Type: application/json' --post-data '{}' \
     http://127.0.0.1:8080/agent/enroll
   # esperado: {"node_invalid":true}
   docker compose -p ens-box exec caddy wget -S -O /dev/null http://127.0.0.1:8080/agent/enroll
   # esperado: error 403 (solo se admite POST)
   ```

Si el servicio sale con código 2, un secreto falta, es corto o ambos son
iguales. El mensaje nombra la variable, nunca el valor.

## 2. Dar de alta un equipo

El equipo debe estar inscrito en `connectors/endpoint.yaml` (uuid y hostname) y
figurar en `scope/assets.yaml`: `agent install` lee su tipo del inventario y
aborta si el activo no está.
La versión de osquery y los sha256 de sus paquetes están fijados en el código
(`src/ens_evidence/agent/install/release.yaml`, osquery 5.24.1): el script que
imprime `agent install` descarga el paquete oficial por HTTPS, verifica su
sha256 antes de instalar y escribe flags y token. No se editan flags a mano.

1. Emite el token de alta (caduca en 24 h por defecto):

   ```bash
   docker compose -p ens-box --profile agents exec -T agent ens-evidence agent admit \
     --asset portatil-01 --by "tu nombre" --hours 24
   ```

   Imprime el token una sola vez en la salida estándar. Guárdalo en un fichero
   (sin pasarlo como argumento ni por variable de entorno).
2. Mueve el token al equipo por un canal seguro (gestor de contraseñas,
   transferencia cifrada) y déjalo en un fichero que solo lean root o los
   administradores. El script rechaza un `--token-file` que sea enlace
   simbólico o no sea un fichero normal.
3. Genera el script de instalación para ese equipo y ejecútalo allí como root
   o administrador. `agent install` no lee ni escribe secretos; el script lee
   el token desde `--token-file`. Genera el script en la caja (o donde tengas
   el workspace), llévalo al equipo y ejecútalo:

   | SO | Orden de `agent install` | Ejecución en el equipo |
   |---|---|---|
   | macOS | `--os macos` | `sudo sh instalar.sh` |
   | Linux (Debian, Ubuntu) | `--os linux --package deb --arch amd64\|arm64` | `sudo sh instalar.sh` |
   | Linux (RHEL, Fedora…) | `--os linux --package rpm --arch amd64\|arm64` | `sudo sh instalar.sh` |
   | Windows (solo amd64) | `--os windows` | Consola de PowerShell como Administrador: `powershell -NoProfile -ExecutionPolicy Bypass -File .\instalar.ps1` |

   ```bash
   docker compose -p ens-box --profile agents exec -T agent ens-evidence agent install \
     --asset portatil-01 --os linux --package deb --arch amd64 \
     --server agentes.example.org --token-file /root/enroll.token > instalar.sh
   ```

   `--server` es el hostname público de agentes (sin esquema ni puerto).
   Linux exige `--package` y `--arch` explícitos: no se deducen de la caja.
   Una combinación no soportada da error con las disponibles.

   **Servidores Linux (activo de tipo `server`):** el script instala además
   ClamAV con actualización de firmas, con el gestor de paquetes de la
   distribución y antes de instalar osquery (`apt-get install -y clamav
   clamav-freshclam` y `systemctl enable --now clamav-freshclam`). Al inicio,
   antes de tocar nada, comprueba que hay `apt-get`. En un servidor RPM
   (`--package rpm`) el script se detiene sin instalar nada: el instalador no
   añade repositorios; habilita EPEL, instala `clamav` y `clamav-update` a mano,
   activa la actualización de firmas y vuelve a ejecutar el script. Portátiles,
   macOS y Windows no instalan ClamAV. Instalar ClamAV no acredita que la
   protección frente a código dañino se cumpla (ver `kit-antimalware`).

   Qué hace el script (todo o nada: un paso fallido aborta antes de arrancar
   el servicio): descarga a un directorio temporal privado, verifica el
   sha256 del mismo fichero que instala (aborta si no coincide, si la descarga
   está truncada o si falta una herramienta), lee el token a una copia
   en el directorio temporal privado (aborta si queda vacío, antes de tocar
   el destino), instala, para el servicio y verifica que está parado (el
   instalador de osquery puede arrancarlo brevemente, sin los flags ni el
   token de esta instalación; en reinstalación, con los flags previos; si no
   se para, el script aborta antes de escribir flags y token), crea el destino
   del token con permisos restrictivos (en Windows, DACL sin herencia solo
   para SYSTEM y Administradores), le copia el token desde la copia temporal y
   arranca el servicio. En Windows, cualquier fallo tras el msi intenta
   dejarlo parado y deshabilitado y, si no puede, lo comunica como error.

   | SO | Servicio | Flags | Token copiado para osquery |
   |---|---|---|---|
   | macOS | `io.osquery.agent` (LaunchDaemon) | `/private/var/osquery/osquery.flags` | `/etc/osquery/enroll_secret` (600, root:wheel) |
   | Linux | `osqueryd` (systemd) | `/etc/osquery/osquery.flags` | `/etc/osquery/enroll_secret` (600, root) |
   | Windows | `osqueryd` (servicio) | `C:\Program Files\osquery\osquery.flags` | `C:\Program Files\osquery\enroll_secret` (solo SYSTEM y Administradores) |

   En Windows, `.\instalar.ps1` a secas falla con la política de ejecución
   por defecto. `-ExecutionPolicy Bypass -File` la omite solo en esa
   invocación: no cambia la política del equipo y no se recomienda
   cambiarla de forma permanente. No verificado en un Windows real (piloto
   pendiente).

   Los flags clave son `--host_identifier=uuid` (obligatorio: el servidor
   exige el uuid de hardware inscrito; con `hostname` el alta responde
   `node_invalid` sin más pista), los endpoints `/agent/*` por TLS y, para
   no perder datos ni saturar el servidor tras un corte, `--buffered_log_max=10000`
   (registros; purga los más antiguos), `--logger_tls_period=60` y
   `--logger_tls_backoff_max=3600`. **Prohibidos:** `--logger_tls_compress`
   (Caddy responde 415), `--tls_secret_always` y `--enroll_secret_env`.
4. Comprueba. En la caja:

   ```bash
   docker compose -p ens-box --profile agents exec -T agent ens-evidence agent hosts
   ```

   El equipo aparece con su último contacto y `admitido`. En el equipo,
   según el SO (esta es la única comprobación por SO; los pilotos remiten
   aquí):

   | SO | Servicio y versión |
   |---|---|
   | macOS | `sudo launchctl print system/io.osquery.agent`; `osqueryd --version`; que el `.pkg` dejó `io.osquery.agent.plist` donde asume el script (no verificado en equipo real) |
   | Linux | `systemctl status osqueryd`; `osqueryd --version` |
   | Windows | `Get-Service osqueryd`; `icacls` sobre `enroll_secret`; versión del msi en Aplicaciones (no verificado en equipo real) |

   El primer snapshot
   programado llega a las ~6 h de arrancar osquery (con splay del 10 %); para
   tener datos antes, lanza una consulta en vivo (sección 3).
5. **Retira el token del equipo en cuanto aparezca en `agent hosts`:** borra
   el fichero que pasaste a `--token-file` (y su copia en el gestor de
   contraseñas o el canal de transferencia). La copia que escribió el script
   (`enroll_secret`, tabla anterior) la lee osquery solo para inscribirse y
   queda protegida; caduca con el token, no se reutiliza y se sobrescribe
   en una nueva alta.

Un token caducado o de otra generación da `node_invalid`: vuelve a ejecutar
`agent admit`.

### Readmisión tras perder el estado

Si el equipo pierde la base de datos de osquery (reinstalación del sistema, borrado de
`/var/osquery` o `C:\Program Files\osquery\osquery.db`) o el servidor
pierde su estado (sección 7), el equipo debe inscribirse de nuevo:
`agent admit` (token nuevo), llévalo al equipo y vuelve a ejecutar el script
de la tabla anterior. El script es repetible: reinstala la misma versión
(`rpm -U --replacepkgs`, `dpkg -i`), reescribe flags y token y reinicia el servicio.
El token se lee antes de escribir el destino, así que `--token-file` puede ser
incluso el propio destino: el fichero acaba con el token. Un equipo revocado exige
también `agent admit` (sección 4).

### Actualizar osquery

La versión se cambia en el repo, no en el equipo: una release nueva sube
`release.yaml` (versión, URLs y sha256 de la API de releases de GitHub,
`gh api repos/osquery/osquery/releases/tags/<versión>`) y pasa por revisión.
Con la caja actualizada (`deploy/box.sh update <vX.Y.Z>`), repite en cada
equipo los pasos 1 a 3 (token nuevo: el anterior habrá caducado) y el paso 5.
El script reinstala encima (dpkg, rpm, `installer`, `msiexec`) y reescribe
flags y token. Comprueba la versión
tras actualizar: `osqueryd --version` (Linux, macOS) o
`Get-Service osqueryd` y la versión del msi en Aplicaciones (Windows; la ruta del binario no está verificada).

### Desinstalar

Antes, revoca el equipo en el servidor (sección 4) y retira el activo de
`connectors/endpoint.yaml` si ya no se evalúa.

- **Linux:** `sudo systemctl disable --now osqueryd`; `sudo dpkg --purge osquery`
  (deb) o `sudo rpm -e osquery` (rpm); `sudo rm -rf /var/osquery /etc/osquery`.
- **macOS:** `sudo launchctl bootout system/io.osquery.agent`;
  `sudo rm /Library/LaunchDaemons/io.osquery.agent.plist`;
  `sudo rm -rf /opt/osquery /private/var/osquery /etc/osquery /var/log/osquery`;
  `sudo pkgutil --forget io.osquery.agent`. Los ficheros exactos de macOS no están
  verificados en un equipo real (pendiente del piloto).
- **Windows (Administrador):** `Stop-Service osqueryd`; desinstala el msi
  (Configuración, Aplicaciones, osquery; o `msiexec /x` con el msi de la
  release); borra `C:\Program Files\osquery` si queda algo (incluye
  `enroll_secret`).

### Migración al paquete v2

El paquete de consultas pasó a v2 (consultas de Windows y la columna
`windows` en las genéricas). Los equipos con agente reciben la
configuración nueva solos (`--config_refresh=3600`). Un paquete de script
(`collect-endpoint`) **v1 pendiente de importar se rechaza** con un mensaje
que pide repetir la recogida con el script nuevo: no se regenera, se vuelve
a ejecutar el script v2 en el equipo y se importa el paquete nuevo. La
evidencia `endpoint-script` v1 ya guardada sigue evaluándose.

### Pilotos reales pendientes

Esta versión se ha probado con osquery 5.24.1 en un contenedor Linux (test
`tests/agent/test_osquery_e2e.py`, `pytest -m docker`), con las sustituciones
que cita su cabecera. osquery 5.24.1 no ejecuta el calendario al arrancar: la
primera pasada programada llega tras un intervalo con splay (en torno a 6 h),
así que el test comprueba el camino de resultados con una consulta en vivo y
la primera pasada programada real se confirma en los pilotos. **Quedan pendientes de aceptación, con responsable el
usuario, los pilotos reales** (no se dan por hechos por el test del
contenedor; el script de macOS y el de Windows no se han ejecutado nunca en
un equipo real). Evidencia que adjuntar al cerrar cada uno: la comprobación
del servicio por SO (paso 4 de la sección 2) más lo que añade la tabla:

| Piloto | Evidencia adicional |
|---|---|
| macOS real (instalar osquery cambia el sistema: requiere aprobación) | Id de la evidencia `osquery-agent` y su snapshot |
| Linux real (deb o rpm, con systemd) | Id del snapshot |
| Windows (VM) | Id del snapshot con filas de `bitlocker_info`, `windows_security_center` y `programs`; que `--tls_server_certs` por defecto valida el certificado de Caddy; que `-ExecutionPolicy Bypass -File` ejecuta el script |

**Evidencia pendiente de «caja caída» (no verificada).** El test del
contenedor mide que el servidor no se degrada y que la consulta en vivo
responde tras el corte; no verifica el primer snapshot programado ni la
entrega de atrasos del búfer, porque el intervalo es fijo de 6 h
(`src/ens_evidence/agent/osquery_config.py`). En al menos el piloto de Linux
real, adjuntar:

1. Primer snapshot programado: salida de `agent hosts` y el id del snapshot
   unas ~6 h después del alta (sin consulta en vivo de por medio).
2. Corte del servidor de varios intervalos (más de 6 h) y recuperación:
   salida de `agent hosts` antes y después, e ids de los snapshots que
   llegan como atrasos y del snapshot actual guardado (cotas en la sección
   3; las descartadas aparecen como `outcome=throttled` en el log de `agent`).

## 3. Consultas en vivo

Solo consultas del paquete, por id. Nunca SQL arbitrario.

```bash
docker compose -p ens-box --profile agents exec -T agent ens-evidence agent query list
docker compose -p ens-box --profile agents exec -T agent ens-evidence agent query run \
  --asset portatil-01 --query <id-consulta> --by "tu nombre"   # o --all en vez de --asset
docker compose -p ens-box --profile agents exec -T agent ens-evidence agent query show <run_id>
```

`query list` muestra las vigentes; añade `--all` para ver también las
caducadas. `--expires` admite hasta 7d (168h), igual que `--hours` del alta.
El equipo la recoge en su siguiente sondeo distribuido y el resultado queda
como evidencia, visible con `query show`.

El servidor guarda como máximo un snapshot por consulta del paquete y equipo
cada 6 h según la hora en que osquery lo generó (con margen para el splay del
10 % y 5 min de desfase), y como mucho 3 entregas atrasadas por consulta y
equipo cada 6 h; el snapshot actual siempre se guarda (en total, como mucho 6
por consulta y equipo cada 6 h). Los POST separados que osquery envía por consulta y los atrasados tras un
corte se guardan; un snapshot repetido o más viejo que el guardado responde
200 y se descarta (`outcome=throttled` en el log). Las consultas en vivo no están sujetas a esta
cota. Las peticiones autenticadas registran `asset=<activo> host=<hash>`.

## 4. Revocar un equipo

Equipo perdido, comprometido o dado de baja:

```bash
docker compose -p ens-box --profile agents exec -T agent ens-evidence agent revoke \
  --asset portatil-01 --by "tu nombre" --reason "equipo perdido"
```

Sube la generación: el node_key anterior deja de valer en la siguiente
petición (el registro no se cachea). Para volver a admitirlo, ejecuta
`agent admit`.

## 5. Rotar secretos

| Secreto | Efecto | Procedimiento |
|---|---|---|
| `ENS_AGENT_ENROLL_KEY` | Invalida los tokens de alta pendientes; los equipos ya dados de alta siguen | Cambia el valor, `deploy/box.sh restart agent`, reemite con `agent admit` los pendientes |
| `ENS_AGENT_NODE_KEY_SECRET` | Invalida **todos** los node_key | Cambia el valor, `restart agent`, y readmite todos los equipos (`agent admit` por cada uno) |

Genera cada valor con `openssl rand -base64 32`; siguen siendo distintos entre sí.

## 6. Volver atrás

`deploy/box.sh rollback` a una versión sin servicio `agent` detiene el
contenedor sin borrarlo (caja.md, 4.1). Los POST a `/agent/*` pasan a 403: osquery
interpreta el fallo, guarda en búfer y reintenta; no se pierden los resultados
pendientes del equipo hasta el límite de su búfer. Al volver a la versión con
`agent`, los equipos retoman solos. Para desactivar el servidor sin volver
atrás: mueve `deploy/agent.env` y `docker compose -p ens-box stop agent`.

## 7. Recuperación

- **Se pierde `deploy/agent.env`:** genera secretos nuevos (sección 1), reinicia
  `agent` y readmite todos los equipos. Los node_key antiguos no valen.
- **Restaurar la copia del workspace:** el registro `agents/registry.yaml`
  vuelve a un estado anterior y las generaciones **bajan**: un equipo
  revocado después de la copia volvería a ser válido. Tras restaurar,
  revoca o readmite todos los equipos antes de reabrir el hostname de
  agentes (`agent revoke` y después `agent admit`).
- **Lock ocupado (503 sostenido):** hay un ciclo o una copia largos
  (`deploy/box.sh status`). osquery reintenta solo.

## Qué no hace

No declara conformidad: los resultados son evidencias del equipo y las
revisa una persona.
