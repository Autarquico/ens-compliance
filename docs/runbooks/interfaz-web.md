# La interfaz web: desplegar, comprobar el acceso, volver atrás y recuperar

La interfaz (ADR 0021) corre en el servicio `ui` de la caja, con la misma imagen
que `app`. No tiene contraseñas ni sesiones propias: la identidad es el JWT que
firma Cloudflare Access (`Cf-Access-Jwt-Assertion`). **El MFA lo impone la
política de Access, no la aplicación**: si la política no lo exige, nadie lo
exige, y el token no lleva una prueba de que se usó (sección 3). Quién puede
escribir y con qué rol ENS lo decide `settings/ui-roles.yaml` (sección 7;
ADR 0022). Esta guía parte de una caja instalada (`docs/runbooks/caja.md`).

Los nombres de este documento son ficticios: `ui.example.org`,
`agents.example.org`, `example.cloudflareaccess.com`.

## 1. Qué hay en la caja

| Pieza | Papel |
|---|---|
| `ui` (perfil `ui`) | Sirve la interfaz en `ui:8000`. Red `ui-net` (interna, sin salida a Internet). Lee solo `deploy/ui.env`. |
| `caddy` | Enruta por nombre de host: `ENS_UI_HOST` a `ui`, `ENS_AGENT_HOST` a los agentes. Host desconocido o vacío, y cualquier otra ruta: 403. Conserva `Cf-Access-Jwt-Assertion` hacia `ui` y lo quita hacia `agent`. |
| `app` | Además del scheduler, descarga los certificados de Access y los publica en el volumen `access-jwks` (sección 4). |
| Cloudflare Access | Autentica y exige MFA delante del hostname de la interfaz. |

`ui` se niega a arrancar si el workspace es S3 (la interfaz solo trabaja sobre
carpeta local). Escribe solo bajo una lista blanca de prefijos del workspace
(`scope/`, `controls/`, `risk/`, `org/`, evidencias de usuario…), nunca en
`connectors/` ni en `settings/`.

## 2. Desplegar

1. **Aplicación de Access.** En Cloudflare Zero Trust, Access, Applications,
   crea una aplicación autoalojada para `ui.example.org`, con una política que
   permita solo a las personas autorizadas **y exija MFA** (ver sección 3).
   Apunta dos datos de la aplicación: el **Application Audience (AUD) tag**
   y el dominio del team (`example.cloudflareaccess.com`).
2. **Hostname del túnel.** En el túnel de la caja (sección 4 de `caja.md`),
   añade el hostname público `ui.example.org` hacia `http://caddy:8080`. Es
   distinto del hostname de los agentes.
3. **`deploy/ui.env`** (modo 600) desde `deploy/ui.env.example`:

   ```bash
   cp deploy/ui.env.example deploy/ui.env && chmod 600 deploy/ui.env
   ```

   | Variable | Valor |
   |---|---|
   | `ENS_UI_HOST` | `ui.example.org` (sin esquema ni puerto; `box.sh` lo valida y se lo pasa a Caddy) |
   | `ENS_UI_ORIGIN` | `https://ui.example.org` |
   | `ENS_UI_ACCESS_AUD` | el AUD tag de la aplicación |
   | `ENS_UI_ACCESS_TEAM_DOMAIN` | `example.cloudflareaccess.com` |
   | `ENS_UI_ACCESS_JWKS_FILE` | `/run/access-jwks/access-jwks.json` (volumen `access-jwks`, de solo lectura en `ui`) |

   El servicio no lee `deploy/.env`: no recibe las credenciales de los conectores.
4. **Hostname de los agentes** (solo si usas el perfil `agents`): añade
   `ENS_AGENT_HOST=agents.example.org` a `deploy/agent.env`. Sin él, Caddy
   deja de enrutar los agentes (host `agent.invalid`).
5. **Certificados de Access** (sección 4). Añade a `deploy/.env` (el que lee
   `app`) `ENS_ACCESS_JWKS_TEAM_DOMAIN=example.cloudflareaccess.com` y
   `ENS_ACCESS_JWKS_FILE=/run/access-jwks/access-jwks.json`. Sin ellos `app` no
   publica nada y la interfaz arranca pero responde 401 a todo.
