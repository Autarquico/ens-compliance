# La caja: instalar, publicar, actualizar y volver atrás

Cómo poner en marcha la caja de v0.1 en una VM con Docker Compose y cómo
operarla. Contrato: [ADR 0016](../decisions/0016-caja-compose.md). Una vez en
marcha, el trabajo diario del contenedor está en el
[runbook de informes y scheduler](informes-y-scheduler.md).

En v0.1 la caja ejecuta el scheduler y nada más. No hay interfaz web (llega en
v0.2) ni servidor de agentes. La herramienta prepara evidencias; no declara
conformidad. Una caja instalada y sana no significa que el sistema cumpla el
ENS.

## 1. Requisitos

- Una VM Linux (o un portátil) con Docker Engine y el plugin Compose v2.
- Un usuario que pueda hablar con Docker, sin ser root.
- `git`, con el clon de este repositorio y sus etiquetas (`git fetch --tags`).
- Salida a Internet (ver sección 10).
- Opcional: una cuenta de Cloudflare con Zero Trust para publicar la caja
  (sección 4).

## 2. Instalar

```bash
git clone <url-del-repositorio> ens-compliance && cd ens-compliance
git fetch --tags
git checkout v0.1.0
deploy/box.sh install v0.1.0
```

La etiqueta tiene la forma `vX.Y.Z`. El script tiene que ser el de esa etiqueta:
si no lo es, se niega y te pide el `git checkout`. La imagen, `compose.yaml` y el
`Caddyfile` salen del commit de la etiqueta, nunca de lo que tengas editado en el
clon; se guardan en `.git/ens-box/releases/<commit>/`. El script:

1. comprueba Docker y Compose;
2. crea `deploy/.env` (modo 600, sin credenciales) si no existe;
3. construye la imagen en la VM desde esa etiqueta;
4. prepara los ajustes y el workspace en el volumen `ens-box_data`;
5. ejecuta el primer ciclo, antes de arrancar el scheduler;
6. arranca la caja y comprueba que está sana.

El primer ciclo termina con uno de estos códigos:

| Código | Significado | Qué hacer |
|---|---|---|
| 0 | Informe escrito | Nada. Es lo que sale en una instalación limpia: aún no hay conectores activos |
| 3 | Informe escrito, con conectores en ERROR | Hay conectores activos sin credenciales o que fallan. `install` avisa y sigue. Configura las credenciales (sección 3) |
| 2 | Fallo: no se escribió el informe, o hay un ciclo en curso | `install` se detiene. Revisa el mensaje y los logs |

Si la copia no está configurada, el primer ciclo imprime que la copia ha
fallado y se verá en el siguiente informe. No es un fallo de la instalación:
configura la copia (sección 6).

`install` no se puede repetir sobre una caja instalada: para cambiar de
versión usa `update`.

## 3. Credenciales

Hay dos ficheros, los dos fuera de Git y con modo 600:

| Fichero | Quién lo recibe | Qué lleva |
|---|---|---|
| `deploy/.env` | Solo `app` | Credenciales de los conectores, del workspace en R2 y de la copia |
| `deploy/tunnel.env` | Solo `cloudflared` | `TUNNEL_TOKEN` |

```bash
cp deploy/.env.example deploy/.env           # install ya lo hace si falta
cp deploy/tunnel.env.example deploy/tunnel.env
chmod 600 deploy/.env deploy/tunnel.env
```

Descomenta y rellena en `deploy/.env` solo lo que uses. Una variable vacía no
equivale a una ausente. Los nombres están en `deploy/.env.example`.

Después de cambiar un fichero, recrea el servicio que lo lee:

```bash
deploy/box.sh restart app        # también: caddy, cloudflared
```

