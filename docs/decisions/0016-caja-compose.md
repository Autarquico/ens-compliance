# 0016 — Caja de v0.1 con Docker Compose

- Estado: aceptada
- Fecha: 2026-11-03

## Contexto

El ADR 0004 fija Docker Compose como forma de desplegar la caja. Esta decisión
concreta la de v0.1. En v0.1 la app solo ejecuta `ens-evidence scheduler run`
(ADR 0014): no hay interfaz web (v0.2) ni servidor osquery. Tampoco hay
registro de imágenes ni releases firmadas (v0.3). Cualquier fallo de la caja
debe poder deshacerse sin perder evidencias. Procedimientos:
[runbook de la caja](../runbooks/caja.md).

## Decisión

### Imagen

- **Se construye en la VM desde una etiqueta git.** `deploy/box.sh` valida la
  etiqueta (`vX.Y.Z`, y `-rc` para pruebas), extrae el árbol con `git archive`
  en un directorio temporal y construye con `docker build`. No se ejecuta
  nada del árbol de trabajo. No hay registro hasta v0.3.
- **Digests.** Las imágenes externas (Python base, uv, Caddy, cloudflared) se
  fijan por digest; para actualizar una se cambian etiqueta y digest juntos.
  restic es el binario oficial con su sha256 verificado en el build, que
  aborta si no coincide. La app local no lleva digest: se identifica por el id
  de la imagen y el commit de la etiqueta.
- La imagen no lleva secretos; `.dockerignore` deja fuera `docs/`, tests y
  ficheros de entorno.

### Redes

- Ningún servicio publica puertos.
- `edge` es interna y sin salida a Internet: solo `caddy` y `cloudflared`.
  Caddy escucha en `:8080` dentro de ella.
- `backend` es solo de `app`: sin entrada y con salida para conectores y R2.
- `tunnel` es solo de `cloudflared`, con salida hacia Cloudflare.
- La interfaz nunca es pública: entra por Cloudflare Tunnel y Access. En
  v0.1 Caddy contesta 403 a todo y lo deja en el log, sin query string ni
  cabeceras (pueden llevar tokens de Access). `/agent/*` no se publica hasta
  `osquery-server`.

### Privilegios

- Sin `privileged`, sin socket de Docker y sin montajes del host salvo el
  `Caddyfile`, en solo lectura.
- `cap_drop: [ALL]`, `no-new-privileges` y `read_only: true` en todos los
  servicios.
- Caddy corre como UID 65532 y sin volúmenes: `/data` y `/config` son tmpfs,
  porque no emite certificados (el TLS lo termina Cloudflare). Los volúmenes
  `caddy-data` y `caddy-config` de una caja anterior quedan huérfanos; nunca
  se borran solos.
- `app` corre con el UID/GID 10001, fijos. Un volumen vacío hereda ese
  propietario porque la imagen de la app es la primera que lo monta.
- **Desvío: Caddy conserva `NET_BIND_SERVICE`.** El binario del Caddy oficial
  lleva una capability de fichero. Con `cap_drop: [ALL]` y `no-new-privileges`
  no puede ejecutarse, porque el conjunto permitido no la contiene, y falla
  con `exec /usr/bin/caddy: operation not permitted` (visto en la primera
  prueba). Se añade solo esa capacidad. Caddy escucha en `:8080`, que no la
  necesita: la mantenemos únicamente porque sin ella no arranca. Alternativa
  descartada en v0.1: una imagen propia de Caddy sin esa capability, que habría
  que mantener nosotros.
- `box.sh` usa un contenedor de un solo uso, sin red, con el mismo UID, para
  tocar el estado del volumen. Nunca monta un volumen inexistente, porque
  Docker lo crearía con root.

### Secretos por servicio

- `deploy/.env` solo llega a `app`: conectores, R2 y copia.
- `deploy/tunnel.env` solo llega a `cloudflared`: `TUNNEL_TOKEN`.
- Los dos son modo 600, están fuera de Git (`.gitignore`) y `box.sh` se niega
  a seguir si son de otro usuario, enlaces o legibles por otros.