6. **Roles** (sección 7). Sin ninguno asignado, todo el mundo solo consulta.
7. **Arrancar.** Con `deploy/ui.env` presente `box.sh` activa el perfil `ui`.
   Caddy tiene que releer los nombres de host:

   ```bash
   deploy/box.sh restart ui
   deploy/box.sh restart caddy
   deploy/box.sh status          # ui y caddy: healthy
   ```

   Una `update` posterior también arranca `ui` y exige que esté sano.

## 3. Comprobar que Access exige MFA

La interfaz **no puede comprobar el MFA por petición**: la documentación de
Cloudflare no describe un claim de segundo factor en el token de aplicación de
Access (revisado el 2026-11-16; ADR 0022), así que no se acepta ni se rechaza
por `amr` ni por otro claim. Lo que se comprueba es la **política** de Access.
Hazlo antes de dar la dirección a nadie, y de nuevo tras cualquier cambio de
política o de proveedor de identidad (IdP). Cómo dar de alta Workspace o Entra ID
y restringir la aplicación a ese proveedor:
[acceso con proveedor de identidad](acceso-con-proveedor-de-identidad.md).

1. **En el panel de Cloudflare.** Zero Trust, Access, Applications,
   `ui.example.org`, pestaña Policies. Toda política `Allow` debe exigir MFA:
   una regla `Require` de **Authentication method** con MFA o el ajuste de MFA
   de la política o de la aplicación; o bien un IdP que ya impone MFA a todos
   sus usuarios (compruébalo en el propio IdP, sin excepciones para las cuentas
   autorizadas). Una política `Allow` por correo o dominio sin una de esas
   cosas **no** exige MFA. Ninguna política `Bypass` ni `Service Auth` sobre
   este hostname.
2. **Que el producto lo compruebe.** En `connectors/cloudflare.yaml` añade el
   hostname de la interfaz (sin esquema, puerto ni ruta):

   ```yaml
   ui_hostname: ui.example.org
   ```

   Después recoge de nuevo la evidencia (los campos nuevos solo se guardan al
   recoger) y ejecuta los checks:

   ```bash
   ens-evidence collect cloudflare
   ens-evidence check --all
   ```

   Resultado de `cloudflare.ui_access_requires_mfa`: `PASS` si toda política
   `Allow` que alcanza el hostname exige MFA; `FAIL` si hay una `Bypass` o una
   `Allow` sin MFA (el motivo nombra la política); `UNKNOWN` si falta
   `ui_hostname` (`ui_hostname_not_configured`), no hay una aplicación que
   cubra el hostname entero, algún dato no se pudo leer, la evidencia es
   anterior a este check (`stale_evidence`: recoge de nuevo) o hay una política
   por token de servicio que una persona debe confirmar que es solo para
   máquinas. La ausencia de datos nunca es `PASS`. El check no está vinculado a
   ninguna medida ENS; lo vincula una persona con `ens-evidence matrix link`.
3. **Qué significa el aviso de MFA.** Mientras el último resultado guardado de
   `cloudflare.ui_access_requires_mfa` no sea `PASS`, todas las páginas de la
   interfaz muestran el aviso «MFA no confirmada para esta interfaz», con el
   estado y el código del motivo (`ui_hostname_not_configured`, `stale_evidence`,
   `not_checked` si no hay ninguna recogida, `unreadable` si el historial no se
   puede leer…). Significa que la política no lo exige, que no se ha podido
   comprobar o que no hay recogida reciente. No dice que alguien haya entrado sin
   MFA. Con `PASS` no se muestra nada, y eso tampoco prueba que el MFA esté
   activo para cada persona. El aviso se calcula leyendo el historial de checks;
   desaparece tras una recogida y un `ens-evidence check --all` con `PASS`. Corrige la política, o `ui_hostname`, y repite
   el paso 2. Un `PASS` tampoco prueba que cada persona haya enrolado el factor:
   eso se evidencia en el proveedor (`collector-workspace`, `collector-m365`).
4. **Sin cookie ni sesión.** Desde una máquina fuera de la red de la caja:

   ```bash
   curl -sS -o /dev/null -D - https://ui.example.org/ | grep -iE '^(HTTP|location)'
   ```

   Se espera una redirección (302) a
   `https://example.cloudflareaccess.com/cdn-cgi/access/login/...`, no contenido
   de la interfaz. Con 200 y contenido, quita el hostname del túnel y avisa al
   responsable del sistema.