`restart` recrea el servicio con el `compose.yaml` y el `Caddyfile` de la
release registrada como actual (comprueba sus sha256) y espera a que esté sano.
No uses `docker compose up` para recrear o arrancar servicios: usaría el
`compose.yaml` del clon, que puede estar en otra etiqueta o tener ediciones, y
la caja correría con una definición distinta de la registrada. `box.sh status`
avisa si algún contenedor se creó así. `docker compose logs`, `exec` y `ps` solo
leen y se pueden usar.

`box.sh` se niega a seguir si un fichero de secretos es de otro usuario, es un
enlace o lo pueden leer otros. Nunca pongas credenciales en `compose.yaml`, en
el ajuste (`settings.yaml`) ni en la imagen.

## 4. Publicar con Cloudflare Tunnel y Access

La interfaz nunca es pública. Si la publicas, entra solo por un túnel y detrás
de Cloudflare Access.

En v0.1 no hay interfaz. Caddy contesta 403 a cualquier ruta y lo deja en el
log, sin query string ni cabeceras. Publicar la caja ahora sirve para dejar el
camino listo y comprobar que no se ve nada desde fuera.

1. En Cloudflare Zero Trust crea un túnel y copia su token.
2. Escribe el token en `deploy/tunnel.env` (modo 600): `TUNNEL_TOKEN=...`.
3. En el túnel, añade un hostname público que apunte al servicio
   `http://caddy:8080`.
4. Crea una aplicación de Access para ese hostname, con una política que
   permita solo a las personas autorizadas.
5. Aplica el cambio. Con `deploy/tunnel.env` presente, `box.sh` activa el
   perfil `tunnel` y arranca `cloudflared`:

```bash
deploy/box.sh status
```

### Comprobación local de la denegación

Sin salir de la caja, desde la red `edge` y sin imágenes de terceros:

```bash
for ruta in / /agent/enroll '/admin?token=prueba'; do
  docker compose -p ens-box exec -T caddy wget -S -O /dev/null "http://127.0.0.1:8080$ruta" 2>&1 | grep '^  HTTP/'
done
```

Se espera `HTTP/1.1 403 Forbidden` una vez por ruta (tres líneas). En `docker compose -p
ens-box logs caddy` la petición queda con la ruta (`/admin`) pero sin la query
ni las cabeceras.

### Comprobación desde Internet (pendiente de verificar con una cuenta real)

Desde una máquina fuera de tu red y sin sesión de Access:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://<hostname-publicado>/
```

Se espera que no llegue contenido de la caja: una redirección al acceso de
Cloudflare o un 403. Si sale 200 con contenido, quita el hostname del túnel y
avisa al responsable.

Esta comprobación no se ha ejecutado con una cuenta real de Cloudflare. Tampoco
se ha probado `cloudflared` en marcha ni su healthcheck. Se verifica en
`validation-selfassessment`.

## 5. Inscribir la caja como activo del alcance

La caja es un activo más del sistema y se declara en el inventario del
workspace de tu organización. Ese dato es tuyo: no va al repositorio.

Los comandos `scope add-asset` y `scope set-system` escriben YAML válido en el
workspace del volumen, sin editar ficheros a mano. No aprueban nada: la
aprobación sigue siendo de una persona (`scope approve`).

```bash
export ENS_VERSION=v0.1.0   # la versión instalada (deploy/box.sh status)
ens() { docker compose -p ens-box exec -T app ens-evidence "$@"; }
```

```bash
# 1. Nombre y descripción del sistema (`init` los deja vacíos y sin ellos el alcance no es válido)
ens scope set-system --name "<nombre del sistema>" --description "<qué hace>"

# 2. Declara la caja e inclúyela en el alcance (ajusta los datos a tu caja)
ens scope add-asset --id srv-caja --type server --include \
  --set hostname=caja.ejemplo.org \
  --set "function=Caja ens-compliance (evidencias e informes)" \
  --set "os=Debian 13" \
  --set "environment=producción" \
  --set location=Oficina

# 3. Comprueba el inventario y el alcance
ens inventory validate
ens scope status        # NOT_APPROVED: falta la aprobación

