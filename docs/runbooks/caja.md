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
deploy/box.sh install v0.1.0
```

La etiqueta tiene la forma `vX.Y.Z`. El script:

1. comprueba Docker y Compose;
2. crea `deploy/.env` (modo 600, sin credenciales) si no existe;
3. construye la imagen en la VM desde esa etiqueta;
4. prepara los ajustes y el workspace en el volumen `ens-box_data`;
5. ejecuta el primer ciclo, antes de arrancar el scheduler;
6. arranca la caja y comprueba que está sana.

El primer ciclo termina con uno de estos códigos:

| Código | Significado | Qué hacer |
|---|---|---|
| 0 | Informe escrito | Nada |
| 3 | Informe escrito, con conectores en ERROR | Es lo normal sin credenciales. `install` avisa y sigue. Configura las credenciales (sección 3) |
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

Después de cambiar un fichero, reinicia el servicio que lo lee:

```bash
ENS_VERSION=v0.1.0 docker compose -p ens-box up -d --force-recreate app
```

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

La CLI no tiene un comando para añadir activos: el inventario son ficheros
YAML en `scope/` del workspace. El flujo es traer una copia, editarla,
validarla y publicarla. Se ha probado con un workspace de ejemplo en local;
dentro del contenedor, no.

Para abreviar:

```bash
export ENS_VERSION=v0.1.0   # la versión instalada (deploy/box.sh status)
ens() { docker compose -p ens-box exec -T app ens-evidence "$@"; }
```

```bash
# 1. Trae una copia editable
ens workspace pull /data/inventario

# 2. Añade el activo (ajusta los datos a tu caja)
docker compose -p ens-box exec -T app sh -c 'cat >> /data/inventario/scope/assets.yaml' <<'YAML'
  - id: srv-caja
    type: server
    deployment: deployed
    hostname: caja.ejemplo.org
    function: Caja ens-compliance (evidencias e informes)
    os: Debian 13
    environment: producción
    location: Oficina
YAML

# 3. Inclúyelo en el alcance
docker compose -p ens-box exec -T app sed -i '/^included_assets:/a\  - srv-caja' /data/inventario/scope/system.yaml

# 4. Valida y publica (push solo publica si la copia es válida)
ens inventory validate
ens workspace push /data/inventario

# 5. Aprueba el alcance (lo decide el responsable del sistema)
ens scope approve --by "<nombre>" --role system_owner
ens scope status
```

Notas:

- `type: server` pide `hostname`, `function`, `os`, `environment` y `location`.
  Si el servidor es de un proveedor, mira los demás tipos en
  `src/ens_evidence/scope/models.py`.
- Si no incluyes el activo en `system.yaml` (o no lo excluyes con
  justificación), `push` no publica nada.
- `push` no publica la aprobación. Tras cambiar el alcance, apruébalo otra vez
  con `scope approve`. La aprobación es declarada: la herramienta no la
  verifica.
- Si prefieres un editor, copia los ficheros fuera con `docker compose cp` y
  vigila el propietario: la app corre con el UID 10001.

## 6. Copia

La copia (restic hacia R2) se configura como se explica en el
[runbook de informes y scheduler](informes-y-scheduler.md) y la restauración
está en el [runbook de restauración](restauracion-de-evidencias.md).
En la caja:

- Las credenciales de la copia van en `deploy/.env`
  (`ENS_BACKUP_AWS_ACCESS_KEY_ID`, `ENS_BACKUP_AWS_SECRET_ACCESS_KEY`,
  `ENS_BACKUP_RESTIC_PASSWORD`).
- La imagen lleva restic con versión fijada.
- Lanzar una copia a mano:

```bash
ens backup run
```

- La copia del volumen de la caja no sustituye a la copia inmutable de
  evidencias: la referencia es esta última.

## 7. Actualizar

```bash
git fetch --tags
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
- Avisa si `compose.yaml` del clon difiere del usado con esa versión.
  Hazlo coincidir: `git checkout <etiqueta>`.

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
ens evidence verify
```

## 9. Estado y salud

```bash
deploy/box.sh status         # registro del despliegue, contenedores y scheduler
docker compose -p ens-box logs app
```

La app está sana mientras el scheduler siga latiendo. El bucle escribe un
latido en `/data/state/scheduler-heartbeat` y el healthcheck comprueba que es
reciente (el umbral está en `compose.yaml`, servicio `app`). Si el latido
envejece, el contenedor pasa a `unhealthy`.

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

Ningún servicio publica puertos en el host. Si tu cortafuegos filtra la salida,
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