5. **Con sesión.** Entra desde un navegador, inicia sesión y comprueba que el
   IdP pide el segundo factor. Si no lo pide, la política no lo exige: corrígela
   antes de seguir.
6. **Falsificación.** Una petición con una cabecera `Cf-Access-Jwt-Assertion`
   inventada que llegue a Caddy sin pasar por Access (solo posible desde la red
   `edge`) recibe 401 de la aplicación: el JWT se verifica con la firma,
   `iss`, `aud` y `exp`.

**No verificado:** la forma exacta con que la API de Cloudflare expresa la MFA
(el selector `Require` de método de autenticación y `mfa_config`) está tomada
de la documentación y solo se ha probado con datos de ejemplo. Esta guía no se
ha ejecutado contra una cuenta real: la comprobación completa queda para
`validation-selfassessment`.

## 4. Certificados de Access (JWKS)

`ui` no tiene salida a Internet y no puede descargar las claves con las que
Access firma los JWT. Lo hace `app`: al arrancar, cada 6 horas y, tras un
fallo, a los 15 minutos, descarga
`https://example.cloudflareaccess.com/cdn-cgi/access/certs` y publica el
fichero `/run/access-jwks/access-jwks.json` en el volumen `access-jwks`, que
`ui` monta solo en lectura. Solo acepta el host `<equipo>.cloudflareaccess.com`,
sin redirecciones, con límites de tamaño, número de claves (20) y fuerza de la
clave (RSA, al menos 2048 bits). Si algo falla conserva el fichero anterior y
deja un código en el log de `app` (`docker compose -p ens-box logs app | grep
JWKS`). Se activa con `ENS_ACCESS_JWKS_TEAM_DOMAIN` y `ENS_ACCESS_JWKS_FILE` en
`deploy/.env`; sin ellas no se registra el trabajo.

`ui` relee el fichero en cada petición: no hace falta reiniciar. Sin un
fichero válido nadie entra (401): la interfaz falla cerrada. Tras la primera
instalación el fichero no existe hasta el primer refresco de `app`.

**Refresco manual**, tras una rotación de claves o al instalar:

```bash
docker compose -p ens-box exec app ens-evidence scheduler refresh-access-jwks
```

Sale con 0 si publica y con 2 si falla (imprime el código del fallo y conserva
el fichero anterior).

**401 a usuarios que sí pasan por Access, y `unknown_kid`.** Tras una rotación
Access firma con una clave que `ui` aún no tiene (`unknown_kid` en el
verificador). La respuesta 401 de `ui` es genérica,
pero deja el motivo en su log, con un código estable y sin el token ni sus datos:
`docker compose -p ens-box logs ui | grep 'acceso rechazado'` (por ejemplo
`acceso rechazado (401): unknown_kid`; otros códigos: `missing`, `bad_signature`,
`expired`, `jwks_unavailable`). Ejecuta el refresco manual; si el 401 persiste,
comprueba el log de `app` (`JWKS`), el dominio del team y el reloj del host. Sin refresco, el siguiente
automático llega en 15 min si el anterior falló y en 6 h si tuvo éxito.

## 5. Volver atrás

- **Desactivar la interfaz sin tocar nada más.** Mueve `deploy/ui.env` fuera
  de `deploy/` y ejecuta `docker compose -p ens-box stop ui`. Quita el hostname
  del túnel si la desactivación va a durar. Los datos del volumen no se tocan.
- **Volver a una versión anterior.** `deploy/box.sh rollback`. Si esa versión
  no define el servicio `ui`, `box.sh` para el contenedor huérfano sin borrarlo
  ni tocar volúmenes. El Caddyfile vuelve al de esa versión.
  **Qué pasa con `settings/ui-roles.yaml`:** está en el workspace, no en la
  imagen, y el rollback no lo toca. Una versión anterior a los roles no lo lee
  (no lo borra) y vuelve a dejar escribir a cualquiera que Access deje entrar,
  sin roles ENS: es el comportamiento del ADR 0021. Al volver a actualizar, el
  fichero sigue vigente. El volumen `access-jwks` tampoco se borra; una versión
  anterior no lo monta, así que, si lee
  `ENS_UI_ACCESS_JWKS_FILE=/data/state/ui/access-jwks.json`, hay que
  aprovisionar ese fichero a mano o la interfaz responderá 401 a todos.