- Sin `set -x`. Ni `compose.yaml` ni el ajuste ni la imagen llevan
  credenciales.

### Identidad del despliegue

- Proyecto Compose `ens-box` y volumen de datos `ens-box_data`, de nombre
  fijo. `ENS_BOX_PROJECT` los cambia solo para pruebas aisladas.
- `/data/state/box-deploy.json` guarda `current`, `previous`, `attempt` y
  `failed`, cada uno con etiqueta, commit, id de imagen, sha256 de
  `compose.yaml` y `Caddyfile`, copia previa y fecha.
- `compose.yaml`, el `Caddyfile` y el propio `box.sh` salen de `git archive`
  del commit de `refs/tags/<tag>`, se guardan en
  `<git-dir>/ens-box/releases/<commit>/` y se contrastan con git en cada uso.
  El script se niega si no es el de la etiqueta.
- El latido vive en un tmpfs de cada contenedor: una versión nueva nunca hereda
  el de la anterior, y `update` solo la da por sana si su scheduler ha latido.
- Los ajustes de la caja están en `/data/settings.yaml`, dentro del volumen.
- `previous` solo se mueve tras un `update` con la caja sana. Un `update`
  fallido deja `current` intacto y registra `attempt`.
- `update` nunca revierte solo. `rollback` vuelve a la versión registrada
  usando la imagen local, y se niega si su id no coincide con el registrado.
- Con la copia configurada, `update` exige `backup run` antes y aborta si
  falla. Solo `--no-backup` la omite, y queda registrado. Antes de actualizar
  se guarda además una copia local de los ajustes y de `box-deploy.json`.
- Nada de `box.sh` borra volúmenes ni imágenes.

### Latido

- El bucle del scheduler escribe `/data/state/scheduler-heartbeat` al
  empezar cada vuelta y tras cada trabajo, de forma atómica. Si no puede
  escribirlo, el scheduler sigue y el fichero envejece.
- El healthcheck de `app` comprueba que el latido es reciente; el umbral vive
  en `compose.yaml`.
- Un fallo de conectores no es un fallo del servicio: se ve en el informe.
- `restart: unless-stopped`. Nada reinicia un contenedor `unhealthy`.

### Fuera de la caja de v0.1

- **Instalador para terceros:** v0.2 (`installer`).
- **Bundle offline y releases con registro de imágenes:** v0.3.
- Interfaz web, servidor osquery y perfiles `deteccion` e `ia-local`
  (ADR 0004): no existen aún.

## Consecuencias

- Construir en la VM exige acceso al clon con sus etiquetas, a Docker Hub, a
  GitHub (restic) y a PyPI durante el build.
- Una caja sin registro depende de que la imagen anterior siga en local. Si se
  limpian las imágenes a mano, el rollback se niega.
- La capability de Caddy es una excepción a la regla de privilegios mínimos,
  documentada aquí.
- `cloudflared` y su healthcheck no se han probado en marcha (hace falta un
  `TUNNEL_TOKEN` real) y la denegación desde Internet tampoco: se verifican en
  `validation-selfassessment`. La denegación sí se comprueba en local desde la
  red `edge` (403 en toda ruta; ver el runbook).
- La inscripción de la caja en el alcance usa `scope add-asset` y
  `scope set-system`, que escriben el workspace del volumen; la aprobación sigue
  siendo humana (`scope approve`).
- Un ciclo diario largo sin pasar por el bucle puede dejar el latido viejo y
  marcar la app `unhealthy` sin que haya fallo real.
- El rollback asume que la versión anterior lee lo que escribió la nueva
  (ADR 0014, «Despliegue y rollback»). En v0.1 no hay migraciones.
- La caja prepara evidencias; no declara conformidad. La revisión humana sigue
  siendo obligatoria.