# 4. Aprueba el alcance (lo decide el responsable del sistema)
ens scope approve --by "<nombre>" --role system_owner
ens scope status        # APPROVED
```

Notas:

- `--type server` pide `hostname`, `function`, `os`, `environment` y
  `location`; si falta alguno, el comando lo dice y no cambia nada. Para otros
  tipos lánzalo solo con `--id` y `--type`: el error enumera los campos que
  faltan.
- `--set` admite los campos del activo; las listas van separadas por comas y
  los booleanos son `true` o `false`.
- Sin `--include` el activo queda sin decidir y `inventory validate` lo dice:
  incluirlo o excluirlo con justificación es decisión tuya.
- Cambiar el alcance revoca una aprobación anterior: apruébalo otra vez con
  `scope approve`. La aprobación es declarada: la herramienta no la verifica.
- Ambos comandos reescriben `scope/assets.yaml` y `scope/system.yaml`: los
  comentarios de las plantillas no se conservan.

## 6. Copia

La copia (restic hacia R2) se explica en el
[runbook de informes y scheduler](informes-y-scheduler.md) y la restauración
está en el [runbook de restauración](restauracion-de-evidencias.md). En la
caja, `ens backup run` necesita dos cosas.

**1. El repositorio, en `/data/settings.yaml`.** Está en el volumen, con
propietario UID 10001, y `docker compose exec` entra como ese usuario. Añade la
sección `backup` al final (solo una vez: repetirla duplica la clave):

```bash
docker compose -p ens-box exec -T app sh -c 'cat >> /data/settings.yaml' <<'YAML'
backup:
  restic_repository: s3:https://<account_id>.r2.cloudflarestorage.com/<bucket>/ens
  region: auto