- **Revocar el acceso de una persona.** En Access, quita a la persona de la
  política y revoca su sesión (Users, Revoke). La aplicación no guarda sesiones.

## 6. Recuperar

- **Quién cambió qué.** `docker compose -p ens-box logs ui | grep 'ens_evidence.ui.audit'`:
  una línea por escritura con `identity=<email>`, paso, acción, roles usados,
  claves y hora; también las operaciones denegadas (`outcome=denied`, con el
  motivo). No incluye valores del formulario.

- **La interfaz responde 401 a todos.** Es lo esperado sin JWKS válido, con
  `ENS_UI_ACCESS_AUD` o el dominio del team equivocados, o con el reloj del
  host desviado más de 60 s. Revisa la sección 4 (refresco del JWKS) y
  `docker compose -p ens-box logs ui` (no imprime valores de configuración).
- **403 de Caddy en el hostname de la interfaz.** `ENS_UI_HOST` de
  `deploy/ui.env` no coincide con el hostname publicado, o Caddy no ha releído
  el valor: `deploy/box.sh restart caddy`.
- **`ui` no arranca.** Falta alguna variable de `deploy/ui.env` o el workspace
  es S3. El error sale con código 2 y no imprime valores.
- **Se perdió el volumen o el workspace.** La interfaz no guarda nada propio:
  restaura el workspace desde la copia (`docs/runbooks/restauracion-de-evidencias.md`),
  ejecuta el refresco del JWKS (sección 4), revisa `settings/ui-roles.yaml`
  (`ui roles list`; viaja con el workspace) y `deploy/box.sh restart ui`.
- **Escrituras de la interfaz sin copia.** Se hacen sin tomar el lock del ciclo
  y de forma atómica; la siguiente copia detecta el cambio y la reintenta.

## 7. Roles: quién edita y quién firma

Sin una asignación, la interfaz es de **solo lectura para todo el mundo**. La
asignación vive en `settings/ui-roles.yaml` y **solo se edita con la CLI**: la
interfaz no puede escribirla, y la lee en cada petición, así que un cambio o una
revocación se aplica en la siguiente. Los correos se comparan sin distinguir
mayúsculas ni espacios. Si el fichero no es válido, nadie edita hasta que se
corrija.

```bash
docker compose -p ens-box exec app ens-evidence ui roles list
docker compose -p ens-box exec app ens-evidence ui roles grant ana@example.org --edit
docker compose -p ens-box exec app ens-evidence ui roles grant ana@example.org --role security_officer
docker compose -p ens-box exec app ens-evidence ui roles grant luis@example.org --edit --role system_owner --role management
docker compose -p ens-box exec app ens-evidence ui roles revoke ana@example.org --role security_officer
docker compose -p ens-box exec app ens-evidence ui roles revoke ana@example.org --no-edit
docker compose -p ens-box exec app ens-evidence ui roles revoke ana@example.org
```

`grant` suma a lo que la persona ya tenía; `--role` es repetible. `revoke` sin
opciones quita todo; `--role` quita solo ese rol; `--no-edit` quita solo la
edición. Para firmar hace falta `--edit` **y** el rol: el rol solo no basta.
Roles ENS: `system_administrator`, `system_administrator_delegate`,
`security_officer`, `system_owner`, `management`, `information_owner`,
`service_owner`. Qué rol pide cada firma está en el ADR 0022; en la interfaz,
quien no lo tiene ve que necesita el rol y la orden que tendría que pedir.
Enviar a una pre-auditoría externa y decidir sus hallazgos piden
`security_officer` en la interfaz (política de la interfaz, no de la CLI);
una petición ya encolada se ejecuta aunque después se revoque el permiso.
Cambiar quién aprueba los procedimientos (bloque «Responsables» de los
documentos) pide `management`; sin él, ese campo queda inactivo y el resto del
bloque se guarda con la edición. Qué rol pide cada documento está en la
sección 12.

Asignar un rol indica que esa persona puede firmar, no que sea quien firma: el
nombre de `by` lo escribe quien rellena el formulario (el ADR 0021 hablaba de
prerrellenarlo con el correo; no se hace). Quién operaba lo dice el log de
auditoría (sección 6). Los roles no sustituyen la revisión humana: la
herramienta nunca declara conformidad.

**Límite que debes conocer.** La protección del fichero es de código (la lista
blanca de escritura), no de aislamiento: `ui` comparte imagen y usuario con
`app` y monta `data` con escritura. Quien ejecutara código dentro de `ui`
podría reescribirlo. El aislamiento real está pendiente (ADR 0022).

## 8. Al actualizar desde una versión sin roles

Tras actualizar, **todo el mundo queda en solo lectura** hasta que asignes roles
(sección 7): quien antes escribía recibe un 403 que explica cómo pedirlo, sin
guardar nada. Antes de actualizar, o justo después:

1. Decide quién edita y qué rol ENS tiene cada persona (las firmas siguen
   siendo decisión de personas).
2. Ejecuta los `ui roles grant` de la sección 7 y `ui roles list`.
3. La matriz ya no se inicializa al abrirla: la primera vez, una persona con
   edición la inicia desde la interfaz (`POST /matriz/iniciar`).
4. Cambia `ENS_UI_ACCESS_JWKS_FILE` en `deploy/ui.env`, añade las variables de
   `app` (sección 2, pasos 3 y 5) y ejecuta el refresco manual (sección 4).
5. Configura `ui_hostname` y recoge de nuevo (sección 3, paso 2).

## 9. Panel de agentes

`/agentes` lista los equipos con agente osquery inscritos (estado del alta,
último contacto registrado en la evidencia y resultado de HOST-002) y
`/agentes/{activo}` da el detalle, como `ens-evidence agent hosts show`. Es de
solo lectura, no tiene acciones y no muestra datos en bruto de osquery. Un
activo no inscrito da 404. No muestra el contacto «según el servicio»: el estado
del servicio de agentes no está al alcance de `ui`.


## 10. Conectores

`/conectores` muestra un bloque por conector registrado: si su
`connectors/<name>.yaml` falta, carga o no es válido, la última recogida
importada (fecha, estado, estado por sección y motivo del fallo en categorías
fijas) y cómo poner su credencial, con enlace a `/ayuda/conector-<name>`.
`/conectores/{conector}` es la ficha; con permiso de edición tiene el
formulario de configuración. Las copias (`backups`) son de solo lectura y
enlazan a `/copias` y `/herramientas/copias`.

- La web no ve, pide ni guarda credenciales: van en `deploy/.env` de la caja.
  Tampoco puede saber si están puestas; se ve en la última recogida.
- Guardar escribe solo `connectors/<name>.yaml` y se audita. Un valor con forma
  de secreto se rechaza sin repetirlo.
- Si el fichero actual no es válido, guardar pide confirmar que se sustituye.
- Sin recogidas, sin configuración o sin evidencia no es un estado favorable.

Nada de esto se ha verificado con Docker ni con Cloudflare reales, salvo el
enrutado por host de Caddy y la validación del Caddyfile.

## 11. Recoger ahora

Cada conector configurado de `/conectores` (los del catálogo y los registrados
como plugin) tiene el botón «Recoger ahora». La
web no recoge: solo deja una petición y el scheduler del servicio `app` la
atiende (la web no tiene credenciales ni salida a Internet).

- **Qué escribe.** `evidence/collect-requests/<conector>/<n>.json` con el
  conector, el correo de quien lo pide y la hora. El scheduler añade
  `<n>.started.json` al reclamarla y `<n>.result.json` al terminar. El
  resultado guarda estado, hora e identificador de la ejecución, sin mensajes
  en bruto ni credenciales.
- **Quién puede pulsarlo.** Solo quien tiene permiso de edición (sección 7).
  Sin él, el POST da 403 y queda auditado sin escribir nada.
- **Una petición viva por conector.** Si ya hay una pendiente o en curso, no se
  crea otra y la página lo dice. Una caducada o terminada no bloquea.
- **Cuándo se atiende.** En cuanto el scheduler queda libre, normalmente en
  menos de un minuto. No hay garantía de 60 s: si el ciclo diario está en
  marcha (o hay otra petición ejecutándose), espera al lock del ciclo y no
  gasta intentos.