YAML
```

Para cambiarlo después: `docker compose -p ens-box exec app sed -i
's#^  restic_repository:.*#  restic_repository: <nuevo>#' /data/settings.yaml`.
El repositorio no admite credenciales ni parámetros en la URL.

**2. Las credenciales, en `deploy/.env`** (modo 600, solo las lee `app`):
`ENS_BACKUP_RESTIC_PASSWORD`, `ENS_BACKUP_AWS_ACCESS_KEY_ID` y
`ENS_BACKUP_AWS_SECRET_ACCESS_KEY`, distintas de las del workspace. Después,
recrea `app` con `deploy/box.sh restart app` (sección 3) para que las lea.

Con eso, crea el repositorio (una vez) y lanza la primera copia:

```bash
ens backup init
ens backup run
```

Sin `backup init`, el repositorio no existe y `backup run` falla.

- La contraseña del repositorio no se puede recuperar: guárdala fuera de la
  caja (ver el runbook de restauración).
- La copia del volumen de la caja no sustituye a la copia inmutable de
  evidencias: la referencia es esta última.
- Probado en Docker con un repositorio restic local (`/tmp/...` dentro del
  contenedor) y solo la contraseña: `backup init` y `backup run` terminan bien.
  No se ha probado contra R2 real.

## 7. Actualizar

```bash
git fetch --tags
git checkout v0.1.1
deploy/box.sh update v0.1.1
```

`update`:

1. exige una copia previa (`backup run`) si la copia está configurada. Si
   falla, no actualiza;
2. guarda una copia local de los ajustes y de `box-deploy.json` en
   `/data/state/pre-update-<fecha>/`;
3. construye la nueva imagen desde la etiqueta y arranca la caja;
4. si la caja está sana, la nueva versión pasa a ser la actual y la anterior
   queda como versión a la que volver.

Si la copia no está configurada, avisa y sigue. Solo `--no-backup` omite una
copia configurada:

```bash
deploy/box.sh update --no-backup v0.1.1
```

Úsalo solo si sabes por qué falla la copia. Queda registrado en
`box-deploy.json` (`backup: skipped_by_flag`).

Si la versión nueva no está sana, `update` lo dice, sale con error y no
revierte sola. Sigue en la sección 8.

## 8. Volver atrás

```bash
deploy/box.sh rollback
```

- Vuelve a la última versión que estuvo sana. La imagen sigue en local: no
  hay que descargar nada.
- No toca el volumen: los informes, los marcadores y el workspace se
  conservan, y la versión anterior los sigue leyendo (ADR 0014; en v0.1 no hay
  migraciones).
- Se niega si la imagen local no es la registrada. En ese caso no cambia
  nada.
- Usa el `compose.yaml` y el `Caddyfile` guardados de esa versión en
  `.git/ens-box/releases/<commit>/` y se niega si no coinciden con los
  registrados. No hace falta cambiar de etiqueta antes. Después, deja el clon
  en la versión que corre: `git checkout <etiqueta a la que se volvió>`.

El registro está en `/data/state/box-deploy.json` y lleva, para `current`,
`previous`, `attempt` (actualización en curso o fallida) y `failed`: la
etiqueta, el commit, el id de la imagen, el sha256 de `compose.yaml` y de
`Caddyfile`, y si hubo copia previa. Se ve con:

```bash
deploy/box.sh status
```

Después de volver atrás, comprueba que no se ha perdido nada:

```bash
ens scheduler status
ens evidence verify --all
```

## 9. Estado y salud

```bash
deploy/box.sh status         # registro del despliegue, contenedores y scheduler
docker compose -p ens-box logs app
```

La app está sana mientras el scheduler siga latiendo. El bucle escribe un
latido en `/tmp/scheduler-heartbeat`, en el tmpfs del contenedor `app` (no en el
volumen: se pierde al recrearlo y una versión nueva no hereda el de la
anterior), y el healthcheck comprueba que es reciente (el umbral está en
`compose.yaml`, servicio `app`). Si el latido envejece, el contenedor pasa a
`unhealthy`. Para ver cuándo latió por última vez:

```bash
docker compose -p ens-box exec app stat -c %y /tmp/scheduler-heartbeat
```

- Un fallo de un conector no es un fallo del servicio: sale en el informe, no
  en el healthcheck.
- Un ciclo muy largo puede dejar el latido viejo. Compáralo con el umbral
  antes de dar la caja por rota.
- Nada reinicia solo un contenedor `unhealthy`. `update` lo trata como «no
  sana».
- Caddy se sondea por su API de administración local; cualquier ruta pública
  da 403.

## 10. Salida de red

| Servicio | Red | Entrada | Salida a Internet |
|---|---|---|---|
| `app` | `backend` | Ninguna | Sí: APIs de los conectores (GitHub, Cloudflare, Google) y R2 |
| `caddy` | `edge` (interna) | Solo desde `cloudflared` | No |
| `cloudflared` (perfil `tunnel`) | `edge` y `tunnel` | Ninguna | Sí: hacia Cloudflare |

Ningún servicio publica puertos en el host. En `docker compose ps`, la columna
PORTS de `caddy` muestra `80/tcp, 443/tcp, 2019/tcp`: son puertos que declara la
imagen, no publicados. Uno publicado llevaría `0.0.0.0:` delante. Si tu cortafuegos filtra la salida,
`app` necesita 443 hacia los servicios que uses.

## 11. Recursos medidos

Medido el 2026-11-03 con `docker stats` en un portátil (Docker 29, macOS), con la
caja recién instalada y **sin conectores configurados**. Es un mínimo: con conectores
reales y meses de evidencia, el ciclo usa más. La medición con cuentas reales queda para
la autoevaluación de Autarqui.

| Qué | Valor |
|---|---|
| `app` en reposo | 46 MiB de memoria, CPU ~0 % |
| `caddy` en reposo | 13 MiB de memoria, CPU ~0 % |
| Pico durante el primer ciclo | 86 MiB (`app`) |
| Imagen `ens-evidence` | 88 MB |
| Volumen de datos tras instalar | 42 kB |

Estimación, no medida: con 1 GB de memoria y 10 GB de disco hay margen para v0.1.