- **Un solo intento.** Recoge ese conector y después evalúa sus checks, sin
  los reintentos del ciclo diario. El tiempo límite es `collect.timeout_minutes`
  de `reports/schedule.yaml` (15 por defecto, máximo 240) y el lock del ciclo
  está tomado durante toda la ejecución: el ciclo diario y otras peticiones
  esperan hasta ese tiempo.

### Estados

| Estado | Significa |
|---|---|
| Pendiente | Escrita y sin reclamar. Se atenderá en cuanto el servicio esté libre. |
| En curso | El scheduler la reclamó (`.started.json`) y aún no hay resultado. |
| Terminada | Hay `.result.json`. Neutral: no significa que el conector esté bien; lo dice «Última recogida». |
| Terminada con error de recogida | Se ejecutó pero la recogida falló; queda una evidencia en `ERROR`. |
| Terminada; comprobaciones no registradas | Se recogió, pero los checks no pudieron evaluarse o registrarse. |
| No se pudo hacer | No se ejecutó (sin configuración, conector desconocido, configuración inválida, fallo interno) o se interrumpió. |
| Caducada | Pasaron 24 h sin que nadie la reclamara. No se ejecutará. |

### Una petición caducada

El servicio no la atendió en 24 h, así que probablemente `app` no está
funcionando. Comprueba el scheduler y pide la recogida de nuevo:

```bash
docker compose -p ens-box exec app ens-evidence scheduler status
docker compose -p ens-box logs app
docker compose -p ens-box exec app stat -c %y /tmp/scheduler-heartbeat
```

Si el latido es antiguo, mira `docs/runbooks/caja.md`, sección 9, y arranca
`app` de nuevo. Después pulsa «Recoger ahora»; la caducada no se reutiliza.

### INTERRUPTED

La petición estaba en curso y el servicio se paró (reinicio, caída) antes de
escribir el resultado. Al recuperarse, el scheduler la cierra como
`INTERRUPTED` («la ejecución se interrumpió») sin reintentarla, también si han
pasado más de 24 h desde que se reclamó. Lo ya importado queda como en
`collect`. Pide otra recogida.

### Caducada al reclamar

Admitir una petición nueva y reclamar la anterior son una sola transición por
conector (un `flock` sobre `evidence/collect-requests/<conector>.lock`, que no
es una petición y nunca se borra). El scheduler lee el reloj dentro de ese lock y solo crea `<n>.started.json` si no
han pasado 24 h: si la petición caducó entre que la vio y que la reclamó, la deja
sin reclamar (queda «Caducada») y la web puede aceptar la siguiente. Tras publicar
el marcador vuelve a leer el reloj: si ya caducó durante la publicación, cierra la
petición con error `EXPIRED` sin recoger (todo bajo el lock, nadie ve un «En
curso» provisional); si no, lo confirma (`confirmed_at`). Un marcador sin
confirmar de una petición ya caducada (el proceso murió entre publicar y
confirmar) no cuenta como viva: queda «Caducada» y la web acepta otra. Nunca hay
dos vivas. Una reclamada y confirmada antes de las 24 h sigue su curso aunque
pase ese límite. La recogida corre fuera del lock. Pide otra recogida.

### Fallo durante la importación

Si la recogida produce un resultado pero falla al guardarlo (error de disco,
permisos) entre escribir el artefacto en `evidence/raw/` y escribir el
manifiesto, la petición se cierra como «No se pudo hacer: la recogida falló de
forma inesperada» (`COLLECT_CRASHED`), sin identificador de evidencia y sin
tono favorable; la página sigue diciendo «Sin recogidas todavía» (o la última
recogida anterior), porque la evidencia solo existe con manifiesto. El
artefacto queda huérfano: es contenido direccionado por su hash, no se lee y
la siguiente recogida con el mismo contenido lo reutiliza; `evidence verify`
puede señalarlo. Los checks se evalúan igualmente con lo que hubiera antes.
Revisa el disco y pide otra recogida.

### Orden

Las peticiones de recogida de varios conectores se atienden de la más antigua a
la más reciente, no por nombre de conector. Van después de las del ciclo y de
las de pre-auditoría.

### Acumulación

Las peticiones no se borran nunca de forma automática: cada pulsación deja sus
ficheros en `evidence/collect-requests/<conector>/` como parte del rastro de
auditoría. Solo las vivas cuentan para el límite de una por conector.

## 12. Documentos

`/documentos` es la pantalla de operación de la documentación organizativa: una
fila por documento de las plantillas. El cuestionario sigue en el asistente
(`/asistente/documentos-*`); esta página solo enlaza a cada bloque desde lo que
falta.

**Qué muestra cada fila.**

- Estado: sin generar, generado pendiente de aprobar, aprobado o aprobación
  caducada. Un documento que ya no coincide con sus entradas lleva los motivos
  en llano. Sin aprobación nunca se pinta como favorable.
- Fuente del documento generado: plantilla y versión, sha256 y fechas de
  consolidación del catálogo y del articulado. No hay fecha de generación: el
  meta no la guarda y no se inventa; la única fecha es la de cada aprobación.
- Última aprobación: nombre declarado, rol y fecha.
- Quién debe aprobarlo y qué falta para generarlo (preguntas pendientes o
  inválidas, por bloque y enlazadas).

`/documentos/{documento}` es la ficha: texto generado (escapado, recortado a
1 MiB solo en pantalla), meta, cláusulas y medidas que cubre, historial de
aprobaciones y aviso de registros ilegibles. Un documento sin generar muestra la
ficha sin texto; un nombre que no está en las plantillas da 404.

**`by` es un nombre declarado.** «Quién lo aprueba» se escribe a mano y nunca
viene relleno, ni desde el JWT ni desde las respuestas. El producto exige que
coincida con la persona declarada para el puesto (`firmante_direccion`,
responsable de la seguridad o del sistema); la página muestra ese nombre como
referencia. Quién operaba la pantalla lo dice la auditoría, no el campo.

**Rol que exige cada documento** (`orgdocs/approval.py`). Hace falta además
permiso de edición (sección 7).

| Documento | Rol |
|---|---|
| `politica-seguridad`, `normativa-seguridad`, `designacion-responsables` | `management` |
| `procedimiento-incidentes`, `procedimientos-seguridad`, `proceso-autorizacion` | El de la autoridad declarada en `autoridad_procedimientos`: la dirección (`management`), el responsable de la seguridad (`security_officer`) o el del sistema (`system_owner`) |

Si la organización aún no ha declarado esa autoridad, la fila lo dice, enlaza al
bloque «Responsables» y no ofrece aprobar. Quien no tiene el rol ve cuál le
falta y la orden que tendría que pedir (sección 7), sin botón.

**Una aprobación no se revoca desde la web.** El registro
(`org/docs/approvals/<documento>/`) es de solo añadir. Si el documento cambia o
sus respuestas cambian, la aprobación pasa a caducada y hay que generar y
aprobar de nuevo. Aprobar un documento no cambia el estado de ninguna medida.

**Generar.** El botón solo aparece si el alcance está aprobado y las respuestas
están completas; si no, la página lista los motivos con enlace al paso o bloque.
El POST responde 200 con el resultado de esa ejecución: documentos creados,
actualizados, sin cambios y archivados (una edición manual se archiva en
`org/docs/history/`), con su sha256. Recargar repite una generación
determinista. Si el POST llega y el producto rechaza, responde 422 con los
motivos y no escribe nada. Aprobar responde 303 a `/documentos?hecho=aprobado`.

**Leer las líneas de auditoría.**
`docker compose -p ens-box logs ui | grep 'ens_evidence.ui.audit'`. Los pasos son
`step=documentos` con `action=generar` o `action=aprobar`. Ninguna línea lleva
el nombre escrito ni mensajes con valores.

| `outcome` | Significa | Qué mirar |
|---|---|---|
| `saved` | Se escribió. | `keys=` son las claves escritas; `roles=` el rol usado al aprobar (`management`…). `keys=-` si todo estaba sin cambios. |
| `rejected` | La petición llegó al handler y se rechazó antes de escribir (alcance, respuestas, nombre no declarado, documento desconocido, documento no al día). | `keys=-`; el motivo está en la respuesta 422 que vio la persona, no en el log. |
| `failed` | Falló la escritura tras haber escrito algo. | `keys=` solo las claves **efectivamente** escritas; revisa el workspace y vuelve a generar. |
| `denied` | Rechazada antes del handler o por rol: sin permiso de edición (403) o sin el rol del documento. | `path=`, `reason=` y `role=` (el rol que faltaba). No se escribió nada. |
