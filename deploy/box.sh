#!/bin/sh
# Opera la caja en Docker Compose (ADR 0016): install, update, rollback, status.
#
#   deploy/box.sh install <vX.Y.Z>
#   deploy/box.sh update [--no-backup] [--seal-ok] <vX.Y.Z>
#   deploy/box.sh rollback [--seal-ok]
#   deploy/box.sh restart [app|caddy|cloudflared|agent|ui]
#   deploy/box.sh status
#   deploy/box.sh kit enable copias [--replace-config] <paquete.tar.gz>
#   deploy/box.sh kit disable copias
#   deploy/box.sh kit copias reset-password
#   deploy/box.sh kit enable claves <paquete.tar.gz>
#   deploy/box.sh kit claves init | unseal
#   deploy/box.sh kit disable claves
#   deploy/box.sh kit enable contrasenas <paquete.tar.gz>
#   deploy/box.sh kit contrasenas alta | cerrar-alta
#   deploy/box.sh kit disable contrasenas
#
# Lo ejecuta una persona con Docker en la VM, desde un clon del repositorio con la etiqueta
# desplegada (git checkout <tag>). La imagen, compose.yaml y el Caddyfile salen de `git archive`
# del commit al que apunta refs/tags/<tag>, nunca del árbol de trabajo; este script tiene que ser
# idéntico al de la etiqueta. Se guardan en <git-dir>/ens-box/releases/<commit>.
# Para recrear un servicio (credenciales nuevas) usa `restart`, no `docker compose up`: este
# último usaría el compose.yaml del clon y no el de la release fijada.
# Nunca borra volúmenes ni imágenes y nunca revierte sola.
#
# ENS_BOX_PROJECT cambia el nombre del proyecto Compose y del volumen (solo para pruebas).

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROJECT=${ENS_BOX_PROJECT:-ens-box}
VOLUME="${PROJECT}_data"
ENV_FILE="$ROOT/deploy/.env"
TUNNEL_ENV_FILE="$ROOT/deploy/tunnel.env"
AGENT_ENV_FILE="$ROOT/deploy/agent.env"
UI_ENV_FILE="$ROOT/deploy/ui.env"
UI_HOST=
AGENT_HOST=
COPIAS_HOST=
CLAVES_HOST=
CONTRASENAS_HOST=
COPIAS_DIR="$ROOT/deploy/kit/copias"
COPIAS_OVERRIDE="$COPIAS_DIR/compose.yaml"
CLAVES_DIR="$ROOT/deploy/kit/claves"
CLAVES_CONFIG="$CLAVES_DIR/openbao.hcl"
CLAVES_MANIFEST="$CLAVES_DIR/MANIFEST"
CONTRASENAS_DIR="$ROOT/deploy/kit/contrasenas"
CONTRASENAS_MANIFEST="$CONTRASENAS_DIR/MANIFEST"
CONTRASENAS_ADMIN_ENV="$CONTRASENAS_DIR/admin.env"
CONTRASENAS_ALTA="$CONTRASENAS_DIR/alta.yaml"
BACKREST_UID=10001
EXPORT_UID=10001
OPENBAO_UID=10001
INIT_CONTAINER=
STATE_FILE=/data/state/box-deploy.json
APP_UID=10001
HEALTH_TIMEOUT=${ENS_BOX_HEALTH_TIMEOUT:-180}
WORK_DIR=
LOCK_DIR=
REL=
COMMIT=
RELEASE_FILES="compose.yaml deploy/Caddyfile deploy/box.sh"
# Solo las releases que los traen: un commit anterior al kit de claves no los tiene y sigue siendo desplegable.
OPTIONAL_RELEASE_FILES="deploy/openbao/config.hcl deploy/openbao/policies/ens-app-metadata.hcl deploy/openbao/policies/claves-admin.hcl"

info() { printf '%s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
  WORK_DIR=
  [ -z "$INIT_CONTAINER" ] || docker rm -f "$INIT_CONTAINER" >/dev/null 2>&1 || true
  INIT_CONTAINER=
}

# Directorio temporal propio (dentro de $1 si se indica, para poder moverlo sin copiar).
new_work_dir() {
  cleanup
  if [ $# -gt 0 ]; then
    mkdir -p "$1"
    WORK_DIR=$(mktemp -d "$1/.stage.XXXXXX")
  else
    WORK_DIR=$(mktemp -d)
  fi
}

# Una sola operación de despliegue por proyecto a la vez. mkdir es atómico y portable; el lock
# lleva el pid de quien lo tiene y se suelta al salir (también con INT/TERM).
git_dir() { (cd "$ROOT" && cd "$(git rev-parse --git-dir)" && pwd); }

acquire_lock() {
  lock="$(git_dir)/ens-box/$PROJECT.lock"
  mkdir -p "$(dirname "$lock")"
  if ! mkdir "$lock" 2>/dev/null; then
    holder=$(cat "$lock/pid" 2>/dev/null || true)
    if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
      die "Hay un lock huérfano de una operación (pid $holder) que ya no existe; si estás seguro de que no hay otra en marcha, borra $lock y repite."
    fi
    die "Hay otra operación de despliegue en marcha${holder:+ (pid $holder)}; espera a que termine. No se ha cambiado nada."
  fi
  LOCK_DIR=$lock
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
  [ -z "$LOCK_DIR" ] || rm -rf "$LOCK_DIR"
  LOCK_DIR=
}

trap 'cleanup; release_lock' EXIT
trap 'exit 130' INT TERM

usage() {
  die "uso: deploy/box.sh install <vX.Y.Z> | update [--no-backup] [--seal-ok] <vX.Y.Z> | rollback [--seal-ok] | restart [app|caddy|cloudflared|agent|ui|backrest] | status | kit enable copias [--replace-config] <paquete> | kit disable copias | kit copias reset-password | kit enable claves <paquete> | kit disable claves | kit claves init | kit claves unseal | kit enable contrasenas <paquete> | kit disable contrasenas | kit contrasenas alta | kit contrasenas cerrar-alta"
}

# --- comprobaciones ------------------------------------------------------------------------

valid_tag() {
  # grep compara línea a línea: un salto de línea colaría «v0.1.0\nlo-que-sea».
  case $1 in *[!v0-9.rc-]*) return 1 ;; esac
  printf '%s' "$1" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$'
}

require_tools() {
  command -v docker >/dev/null 2>&1 || die "Docker no está instalado."
  docker compose version >/dev/null 2>&1 || die "Falta el plugin Docker Compose (v2)."
  command -v git >/dev/null 2>&1 || die "git no está instalado."
  git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "$ROOT no es un clon de git."
  docker info >/dev/null 2>&1 || die "No se puede hablar con el daemon de Docker (¿permisos?)."
}

file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
file_owner() { stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1"; }

# Un fichero de secretos debe ser del usuario y no legible por otros.
check_private() {
  [ ! -L "$1" ] || die "$1 es un enlace simbólico; debe ser un fichero normal."
  [ -e "$1" ] || return 0
  [ "$(file_owner "$1")" = "$(id -u)" ] || die "$1 no es de tu usuario."
  case "$(file_mode "$1")" in
    *00) ;;
    *) die "$1 es legible por otros: ejecuta chmod 600 $1" ;;
  esac
}

check_secret_files() {
  check_private "$ENV_FILE"
  check_private "$TUNNEL_ENV_FILE"
  check_private "$AGENT_ENV_FILE"
  check_private "$UI_ENV_FILE"
  load_hostnames
}

# Nombre de host (ENS_*_HOST) de un fichero de entorno, sin ejecutarlo. Vacío si falta.
# Falla si no es un nombre de host simple: acaba en un matcher de Caddy.
hostname_from() {
  [ -e "$1" ] || return 0
  value=$(sed -n "s/^$2=//p" "$1" | tail -n 1)
  case $value in
    *[!A-Za-z0-9.-]* | .* | -*) return 1 ;;
  esac
  printf '%s' "$value"
}

load_hostnames() {
  UI_HOST=$(hostname_from "$UI_ENV_FILE" ENS_UI_HOST) ||
    die "ENS_UI_HOST de $UI_ENV_FILE no es un nombre de host (sin esquema ni puerto)."
  AGENT_HOST=$(hostname_from "$AGENT_ENV_FILE" ENS_AGENT_HOST) ||
    die "ENS_AGENT_HOST de $AGENT_ENV_FILE no es un nombre de host (sin esquema ni puerto)."
  COPIAS_HOST=$(hostname_from "$UI_ENV_FILE" ENS_COPIAS_HOST) ||
    die "ENS_COPIAS_HOST de $UI_ENV_FILE no es un nombre de host (sin esquema ni puerto)."
  CLAVES_HOST=$(hostname_from "$UI_ENV_FILE" ENS_CLAVES_HOST) ||
    die "ENS_CLAVES_HOST de $UI_ENV_FILE no es un nombre de host (sin esquema ni puerto)."
  CONTRASENAS_HOST=$(hostname_from "$UI_ENV_FILE" ENS_CONTRASENAS_HOST) ||
    die "ENS_CONTRASENAS_HOST de $UI_ENV_FILE no es un nombre de host (sin esquema ni puerto)."
}

ensure_env_file() {
  if [ ! -e "$ENV_FILE" ]; then
    (umask 077 && cp "$ROOT/deploy/.env.example" "$ENV_FILE")
    chmod 600 "$ENV_FILE"
    info "Creado $ENV_FILE (modo 600) sin credenciales: rellénalo y reinicia si las necesitas."
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# El kit de copias se activa con deploy/kit/copias/compose.yaml (lo instala `kit enable`). Una
# release anterior que no define `backrest` no lo conoce: se ignora (rollback), sin tocar el fichero.
copias_enabled() {
  [ -e "$COPIAS_OVERRIDE" ] && grep -q '^  backrest:' "$REL/compose.yaml"
}

# El override es del host, no de la release: solo vale si sigue siendo el que validó `kit enable`.
check_copias_override() {
  [ ! -L "$COPIAS_OVERRIDE" ] || die "$COPIAS_OVERRIDE es un enlace simbólico; debe ser un fichero normal."
  want=$(sed -n 's/^\([0-9a-f]\{64\}\)  copias\/compose[.]yaml$/\1/p' "$COPIAS_DIR/MANIFEST" 2>/dev/null || true)
  [ -n "$want" ] && [ "$(sha256_of "$COPIAS_OVERRIDE")" = "$want" ] ||
    die "$COPIAS_OVERRIDE no coincide con el MANIFEST del paquete instalado; no se usa. Repite kit enable con el paquete."
}

# El kit de claves se activa con deploy/kit/claves/MANIFEST (lo instala `kit enable`, lo renombra
# `kit disable`). Igual que copias, una release que no define `openbao` lo ignora.
claves_enabled() {
  [ -e "$CLAVES_MANIFEST" ] && grep -q '^  openbao:' "$REL/compose.yaml"
}

# El HCL es del host, no de la release: solo vale si sigue siendo el que registró `kit enable`.
check_claves_config() {
  [ ! -L "$CLAVES_CONFIG" ] || die "$CLAVES_CONFIG es un enlace simbólico; debe ser un fichero normal."
  want=$(sed -n 's/^\([0-9a-f]\{64\}\)  claves\/openbao[.]hcl$/\1/p' "$CLAVES_MANIFEST" 2>/dev/null || true)
  [ -n "$want" ] && [ -f "$CLAVES_CONFIG" ] && [ "$(sha256_of "$CLAVES_CONFIG")" = "$want" ] ||
    die "$CLAVES_CONFIG no coincide con el MANIFEST del paquete instalado; no se usa. Repite kit enable claves con el paquete."
}

# El kit de contraseñas se activa con deploy/kit/contrasenas/MANIFEST (lo instala `kit enable`, lo
# renombra `kit disable`). Una release que no define `vaultwarden` lo ignora.
contrasenas_enabled() {
  [ -e "$CONTRASENAS_MANIFEST" ] && grep -q '^  vaultwarden:' "$REL/compose.yaml"
}

# alta.yaml abre el panel de administración: es el único modo de que Vaultwarden tenga ADMIN_TOKEN, y
# solo vale tal como lo escribió `kit contrasenas alta` (mismo contenido, admin.env privado).
alta_override_text() {
  printf 'services:\n  vaultwarden:\n    env_file:\n      - "%s"\n' "$CONTRASENAS_ADMIN_ENV"
}

check_alta_override() {
  [ ! -L "$CONTRASENAS_ALTA" ] || die "$CONTRASENAS_ALTA es un enlace simbólico; debe ser un fichero normal."
  [ -f "$CONTRASENAS_ALTA" ] && [ "$(alta_override_text)" = "$(cat "$CONTRASENAS_ALTA")" ] &&
    [ -f "$CONTRASENAS_ADMIN_ENV" ] ||
    die "$CONTRASENAS_ALTA no es el que escribió kit contrasenas alta (o falta admin.env); no se usa. Ejecuta kit contrasenas cerrar-alta."
  check_private "$CONTRASENAS_ADMIN_ENV"
}

# --- docker --------------------------------------------------------------------------------

# ENS_VERSION es obligatoria en compose.yaml; compose y Caddyfile son los de la release en uso.
dc() {
  [ -n "$REL" ] || die "interno: no hay release seleccionada."
  profiles=
  [ ! -e "$TUNNEL_ENV_FILE" ] || profiles=tunnel
  [ ! -e "$AGENT_ENV_FILE" ] || profiles="${profiles:+$profiles,}agents"
  [ ! -e "$UI_ENV_FILE" ] || profiles="${profiles:+$profiles,}ui"
  if copias_enabled; then
    check_copias_override
    profiles="${profiles:+$profiles,}copias"
    set -- -f "$COPIAS_OVERRIDE" "$@"
  fi
  claves_config=
  if claves_enabled; then
    check_claves_config
    profiles="${profiles:+$profiles,}claves"
    claves_config=$CLAVES_CONFIG
  fi
  if contrasenas_enabled; then
    profiles="${profiles:+$profiles,}contrasenas"
    if [ -e "$CONTRASENAS_ALTA" ] || [ -L "$CONTRASENAS_ALTA" ]; then
      check_alta_override
      set -- -f "$CONTRASENAS_ALTA" "$@"
    fi
  fi
  # Vacíos, compose.yaml pone `.invalid` y Caddy contesta 403 a todo en ese servicio.
  COMPOSE_PROFILES=$profiles ENS_ENV_FILE=$ENV_FILE ENS_TUNNEL_ENV_FILE=$TUNNEL_ENV_FILE \
    ENS_AGENT_ENV_FILE=$AGENT_ENV_FILE ENS_UI_ENV_FILE=$UI_ENV_FILE \
    ENS_UI_HOST=$UI_HOST ENS_AGENT_HOST=$AGENT_HOST ENS_COPIAS_HOST=$COPIAS_HOST \
    ENS_CLAVES_HOST=$CLAVES_HOST ENS_CLAVES_CONFIG=$claves_config ENS_CONTRASENAS_HOST=$CONTRASENAS_HOST \
    docker compose -p "$PROJECT" -f "$REL/compose.yaml" --project-directory "$REL" "$@"
}

in_app() { dc run --rm --no-deps -T app "$@"; }

image_ref() { printf 'ens-evidence:%s' "$1"; }
image_id() { docker image inspect --format '{{.Id}}' "$(image_ref "$1")" 2>/dev/null || true; }

python_image() {
  sed -n 's/^ARG PYTHON_IMAGE=//p' "$ROOT/Dockerfile"
}

# Contenedor de un solo uso, sin red ni privilegios, con el volumen de datos y el mismo UID
# que la app. Usa la imagen base de Python (independiente de la versión desplegada, que puede
# estar rota). El volumen debe existir ya con el propietario correcto (lo crea la imagen de la app).
helper() {
  docker run --rm -i --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user "$APP_UID:$APP_UID" \
    -v "$VOLUME:/data" "$(python_image)" "$@"
}

# Sin el volumen creado por la imagen de la app, un `docker run -v` lo crearía con root.
require_volume() {
  docker volume inspect "$VOLUME" >/dev/null 2>&1 || die "No hay caja instalada (falta el volumen $VOLUME); usa install."
}

# Registro del despliegue en /data/state/box-deploy.json. Solo cambia por estas operaciones.
state() {
  helper python -I - "$STATE_FILE" "$@" <<'PY'
import json
import os
import sys

path, op, *args = sys.argv[1:]


def load():
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {"current": None, "previous": None, "attempt": None, "failed": None}


def save(data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


data = load()
if op == "get":
    value = data
    for part in args[0].split("."):
        value = value.get(part) if isinstance(value, dict) else None
    print("" if value is None else value)
elif op == "set":
    data[args[0]] = dict(item.split("=", 1) for item in args[1:])
    save(data)
elif op == "promote":
    if not data.get("attempt"):
        sys.exit("no hay un intento que promover; no se cambia el registro")
    data["previous"], data["current"], data["attempt"] = data["current"], data["attempt"], None
    save(data)
elif op == "rolled-back":
    if data.get("attempt"):
        data["failed"], data["attempt"] = data["attempt"], None
    else:
        data["failed"], data["current"], data["previous"] = (
            data["current"],
            data["previous"],
            None,
        )
    save(data)
elif op == "dump":
    print(json.dumps(data, indent=2, sort_keys=True))
else:
    sys.exit("operación desconocida")
PY
}

# Úsese siempre en una asignación (`x=$(state_get k)`): así un fallo de lectura aborta el script.
state_get() { state get "$1" || die "No se puede leer $STATE_FILE ($1)."; }

# Ajustes de la copia: 1 si backup.restic_repository está configurado, 0 si no. Pregunta a la
# propia app (modelo Settings); cualquier otra salida aborta, nunca se sigue sin copia por error.
backup_configured() {
  value=$(in_app python -I -c "
from ens_evidence.workspace.settings import load_settings, resolve_settings_path
print(1 if load_settings(resolve_settings_path()).backup else 0)") ||
    die "No se pudo leer /data/settings.yaml; no se continúa."
  case $value in
    0 | 1) printf '%s' "$value" ;;
    *) die "Respuesta inesperada al leer los ajustes de la copia: no se continúa." ;;
  esac
}

# Avisa de todo lo que falta para que la copia funcione.
warn_backup_gaps() {
  configured=$(backup_configured)
  gaps=
  [ "$configured" = 1 ] || gaps="backup.restic_repository en /data/settings.yaml (volumen $VOLUME)"
  for name in ENS_BACKUP_AWS_ACCESS_KEY_ID ENS_BACKUP_AWS_SECRET_ACCESS_KEY ENS_BACKUP_RESTIC_PASSWORD; do
    grep -q "^$name=." "$ENV_FILE" || gaps="$gaps${gaps:+; }$name en deploy/.env"
  done
  [ -z "$gaps" ] || warn "La copia no está lista. Falta: $gaps (runbook de la caja, sección 6)."
}

# Espera a que un servicio esté healthy. 0 sano; 1 no sano, reiniciándose o sin arrancar.
# El latido del app vive en el tmpfs de su contenedor (compose.yaml): healthy exige que el
# scheduler de este contenedor haya latido, no el de la versión anterior.
wait_healthy() {
  info "Esperando a que $1 esté sano (hasta ${HEALTH_TIMEOUT}s)..."
  waited=0
  restarting=0
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    container=$(dc ps -q "$1" 2>/dev/null || true)
    if [ -n "$container" ]; then
      case "$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container")" in
        "running healthy") return 0 ;;
        "running unhealthy" | exited* | dead*) return 1 ;;
        restarting*)
          restarting=$((restarting + 1))
          [ "$restarting" -lt 2 ] || return 1
          ;;
      esac
    fi
    sleep 5
    waited=$((waited + 5))
  done
  return 1
}

# Un servicio opcional corre si existe su fichero de entorno ($1) y la release en uso lo define ($2).
service_enabled() {
  [ -e "$1" ] && dc config --services | grep -qx "$2"
}

agent_enabled() { service_enabled "$AGENT_ENV_FILE" agent; }
ui_enabled() { service_enabled "$UI_ENV_FILE" ui; }

# Una release sin el servicio $1 (rollback a una versión anterior) deja su contenedor huérfano:
# se para, sin borrarlo ni tocar volúmenes.
stop_orphan() {
  for container in $(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1"); do
    warn "La release $target no define $1: se para el contenedor huérfano (los datos no se tocan)."
    docker stop "$container" >/dev/null
  done
}

stop_orphan_services() {
  agent_enabled || stop_orphan agent
  ui_enabled || stop_orphan ui
  copias_enabled || stop_orphan backrest
  claves_enabled || stop_orphan openbao
  contrasenas_enabled || stop_orphan contrasenas-export
  contrasenas_enabled || stop_orphan vaultwarden
}

# La imagen de Backrest es scratch: no hay healthcheck. Corre y no se reinicia durante unos segundos.
wait_running() {
  info "Comprobando que $1 corre..."
  waited=0
  stable=0
  while [ "$waited" -lt 30 ]; do
    container=$(dc ps -q "$1" 2>/dev/null || true)
    if [ -n "$container" ] && [ "$(docker inspect --format '{{.State.Status}}' "$container")" = running ]; then
      stable=$((stable + 1))
      [ "$stable" -lt 3 ] || return 0
    else
      stable=0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

verify_running_app() {
  wait_healthy app || return 1
  dc exec -T app ens-evidence scheduler status >/dev/null || return 1
}

verify_running() {
  verify_running_app || return 1
  ! agent_enabled || wait_healthy agent || return 1
  ! ui_enabled || wait_healthy ui || return 1
  ! copias_enabled || wait_running backrest || return 1
  ! claves_enabled || wait_running openbao || return 1
  ! contrasenas_enabled || wait_healthy vaultwarden || return 1
  ! contrasenas_enabled || wait_running contrasenas-export || return 1
  wait_healthy caddy || return 1
}

# --- release y construcción -----------------------------------------------------------------

# Fija COMMIT al commit de la etiqueta (refs/tags, sin ambigüedad con ramas).
resolve_tag() {
  git -C "$ROOT" rev-parse --verify --quiet "refs/tags/$1^{commit}" >/dev/null ||
    die "La etiqueta $1 no existe en $ROOT (¿git fetch --tags?)."
  COMMIT=$(git -C "$ROOT" rev-parse "refs/tags/$1^{commit}") || die "No se puede resolver $1."
}

# Fija REL al directorio con compose.yaml y el Caddyfile del commit $1. Es estable (compose monta
# el Caddyfile desde ahí) y se verifica contra git en cada uso. $2 y $3, si se pasan, son los
# sha256 registrados de compose.yaml y del Caddyfile.
use_release() {
  commit=$1
  # El commit puede venir del registro, que está en un volumen escribible por la app.
  case $commit in
    '' | *[!0-9a-f]*) die "El commit registrado no es un hash de git: $commit" ;;
  esac
  [ "${#commit}" -eq 40 ] || [ "${#commit}" -eq 64 ] || die "El commit registrado no es un hash de git: $commit"
  git -C "$ROOT" rev-parse --verify --quiet "$commit^{commit}" >/dev/null ||
    die "El commit registrado $commit no existe en $ROOT (¿git fetch --tags?)."
  releases="$(git_dir)/ens-box/releases"
  files=$RELEASE_FILES
  for file in $OPTIONAL_RELEASE_FILES; do
    ! git -C "$ROOT" cat-file -e "$commit:$file" 2>/dev/null || files="$files $file"
  done
  REL="$releases/$commit"
  if [ ! -d "$REL" ]; then
    new_work_dir "$releases"
    # shellcheck disable=SC2086 # son rutas fijas sin espacios
    git -C "$ROOT" archive --format=tar -o "$WORK_DIR/release.tar" "$commit" $files ||
      die "No se puede extraer la release $commit de git."
    mkdir "$WORK_DIR/release"
    tar -x -f "$WORK_DIR/release.tar" -C "$WORK_DIR/release"
    mv "$WORK_DIR/release" "$REL"
    cleanup
  fi
  for file in $files; do
    git -C "$ROOT" show "$commit:$file" | cmp -s - "$REL/$file" ||
      die "$REL/$file no coincide con el commit $commit; no se despliega."
  done
  [ -z "${2:-}" ] || [ "$(sha256_of "$REL/compose.yaml")" = "$2" ] ||
    die "compose.yaml de la release no es el registrado para este despliegue."
  [ -z "${3:-}" ] || [ "$(sha256_of "$REL/deploy/Caddyfile")" = "$3" ] ||
    die "El Caddyfile de la release no es el registrado para este despliegue."
}

# Este script tiene que ser el de la etiqueta que se despliega.
require_script_from_release() {
  cmp -s "$0" "$REL/deploy/box.sh" ||
    die "deploy/box.sh no es el de la etiqueta $1: ejecuta git checkout $1 y repite."
}

build_image() {
  tag=$1
  commit=$2
  new_work_dir
  git -C "$ROOT" archive --format=tar -o "$WORK_DIR/source.tar" "$commit" ||
    die "No se puede extraer $commit de git."
  mkdir "$WORK_DIR/source"
  tar -x -f "$WORK_DIR/source.tar" -C "$WORK_DIR/source"
  info "Construyendo $(image_ref "$tag") desde $tag ($commit)..."
  docker build --label "org.opencontainers.image.revision=$commit" \
    --label "org.opencontainers.image.version=$tag" -t "$(image_ref "$tag")" "$WORK_DIR/source"
  cleanup
}

# Argumentos clave=valor del registro de un despliegue: etiqueta, commit, copia previa.
record_args() {
  printf 'tag=%s commit=%s image_id=%s compose_sha256=%s caddyfile_sha256=%s backup=%s at=%s' \
    "$1" "$2" "$(image_id "$1")" "$(sha256_of "$REL/compose.yaml")" \
    "$(sha256_of "$REL/deploy/Caddyfile")" "${3:-none}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

print_status() {
  info ""
  info "Registro del despliegue:"
  state dump
  info ""
  dc ps
}

# --- subcomandos ---------------------------------------------------------------------------

cmd_install() {
  [ $# -eq 1 ] || usage
  tag=$1
  valid_tag "$tag" || die "Etiqueta no válida: $tag (esperado vX.Y.Z o vX.Y.Z-rcN)."
  require_tools
  acquire_lock
  check_secret_files
  resolve_tag "$tag"
  use_release "$COMMIT"
  require_script_from_release "$tag"
  ensure_env_file
  export ENS_VERSION="$tag"

  if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    installed=$(state_get current.tag)
    [ -z "$installed" ] || die "Ya hay una caja instalada ($installed); usa update."
  fi

  build_image "$tag" "$COMMIT"

  # La primera vez que se monta el volumen lo crea la imagen de la app con el propietario 10001.
  in_app sh -c 'test -w /data' ||
    die "El volumen $VOLUME no es escribible por el UID $APP_UID. No lo toques desde el host: elimínalo a mano solo si está vacío."
  in_app ens-evidence init --path /data/workspace

  info "Ejecutando el primer ciclo (antes de arrancar el scheduler)..."
  code=0
  in_app ens-evidence report daily --cycle || code=$?
  case "$code" in
    0) ;;
    3) warn "El informe se escribió con conectores en ERROR (conectores activos sin credenciales o que fallan)." ;;
    *) die "El primer ciclo falló (código $code)." ;;
  esac
  warn_backup_gaps

  dc up -d --no-build
  verify_running || die "La caja no está sana; revisa: docker compose -p $PROJECT logs app"
  # shellcheck disable=SC2046 # record_args emite clave=valor sin espacios
  state set current $(record_args "$tag" "$COMMIT" none)
  info "Caja instalada: $tag"
  info ""
  info "Siguientes pasos (docs/runbooks/caja.md):"
  info "  1. Credenciales de los conectores en deploy/.env (sección 3)."
  info "  2. Copia a R2: ajustes en el volumen y credenciales (sección 6)."
  info "  3. Inscribir y aprobar el alcance (sección 5): scope/ está en el volumen $VOLUME,"
  info "     no en este clon. Sin alcance aprobado los checks no corren."
  print_status
}

cmd_update() {
  skip_backup=0
  seal_ok=0
  while [ $# -gt 1 ]; do
    case $1 in
      --no-backup) skip_backup=1 ;;
      --seal-ok) seal_ok=1 ;;
      *) usage ;;
    esac
    shift
  done
  [ $# -eq 1 ] || usage
  tag=$1
  valid_tag "$tag" || die "Etiqueta no válida: $tag (esperado vX.Y.Z o vX.Y.Z-rcN)."
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  current=$(state_get current.tag)
  [ -n "$current" ] || die "No hay caja instalada; usa install."
  [ "$tag" != "$current" ] || die "$tag ya es la versión actual."

  # Primero lo que puede fallar sin tocar nada: la etiqueta nueva y su release.
  resolve_tag "$tag"
  new_commit=$COMMIT
  use_release "$new_commit"
  require_script_from_release "$tag"
  ! openbao_would_recreate "$tag" || confirm_seal_loss "$seal_ok"

  # La copia previa y la lectura de ajustes se hacen con la versión que corre ahora.
  current_commit=$(state_get current.commit)
  current_compose=$(state_get current.compose_sha256)
  current_caddyfile=$(state_get current.caddyfile_sha256)
  use_release "$current_commit" "$current_compose" "$current_caddyfile"
  export ENS_VERSION="$current"

  backup=not_configured
  if [ "$skip_backup" = 1 ]; then
    backup=skipped_by_flag
    warn "Se omite la copia previa (--no-backup); queda registrado."
  else
    configured=$(backup_configured)
    if [ "$configured" = 1 ]; then
      info "Copia previa (backup run)..."
      in_app ens-evidence backup run || die "La copia previa falló: no se actualiza. Corrígela o usa --no-backup."
      backup="done"
    else
      warn "La copia no está configurada (backup.restic_repository en /data/settings.yaml): se actualiza sin copia previa."
    fi
  fi

  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  # shellcheck disable=SC2016 # $1, $d y $f los expande el sh del contenedor
  helper sh -c 'set -eu; umask 077; d=/data/state/pre-update-$1; mkdir -p "$d"
    for f in /data/settings.yaml /data/state/box-deploy.json; do
      [ ! -e "$f" ] || cp -p "$f" "$d"/
    done' sh "$stamp" || die "No se pudo hacer la copia local previa a /data/state/pre-update-$stamp; no se actualiza."

  build_image "$tag" "$new_commit"
  use_release "$new_commit"
  # shellcheck disable=SC2046 # record_args emite clave=valor sin espacios
  state set attempt $(record_args "$tag" "$new_commit" "$backup")
  export ENS_VERSION="$tag"
  if dc up -d --no-build && verify_running; then
    state promote
    info "Actualizado a $tag (anterior: $current)."
    print_status
    return 0
  fi
  warn "La versión $tag no está sana (healthcheck o scheduler status)."
  warn "Los datos se conservan. Para volver a $current: deploy/box.sh rollback"
  exit 1
}

cmd_rollback() {
  seal_ok=0
  case ${1:-} in
    --seal-ok) seal_ok=1; shift ;;
  esac
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  attempt=$(state_get attempt.tag)
  from=previous
  [ -z "$attempt" ] || from=current
  target=$(state_get "$from.tag")
  [ -n "$target" ] || die "No hay versión anterior registrada a la que volver."
  wanted_id=$(state_get "$from.image_id")
  commit=$(state_get "$from.commit")
  compose_sha=$(state_get "$from.compose_sha256")
  caddyfile_sha=$(state_get "$from.caddyfile_sha256")
  [ "$(image_id "$target")" = "$wanted_id" ] ||
    die "La imagen local $(image_ref "$target") no es la registrada; no se revierte sola."
  use_release "$commit" "$compose_sha" "$caddyfile_sha"
  export ENS_VERSION="$target"
  # K12: si la release de destino cambia la configuración de OpenBao, `up` lo recrearía y sellaría.
  ! openbao_would_recreate "$target" || confirm_seal_loss "$seal_ok"
  info "Volviendo a $target (los datos no se tocan)..."
  dc up -d --no-build
  stop_orphan_services
  verify_running || die "$target no está sana tras el rollback; revisa los logs."
  state rolled-back
  info "Rollback hecho: $target"
  print_status
}

# Fija REL a la release registrada como actual (con sus sha256) y `current` a su etiqueta.
use_current_release() {
  current=$(state_get current.tag)
  [ -n "$current" ] || die "No hay caja instalada; usa install."
  [ -z "$(state_get attempt.tag)" ] ||
    die "Hay una actualización sin completar; no se sabe qué versión debe correr. Usa rollback."
  commit=$(state_get current.commit)
  compose_sha=$(state_get current.compose_sha256)
  caddyfile_sha=$(state_get current.caddyfile_sha256)
  use_release "$commit" "$compose_sha" "$caddyfile_sha"
  export ENS_VERSION="$current"
}

recreate_service() {
  service=$1
  # La etiqueta de la imagen es mutable: recrear la app exige que siga siendo la registrada.
  if [ "$service" = app ]; then
    wanted_id=$(state_get current.image_id)
    [ "$(image_id "$current")" = "$wanted_id" ] ||
      die "La imagen local $(image_ref "$current") no es la registrada; no se recrea."
  fi
  info "Recreando $service con la release $current..."
  dc up -d --no-build --no-deps --force-recreate "$service"
  case $service in
    app) verify_running_app ;;
    backrest) wait_running backrest ;;
    openbao) wait_running openbao ;;
    contrasenas-export) wait_running contrasenas-export ;;
    *) wait_healthy "$service" ;;
  esac || die "$service no está sano tras recrearlo; revisa: docker compose -p $PROJECT logs $service"
  info "Recreado: $service ($current)"
}

# Recrea un servicio con la release registrada como actual (compose.yaml y Caddyfile con los
# sha256 del registro), nunca con los ficheros del clon.
cmd_restart() {
  [ $# -le 1 ] || usage
  service=${1:-app}
  case $service in
    app | caddy) ;;
    cloudflared) [ -e "$TUNNEL_ENV_FILE" ] || die "cloudflared necesita $TUNNEL_ENV_FILE (perfil tunnel)." ;;
    agent) [ -e "$AGENT_ENV_FILE" ] || die "agent necesita $AGENT_ENV_FILE (perfil agents)." ;;
    ui) [ -e "$UI_ENV_FILE" ] || die "ui necesita $UI_ENV_FILE (perfil ui)." ;;
    backrest) [ -e "$COPIAS_OVERRIDE" ] || die "backrest necesita $COPIAS_OVERRIDE (deploy/box.sh kit enable copias)." ;;
    vaultwarden | contrasenas-export) [ -e "$CONTRASENAS_MANIFEST" ] || die "$service necesita el kit de contraseñas (deploy/box.sh kit enable contrasenas)." ;;
    *) usage ;;
  esac
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  recreate_service "$service"
}

# K12: recrear OpenBao lo sella y no hay desbloqueo automático. Con la release $1 (REL) y el
# servicio en marcha, ¿cambiaría su configuración de Compose (imagen incluida)?
openbao_would_recreate() {
  claves_enabled || return 1
  export ENS_VERSION="$1"
  running=$(dc ps -q openbao 2>/dev/null || true)
  [ -n "$running" ] || return 1
  have=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$running")
  want=$(dc config --hash openbao 2>/dev/null | awk '{ print $2 }')
  [ -n "$want" ] && [ "$have" = "$want" ] && return 1
  return 0
}

# $1 = 1 si se pasó --seal-ok. Sin él hace falta una persona delante: en modo no interactivo se para.
confirm_seal_loss() {
  warn "OpenBao se va a recrear y quedará SELLADO: harán falta los custodios con sus partes (kit claves unseal)."
  [ "$1" = 1 ] && return 0
  [ -t 0 ] || die "No hay terminal para confirmarlo; usa --seal-ok si los custodios están localizables. No se ha cambiado nada."
  printf 'Escribe SELLAR para continuar: '
  read -r answer || answer=
  [ "$answer" = SELLAR ] || die "Sin confirmación no se recrea OpenBao. No se ha cambiado nada."
}

# Avisa de los contenedores que no se crearon con el compose.yaml de la release en uso.
warn_foreign_compose() {
  for container in $(dc ps -q 2>/dev/null || true); do
    files=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$container")
    [ "$files" = "$REL/compose.yaml" ] && continue
    name=$(docker inspect --format '{{.Name}}' "$container")
    service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container")
    warn "$name no se creó con $REL/compose.yaml (etiqueta: ${files:-vacía}); recréalo con deploy/box.sh restart $service."
  done
}

cmd_status() {
  [ $# -eq 0 ] || usage
  require_tools
  check_secret_files
  require_volume
  current=$(state_get current.tag)
  [ -n "$current" ] || die "No hay caja instalada; usa install."
  commit=$(state_get current.commit)
  compose_sha=$(state_get current.compose_sha256)
  caddyfile_sha=$(state_get current.caddyfile_sha256)
  attempt=$(state_get attempt.tag)
  use_release "$commit" "$compose_sha" "$caddyfile_sha"
  export ENS_VERSION="$current"
  if [ -n "$attempt" ]; then
    warn "Actualización a $attempt sin completar; la versión registrada es $current. Para volver: deploy/box.sh rollback"
  fi
  print_status
  [ -n "$attempt" ] || warn_foreign_compose
  if claves_enabled; then
    info ""
    info "Claves (OpenBao): $(openbao_seal_state || echo 'no responde')"
  fi
  if contrasenas_enabled; then
    info ""
    if [ -e "$CONTRASENAS_ALTA" ] || [ -L "$CONTRASENAS_ALTA" ]; then
      warn "Contraseñas (Vaultwarden): alta ABIERTA, el panel /admin está activo. Ciérrala: deploy/box.sh kit contrasenas cerrar-alta"
    else
      info "Contraseñas (Vaultwarden): alta cerrada."
    fi
  fi
  if [ -z "$attempt" ] && [ -n "$(dc ps -q app 2>/dev/null)" ]; then
    info ""
    dc exec -T app ens-evidence scheduler status || warn "scheduler status falló."
  fi
}

# --- kit de herramientas -------------------------------------------------------------------

# Volumen de un kit ($1) para el UID $2: lo crea con las etiquetas de Compose y lo deja de ese UID.
# Es el único paso con root, sin más capacidad que CHOWN; las imágenes scratch/distroless no
# traen directorios propios.
prepare_volume() {
  volume="${PROJECT}_$1"
  docker volume inspect "$volume" >/dev/null 2>&1 ||
    docker volume create --label "com.docker.compose.project=$PROJECT" \
      --label "com.docker.compose.volume=$1" "$volume" >/dev/null
  # shellcheck disable=SC2016 # $(stat ...) lo expande el sh del contenedor
  docker run --rm --network none --read-only --cap-drop ALL --cap-add CHOWN \
    --security-opt no-new-privileges:true --user 0:0 -v "$volume:/v" "$(python_image)" \
    sh -c '[ "$(stat -c %u /v)" = "$1" ] || { chmod 700 /v && chown "$1:$1" /v; }' sh "$2" ||
    die "No se pudo preparar el volumen $volume."
}

# Un contenedor de un solo uso con el volumen de configuración de Backrest y su UID. $@ es el comando sh.
in_backrest_config() {
  docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --user "$BACKREST_UID:$BACKREST_UID" -v "${PROJECT}_backrest-config:/config" "$(python_image)" sh -c "$@"
}

# MANIFEST del paquete (contrato C2): `version: 1` y, por fichero, `<sha256>  copias/<fichero>`.
verify_copias_package() {
  dir=$1
  manifest="$dir/MANIFEST"
  [ "$(sed -n 1p "$manifest")" = "version: 1" ] || die "MANIFEST: versión de formato no admitida."
  [ "$(sed -n '2,$p' "$manifest" | grep -c .)" -eq 4 ] || die "MANIFEST: debe listar exactamente cuatro ficheros."
  for name in config.json compose.yaml app.env backups.yaml; do
    want=$(sed -n "s/^\([0-9a-f]\{64\}\)  copias\/$(printf '%s' "$name" | sed 's/[.]/[.]/g')\$/\1/p" "$manifest")
    [ "$(printf '%s' "$want" | wc -c)" -eq 64 ] || die "MANIFEST: falta o se repite el sha256 de $name."
    [ "$(sha256_of "$dir/$name")" = "$want" ] || die "MANIFEST: el sha256 de $name no coincide; no se instala nada."
  done
}

# Extrae el paquete en $2 solo si contiene exactamente los cinco ficheros esperados, como ficheros normales.
extract_copias_package() {
  package=$1
  dest=$2
  names=$(tar -tzf "$package" 2>/dev/null) || die "$package no es un tar.gz válido."
  printf '%s\n' "$names" | grep -vxE 'copias/?|copias/(MANIFEST|config[.]json|compose[.]yaml|app[.]env|backups[.]yaml)' |
    grep -q . && die "El paquete contiene ficheros que no son del contrato; no se instala."
  [ -z "$(printf '%s\n' "$names" | grep -v '/$' | sort | uniq -d)" ] || die "El paquete repite ficheros; no se instala."
  tar -tvzf "$package" | grep -q '^[^-d]' &&
    die "El paquete contiene enlaces u otros tipos de fichero; no se instala."
  mkdir "$dest"
  tar -xzf "$package" -C "$dest"
  for name in MANIFEST config.json compose.yaml app.env backups.yaml; do
    [ -f "$dest/copias/$name" ] || die "Al paquete le falta copias/$name."
  done
}

# Solo `services.backrest.volumes`, de host en solo lectura bajo /source/. Ni raíz ni el socket de Docker.
validate_copias_override() {
  in_app python -I -c '
import re
import sys

import yaml

doc = yaml.safe_load(sys.stdin)
ok = isinstance(doc, dict) and set(doc) == {"services"} and isinstance(doc["services"], dict)
ok = ok and set(doc["services"]) == {"backrest"} and isinstance(doc["services"]["backrest"], dict)
ok = ok and set(doc["services"]["backrest"]) == {"volumes"}
if not ok:
    sys.exit("compose.yaml del paquete: solo se admite services.backrest.volumes")
shape = re.compile(r"(/[A-Za-z0-9._/@+-]*):(/source/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*):ro")
denied = ("/proc", "/sys", "/dev", "/run", "/var/run")
for volume in doc["services"]["backrest"]["volumes"]:
    found = shape.fullmatch(volume) if isinstance(volume, str) else None
    if not found:
        sys.exit("compose.yaml del paquete: cada montaje debe ser /ruta:/source/<nombre>:ro")
    host, target = found.groups()
    parts = host.split("/")
    if host == "/" or ".." in parts or "" in parts[1:] or "docker.sock" in host:
        sys.exit("compose.yaml del paquete: ruta del host no admitida")
    if any(host == d or host.startswith(d + "/") for d in denied):
        sys.exit("compose.yaml del paquete: ruta del host no admitida")
    if target == "/source/caja" or target.startswith("/source/caja/"):
        sys.exit("compose.yaml del paquete: /source/caja es de la caja")
    if target.startswith("/source/claves"):
        sys.exit("compose.yaml del paquete: /source/claves* es del kit de claves")
    if target.startswith("/source/contrasenas"):
        sys.exit("compose.yaml del paquete: /source/contrasenas* es del kit de contraseñas")
'
}

# Usuario inicial de Backrest (invariante: nunca arranca publicado sin usuario con contraseña).
# $1 es el bcrypt en base64 (formato de config.proto); lee config.json por stdin y lo escribe por stdout.
add_backrest_user() {
  in_app python -I -c '
import json
import re
import sys

if not re.fullmatch(r"JDJ[A-Za-z0-9+/]{77}", sys.argv[1]):
    sys.exit("el hash bcrypt del usuario de Backrest no tiene el formato esperado")
config = json.load(sys.stdin)
config["auth"] = {"disabled": False, "users": [{"name": "admin", "passwordBcrypt": sys.argv[1]}]}
sys.stdout.write(json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
' "$1"
}

# bcrypt (en base64, como lo guarda Backrest) de la contraseña de stdin, con la imagen de Caddy de la release.
backrest_password_hash() {
  image=$(sed -n 's/^    image: \(caddy:.*\)$/\1/p' "$REL/compose.yaml")
  [ -n "$image" ] || die "No se encuentra la imagen de caddy en $REL/compose.yaml."
  docker run --rm -i --network none --read-only --cap-drop ALL --cap-add NET_BIND_SERVICE \
    --security-opt no-new-privileges:true "$image" caddy hash-password | tr -d '\n' | base64 | tr -d '\n'
}

# La contraseña solo vive en `backrest_password`: va al hash por stdin y se muestra al final, una vez.
new_backrest_credentials() {
  backrest_password=$(head -c 48 /dev/urandom | base64 | tr -d '=+/\n' | cut -c1-32)
  [ "${#backrest_password}" -eq 32 ] || die "No se pudo generar la contraseña de Backrest."
  password_hash=$(printf '%s\n' "$backrest_password" | backrest_password_hash)
  [ -n "$password_hash" ] || die "No se pudo calcular el bcrypt de la contraseña de Backrest."
}

# Solo `repositories` con id y sin claves de credencial (la config del conector no lleva secretos).
validate_copias_fragment() {
  in_app python -I -c '
import re
import sys

import yaml

doc = yaml.safe_load(sys.stdin)
repos = doc.get("repositories") if isinstance(doc, dict) and set(doc) == {"repositories"} else None
if not isinstance(repos, list) or not repos:
    sys.exit("backups.yaml del paquete: solo se admite repositories (lista no vacía)")
secret = re.compile(r"password|secret|token|credential|access_?key", re.IGNORECASE)
for repo in repos:
    if not isinstance(repo, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", str(repo.get("id", ""))):
        sys.exit("backups.yaml del paquete: cada repositorio necesita un id [a-z][a-z0-9_]{1,30}")
    if any(secret.search(str(key)) for key in repo):
        sys.exit("backups.yaml del paquete: no puede llevar credenciales")
'
}

merge_copias_fragment() {
  in_app python -I -c '
import os
import pathlib
import sys

import yaml

path = pathlib.Path("/data/workspace/connectors/backups.yaml")
new = yaml.safe_load(sys.stdin)
current = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
repos = {r["id"]: r for r in current.get("repositories", [])}
repos.update({r["id"]: r for r in new["repositories"]})
current["repositories"] = list(repos.values())
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(".yaml.tmp")
temporary.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
os.replace(temporary, path)
'
}

# app.env: una o más líneas ENS_BACKUPS_<ID>_...=valor (las líneas en blanco se ignoran). No muestra valores.
validate_copias_env() {
  [ "$(grep -c . "$1")" -gt 0 ] || die "app.env del paquete está vacío."
  [ "$(grep -v '^$' "$1" | grep -vcE '^ENS_BACKUPS_[A-Z0-9_]+=')" -eq 0 ] ||
    die "app.env del paquete: solo se admiten variables ENS_BACKUPS_*."
}

# Añade o sustituye las variables ENS_BACKUPS_* de $1 en deploy/.env sin escribirlas en pantalla.
merge_copias_env() {
  merged=$(umask 077 && mktemp "$ROOT/deploy/.env.XXXXXX")
  awk 'NR == FNR { k = index($0, "="); if (k > 0) new[substr($0, 1, k - 1)] = $0; next }
       { k = index($0, "="); key = (k > 0) ? substr($0, 1, k - 1) : ""
         if (key in new) { print new[key]; seen[key] = 1 } else print }
       END { for (key in new) if (!(key in seen)) print new[key] }' "$1" "$ENV_FILE" >"$merged"
  chmod 600 "$merged"
  mv "$merged" "$ENV_FILE"
}

kit_copias_enable() {
  replace=0
  if [ "${1:-}" = "--replace-config" ]; then
    replace=1
    shift
  fi
  [ $# -eq 1 ] || usage
  package=$1
  [ -f "$package" ] && [ ! -L "$package" ] || die "$package no es un fichero normal."
  [ "$(wc -c <"$package")" -le 1048576 ] || die "$package pesa más de 1 MiB; no parece un paquete de copias."
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  grep -q '^  backrest:' "$REL/compose.yaml" ||
    die "La release $current no define el perfil copias; actualiza la caja a una versión que lo incluya."
  [ -n "$COPIAS_HOST" ] ||
    die "Define ENS_COPIAS_HOST en $UI_ENV_FILE (p. ej. copias.example.org) antes de activar las copias."

  # Todo lo que puede fallar sin tocar nada, antes de instalar.
  new_work_dir
  extract_copias_package "$package" "$WORK_DIR/pkg"
  pkg="$WORK_DIR/pkg/copias"
  verify_copias_package "$pkg"
  validate_copias_override <"$pkg/compose.yaml"
  validate_copias_fragment <"$pkg/backups.yaml"
  validate_copias_env "$pkg/app.env"
  prepare_volume backrest-config "$BACKREST_UID"
  prepare_volume backrest-data "$BACKREST_UID"
  has_config=$(in_backrest_config 'test -e /config/config.json && echo 1 || echo 0')
  [ "$has_config" = 0 ] || [ "$replace" = 1 ] ||
    die "Backrest ya tiene configuración (con su usuario). Para sustituirla por la del paquete usa --replace-config (la anterior queda como config.json.prev en su volumen)."

  new_backrest_credentials

  # Instalación. config.json (con el usuario inicial) solo llega al volumen por stdin; app.env solo a deploy/.env (600).
  add_backrest_user "$password_hash" <"$pkg/config.json" |
    in_backrest_config 'set -eu; umask 077
    cat >/config/config.json.new
    [ -s /config/config.json.new ] || { rm -f /config/config.json.new; exit 1; }
    [ ! -e /config/config.json ] || mv /config/config.json /config/config.json.prev
    mv /config/config.json.new /config/config.json' ||
    die "No se pudo escribir la configuración de Backrest en su volumen."
  ensure_env_file
  merge_copias_env "$pkg/app.env"
  merge_copias_fragment <"$pkg/backups.yaml" || die "No se pudo escribir connectors/backups.yaml en el workspace."
  mkdir -p "$COPIAS_DIR"
  chmod 700 "$ROOT/deploy/kit" "$COPIAS_DIR"
  (umask 077 && cp "$pkg/MANIFEST" "$COPIAS_DIR/MANIFEST.new" && cp "$pkg/compose.yaml" "$COPIAS_DIR/compose.yaml.new")
  mv "$COPIAS_DIR/compose.yaml.new" "$COPIAS_OVERRIDE"
  mv "$COPIAS_DIR/MANIFEST.new" "$COPIAS_DIR/MANIFEST"
  cleanup

  info "Arrancando Backrest y publicando $COPIAS_HOST..."
  dc up -d --no-build --no-deps --force-recreate backrest caddy
  if wait_running backrest; then
    # Con la configuración nueva sana, sus restos con claves de escritura ya no sirven para nada.
    in_backrest_config 'rm -f /config/config.json.prev /config/config.json.pre-update' ||
      warn "No se pudieron borrar config.json.prev ni config.json.pre-update del volumen; bórralos a mano."
  else
    if [ "$has_config" = 1 ]; then
      in_backrest_config 'set -eu; umask 077; cp /config/config.json.prev /config/config.json.new && mv /config/config.json.new /config/config.json' &&
        dc up -d --no-build --no-deps --force-recreate backrest || true
      die "Backrest no arranca con la configuración del paquete. Se ha restaurado la anterior; config.json.prev se conserva para el rollback. Revisa: docker compose -p $PROJECT logs backrest"
    fi
    die "Backrest no arranca; revisa: docker compose -p $PROJECT logs backrest"
  fi
  wait_healthy caddy || die "caddy no está sano; revisa: docker compose -p $PROJECT logs caddy"
  recreate_service app
  info ""
  info "Copias activadas. Backrest ya pide usuario y contraseña (docs/runbooks/kit-copias.md, sección 2):"
  info "  Usuario:    admin"
  info "  Contraseña: $backrest_password"
  info "  Se muestra solo ahora y no se guarda en ningún sitio: pásala a tu gestor de contraseñas."
  info "  Si la pierdes: deploy/box.sh kit copias reset-password (no hace falta el paquete)."
  info "  Borra el paquete $package: lleva las claves de escritura."
}

# Sin paquete: reescribe solo `auth` del config.json que ya hay en el volumen y reinicia Backrest.
kit_copias_reset_password() {
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  [ -e "$COPIAS_OVERRIDE" ] || die "Las copias no están activadas (falta $COPIAS_OVERRIDE); no hay contraseña que restablecer."
  has_config=$(in_backrest_config 'test -s /config/config.json && echo 1 || echo 0')
  [ "$has_config" = 1 ] || die "Backrest no tiene config.json en su volumen; activa las copias con kit enable copias."
  new_backrest_credentials
  in_backrest_config 'cat /config/config.json' | add_backrest_user "$password_hash" |
    in_backrest_config 'set -eu; umask 077
    cat >/config/config.json.new
    [ -s /config/config.json.new ] || { rm -f /config/config.json.new; exit 1; }
    mv /config/config.json.new /config/config.json' ||
    die "No se pudo reescribir la configuración de Backrest; la contraseña anterior sigue vigente."
  dc up -d --no-build --no-deps --force-recreate backrest
  wait_running backrest || die "Backrest no arranca; revisa: docker compose -p $PROJECT logs backrest"
  info ""
  info "Contraseña de Backrest restablecida:"
  info "  Usuario:    admin"
  info "  Contraseña: $backrest_password"
  info "  Se muestra solo ahora y no se guarda en ningún sitio: pásala a tu gestor de contraseñas."
}

kit_copias_disable() {
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  [ -e "$COPIAS_OVERRIDE" ] || die "Las copias no están activadas (falta $COPIAS_OVERRIDE)."
  for container in $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=backrest"); do
    docker stop "$container" >/dev/null
    docker rm "$container" >/dev/null
  done
  rm -f "$COPIAS_OVERRIDE" "$COPIAS_DIR/MANIFEST"
  info "Backrest parado y desactivado. No se ha borrado nada:"
  info "  - volúmenes ${PROJECT}_backrest-config y ${PROJECT}_backrest-data (su configuración y su caché),"
  info "  - las variables ENS_BACKUPS_* de deploy/.env ni connectors/backups.yaml del workspace,"
  info "  - las copias en el destino S3."
  info "La ruta $COPIAS_HOST sigue en Caddy y responde 503 mientras no haya Backrest."
}

# --- kit de claves ---------------------------------------------------------------------------

# Contenedor aislado de la red claves-app (la que alcanza a OpenBao), sin logs, con la imagen de la
# app. $@ son las opciones de `docker run` restantes, la imagen y el comando.
claves_isolated() {
  docker run --log-driver none --network "${PROJECT}_claves-app" --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --tmpfs /tmp "$@"
}

# Estado de sellado sin token (sys/seal-status no lo pide): `initialized=true sealed=false`.
openbao_seal_state() {
  docker run --rm --log-driver none --network "${PROJECT}_claves-app" --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user "$APP_UID:$APP_UID" "$(python_image)" python -I -c '
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://openbao:8200/v1/sys/seal-status", timeout=5) as response:
    status = json.load(response)
print("initialized=%s sealed=%s" % (str(status["initialized"]).lower(), str(status["sealed"]).lower()))
' 2>/dev/null
}

wait_openbao_api() {
  info "Esperando a que OpenBao responda..."
  waited=0
  while [ "$waited" -lt 60 ]; do
    openbao_seal_state >/dev/null && return 0
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

# La imagen de la app solo sirve si sigue siendo la registrada (la etiqueta es mutable).
app_image_checked() {
  wanted_id=$(state_get current.image_id)
  [ "$(image_id "$current")" = "$wanted_id" ] ||
    die "La imagen local $(image_ref "$current") no es la registrada; no se usa."
  image_ref "$current"
}

# MANIFEST del paquete (contrato C6): `version: 1` y `<sha256>  claves/<fichero>` de init.json, keystore.yaml y openbao.hcl.
verify_claves_package() {
  dir=$1
  manifest="$dir/MANIFEST"
  [ "$(sed -n 1p "$manifest")" = "version: 1" ] || die "MANIFEST: versión de formato no admitida."
  [ "$(sed -n '2,$p' "$manifest" | grep -c .)" -eq 3 ] || die "MANIFEST: debe listar exactamente tres ficheros."
  for name in init.json keystore.yaml openbao.hcl; do
    want=$(sed -n "s/^\([0-9a-f]\{64\}\)  claves\/$(printf '%s' "$name" | sed 's/[.]/[.]/g')\$/\1/p" "$manifest")
    [ "$(printf '%s' "$want" | wc -c)" -eq 64 ] || die "MANIFEST: falta o se repite el sha256 de $name."
    [ "$(sha256_of "$dir/$name")" = "$want" ] || die "MANIFEST: el sha256 de $name no coincide; no se instala nada."
  done
}

extract_claves_package() {
  package=$1
  dest=$2
  names=$(tar -tzf "$package" 2>/dev/null) || die "$package no es un tar.gz válido."
  printf '%s\n' "$names" | grep -vxE 'claves/?|claves/(MANIFEST|init[.]json|keystore[.]yaml|openbao[.]hcl)' |
    grep -q . && die "El paquete contiene ficheros que no son del contrato; no se instala."
  [ -z "$(printf '%s\n' "$names" | grep -v '/$' | sort | uniq -d)" ] || die "El paquete repite ficheros; no se instala."
  tar -tvzf "$package" | grep -q '^[^-d]' &&
    die "El paquete contiene enlaces u otros tipos de fichero; no se instala."
  mkdir "$dest"
  tar -xzf "$package" -C "$dest"
  for name in MANIFEST init.json keystore.yaml openbao.hcl; do
    [ -f "$dest/claves/$name" ] || die "Al paquete le falta claves/$name."
  done
}

# init.json: solo key_shares y key_threshold enteros, 1 <= umbral <= partes <= 255 (lo repite keystore_setup).
validate_claves_init() {
  in_app python -I -c '
import json
import sys

doc = json.load(sys.stdin)
ok = isinstance(doc, dict) and set(doc) == {"key_shares", "key_threshold"}
ok = ok and all(type(n) is int for n in doc.values())
ok = ok and 1 <= doc["key_threshold"] <= doc["key_shares"] <= 255
if not ok:
    sys.exit("init.json del paquete: solo key_shares y key_threshold, con 1 <= umbral <= partes <= 255")
'
}

# keystore.yaml: configuración del conector, solo las claves admitidas (ninguna es una credencial) y apuntando al OpenBao de la caja.
validate_claves_fragment() {
  in_app python -I -c '
import sys

import yaml

doc = yaml.safe_load(sys.stdin)
allowed = {"asset_id", "address", "max_key_age_days", "max_token_period_hours"}
if not isinstance(doc, dict) or not {"asset_id", "address", "max_key_age_days"} <= set(doc) <= allowed:
    sys.exit("keystore.yaml del paquete: claves no admitidas o faltan asset_id, address y max_key_age_days")
if doc["address"] != "http://openbao:8200":
    sys.exit("keystore.yaml del paquete: address debe ser http://openbao:8200")
'
}

# Instala connectors/<$1>.yaml en el workspace si no existe; uno ya editado no se pisa.
install_connector_fragment() {
  in_app python -I -c '
import os
import pathlib
import sys

path = pathlib.Path("/data/workspace/connectors") / (sys.argv[1] + ".yaml")
new = sys.stdin.read()
if path.exists():
    print("conservado" if path.read_text(encoding="utf-8") != new else "igual")
    sys.exit(0)
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(".yaml.tmp")
temporary.write_text(new, encoding="utf-8")
os.replace(temporary, path)
print("instalado")
' "$1"
}

kit_claves_enable() {
  [ $# -eq 1 ] || usage
  package=$1
  [ -f "$package" ] && [ ! -L "$package" ] || die "$package no es un fichero normal."
  [ "$(wc -c <"$package")" -le 1048576 ] || die "$package pesa más de 1 MiB; no parece un paquete de claves."
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  grep -q '^  openbao:' "$REL/compose.yaml" ||
    die "La release $current no define el perfil claves; actualiza la caja a una versión que lo incluya."
  [ -f "$REL/deploy/openbao/config.hcl" ] || die "La release $current no trae deploy/openbao/config.hcl."
  [ -n "$CLAVES_HOST" ] ||
    die "Define ENS_CLAVES_HOST en $UI_ENV_FILE (p. ej. claves.example.org) antes de activar las claves."

  # Todo lo que puede fallar sin tocar nada, antes de instalar.
  new_work_dir
  extract_claves_package "$package" "$WORK_DIR/pkg"
  pkg="$WORK_DIR/pkg/claves"
  verify_claves_package "$pkg"
  # La configuración de OpenBao (auditoría, listener) la fija la release, no el paquete.
  cmp -s "$pkg/openbao.hcl" "$REL/deploy/openbao/config.hcl" ||
    die "openbao.hcl del paquete no coincide con el de la release $current; genera el paquete con esa versión."
  validate_claves_init <"$pkg/init.json"
  validate_claves_fragment <"$pkg/keystore.yaml"

  # Cambiar el HCL de un OpenBao en marcha lo recrea y lo sella.
  recreate=
  if [ -e "$CLAVES_CONFIG" ] && ! cmp -s "$pkg/openbao.hcl" "$CLAVES_CONFIG" && [ -n "$(dc ps -q openbao 2>/dev/null)" ]; then
    confirm_seal_loss 0
    recreate=--force-recreate
  fi
  prepare_volume openbao-data "$OPENBAO_UID"
  prepare_volume openbao-audit "$OPENBAO_UID"

  # El HCL y init.json no llevan secretos y los lee el UID 10001 del contenedor: modo 644 en un directorio 700.
  mkdir -p "$CLAVES_DIR"
  chmod 700 "$ROOT/deploy/kit" "$CLAVES_DIR"
  (umask 022 && cp "$pkg/openbao.hcl" "$CLAVES_DIR/openbao.hcl.new" && cp "$pkg/init.json" "$CLAVES_DIR/init.json.new" &&
    cp "$pkg/MANIFEST" "$CLAVES_DIR/MANIFEST.new")
  mv "$CLAVES_DIR/openbao.hcl.new" "$CLAVES_CONFIG"
  mv "$CLAVES_DIR/init.json.new" "$CLAVES_DIR/init.json"
  mv "$CLAVES_DIR/MANIFEST.new" "$CLAVES_MANIFEST"
  installed=$(install_connector_fragment keystore <"$pkg/keystore.yaml") ||
    die "No se pudo escribir connectors/keystore.yaml en el workspace."
  cleanup
  [ "$installed" != conservado ] || warn "connectors/keystore.yaml ya existía y difiere del paquete: se conserva el actual."

  info "Arrancando OpenBao y publicando $CLAVES_HOST..."
  # shellcheck disable=SC2086 # $recreate es vacío o una sola opción
  dc up -d --no-build --no-deps $recreate openbao caddy
  wait_running openbao || die "OpenBao no arranca; revisa: docker compose -p $PROJECT logs openbao"
  wait_openbao_api || die "OpenBao no responde en claves-app; revisa: docker compose -p $PROJECT logs openbao"
  wait_healthy caddy || die "caddy no está sano; revisa: docker compose -p $PROJECT logs caddy"
  state_now=$(openbao_seal_state) || die "No se pudo leer el estado de sellado de OpenBao."
  info "Claves activadas (OpenBao: $state_now)."
  case $state_now in
    *initialized=false*) info "Siguiente paso: deploy/box.sh kit claves init (docs/runbooks/kit-claves.md)." ;;
    *sealed=true*) info "Está sellado: deploy/box.sh kit claves unseal (hacen falta los custodios)." ;;
    *) info "Ya inicializado y desbloqueado; no hay nada más que hacer." ;;
  esac
}

# Un solo proceso interactivo dentro de un contenedor de un solo uso con la imagen de la app. Docker
# con -t mezcla stdout y pantalla, así que el JSON final no se puede separar de lo que ve el
# operador. Por eso el contenedor se deja vivo (`sleep`), `exec -it` ejecuta keystore_setup con el
# stdout redirigido a un tmpfs (/out, solo RAM) y, al terminar, se lee el JSON con un `exec` sin
# TTY y se destruye el contenedor. Ni las partes ni el root token salen del contenedor; el token
# de la app y la contraseña viajan por la salida de `docker exec`, nunca por argv ni a disco.
kit_claves_init() {
  [ $# -eq 0 ] || usage
  [ -t 0 ] && [ -t 1 ] || die "init necesita un terminal interactivo (las partes se piden y se muestran en él)."
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  claves_enabled || die "Las claves no están activadas; usa kit enable claves <paquete>."
  [ -f "$CLAVES_DIR/init.json" ] || die "Falta $CLAVES_DIR/init.json; repite kit enable claves con el paquete."
  state_now=$(openbao_seal_state) || die "OpenBao no responde; ¿está en marcha? (deploy/box.sh status)"
  case $state_now in
    *initialized=false*) ;;
    *) die "OpenBao ya está inicializado ($state_now); init no se repite. Si se cortó a medias, sigue la recuperación manual de docs/runbooks/kit-claves.md." ;;
  esac
  image=$(app_image_checked)

  info "Se abre una sesión interactiva con OpenBao. Haz esto en una terminal sin grabación"
  info "(sin script, tmux/screen con log ni grabador de pantalla) y cierra la ventana al terminar."
  INIT_CONTAINER="${PROJECT}-claves-init"
  docker rm -f "$INIT_CONTAINER" >/dev/null 2>&1 || true
  claves_isolated -d --name "$INIT_CONTAINER" --tmpfs /out:uid=10001,mode=700 \
    -v "$CLAVES_DIR/init.json:/init.json:ro" "$image" sleep 3600 >/dev/null ||
    die "No se pudo crear el contenedor de inicialización."
  failed=0
  # shellcheck disable=SC2016 # lo expande el sh del contenedor
  docker exec -it "$INIT_CONTAINER" sh -c 'umask 077; exec python -I -m ens_evidence.kit.keystore_setup init --init-file /init.json >/out/result.json' ||
    failed=1
  if [ "$failed" = 1 ]; then
    cleanup
    die "keystore_setup terminó con error (el motivo se mostró en pantalla). Revisa docs/runbooks/kit-claves.md: si OpenBao llegó a inicializarse, la recuperación es manual."
  fi
  admin_password=$(claves_result password) || admin_password=
  token_line=$(claves_result token) || token_line=
  cleanup
  if [ -z "$admin_password" ] || [ -z "$token_line" ]; then
    die "keystore_setup no entregó el JSON esperado (falta o no es válido). OpenBao puede estar inicializado sin token de app: sigue docs/runbooks/kit-claves.md (paso manual, sin automatismo)."
  fi

  info ""
  info "Contraseña del administrador de OpenBao (usuario admin, método userpass):"
  info "  Contraseña: $admin_password"
  info "  Se muestra solo ahora y no se guarda en ningún sitio: pásala a tu gestor de contraseñas."
  ensure_env_file
  # merge_copias_env no distingue de qué kit son las variables; lee de stdin, sin fichero temporal.
  printf '%s\n' "$token_line" | merge_copias_env /dev/stdin ||
    die "No se pudo escribir ENS_KEYSTORE_TOKEN en $ENV_FILE. Crea un token nuevo con el rol ens-app como administrador (docs/runbooks/kit-claves.md) y pégalo ahí."
  token_line=
  info "ENS_KEYSTORE_TOKEN guardado en $ENV_FILE (modo 600); no se muestra."
  recreate_service app
}

# $1 = token | password. Lee /out/result.json del contenedor de init y valida su forma.
claves_result() {
  docker exec "$INIT_CONTAINER" python -I -c '
import json
import re
import sys

with open("/out/result.json", encoding="utf-8") as handle:
    result = json.load(handle)
token, password = result["app_token"], result["admin_password"]
if not (re.fullmatch(r"[A-Za-z0-9._-]{8,300}", token) and re.fullmatch(r"[A-Za-z0-9_-]{16,200}", password)):
    sys.exit(1)
sys.stdout.write("ENS_KEYSTORE_TOKEN=" + token if sys.argv[1] == "token" else password)
' "$1"
}

kit_claves_unseal() {
  [ $# -eq 0 ] || usage
  [ -t 0 ] && [ -t 1 ] || die "unseal necesita un terminal interactivo (las partes se piden ocultas en él)."
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  claves_enabled || die "Las claves no están activadas; usa kit enable claves <paquete>."
  image=$(app_image_checked)
  claves_isolated --rm -it "$image" python -I -m ens_evidence.kit.keystore_setup unseal ||
    die "unseal no terminó bien (el motivo se mostró en pantalla)."
  info "Estado: $(openbao_seal_state || echo 'no responde')"
}

# Para y quita el contenedor. No borra volúmenes (claves, auditoría) ni deploy/kit/claves: el
# MANIFEST se renombra para que `dc` deje de activar el perfil.
kit_claves_disable() {
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  [ -e "$CLAVES_MANIFEST" ] || die "Las claves no están activadas (falta $CLAVES_MANIFEST)."
  for container in $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=openbao"); do
    docker stop "$container" >/dev/null
    docker rm "$container" >/dev/null
  done
  mv "$CLAVES_MANIFEST" "$CLAVES_MANIFEST.disabled"
  info "OpenBao parado y desactivado. No se ha borrado nada:"
  info "  - volúmenes ${PROJECT}_openbao-data y ${PROJECT}_openbao-audit (las claves y la auditoría),"
  info "  - $CLAVES_DIR (el HCL queda; el MANIFEST pasa a MANIFEST.disabled),"
  info "  - ENS_KEYSTORE_TOKEN de deploy/.env ni connectors/keystore.yaml del workspace."
  info "Al reactivar con kit enable claves, OpenBao arranca sellado: harán falta los custodios."
  info "La ruta $CLAVES_HOST sigue en Caddy y responde 503 mientras no haya OpenBao."
}

# --- kit de contraseñas ----------------------------------------------------------------------

# MANIFEST del paquete: `version: 1` y `<sha256>  contrasenas/passwords.yaml`.
verify_contrasenas_package() {
  dir=$1
  manifest="$dir/MANIFEST"
  [ "$(sed -n 1p "$manifest")" = "version: 1" ] || die "MANIFEST: versión de formato no admitida."
  [ "$(sed -n '2,$p' "$manifest" | grep -c .)" -eq 1 ] || die "MANIFEST: debe listar exactamente un fichero."
  want=$(sed -n 's/^\([0-9a-f]\{64\}\)  contrasenas\/passwords[.]yaml$/\1/p' "$manifest")
  [ "$(printf '%s' "$want" | wc -c)" -eq 64 ] || die "MANIFEST: falta o se repite el sha256 de passwords.yaml."
  [ "$(sha256_of "$dir/passwords.yaml")" = "$want" ] || die "MANIFEST: el sha256 de passwords.yaml no coincide; no se instala nada."
}

extract_contrasenas_package() {
  package=$1
  dest=$2
  names=$(tar -tzf "$package" 2>/dev/null) || die "$package no es un tar.gz válido."
  printf '%s\n' "$names" | grep -vxE 'contrasenas/?|contrasenas/(MANIFEST|passwords[.]yaml)' |
    grep -q . && die "El paquete contiene ficheros que no son del contrato; no se instala."
  [ -z "$(printf '%s\n' "$names" | grep -v '/$' | sort | uniq -d)" ] || die "El paquete repite ficheros; no se instala."
  tar -tvzf "$package" | grep -q '^[^-d]' &&
    die "El paquete contiene enlaces u otros tipos de fichero; no se instala."
  mkdir "$dest"
  tar -xzf "$package" -C "$dest"
  for name in MANIFEST passwords.yaml; do
    [ -f "$dest/contrasenas/$name" ] || die "Al paquete le falta contrasenas/$name."
  done
}

# passwords.yaml: configuración del conector. Con asset_id, sin claves que parezcan credenciales y
# con el export en la ruta donde la app lo monta (la fija compose.yaml, no el paquete).
validate_contrasenas_fragment() {
  in_app python -I -c '
import re
import sys

import yaml

doc = yaml.safe_load(sys.stdin)
if not isinstance(doc, dict) or not isinstance(doc.get("asset_id"), str) or not doc["asset_id"]:
    sys.exit("passwords.yaml del paquete: falta asset_id")
if re.search(r"password|secret|token|credential|access_?key", " ".join(map(str, doc)), re.IGNORECASE):
    sys.exit("passwords.yaml del paquete: no puede llevar credenciales")
if doc.get("export_path", "/run/kit/contrasenas/usuarios.json") != "/run/kit/contrasenas/usuarios.json":
    sys.exit("passwords.yaml del paquete: export_path debe ser /run/kit/contrasenas/usuarios.json")
'
}

# Contenedor aislado en la red de Vaultwarden (interna), sin logs ni privilegios.
contrasenas_isolated() {
  docker run --rm --log-driver none --network "${PROJECT}_contrasenas-net" --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user "$APP_UID:$APP_UID" "$@"
}

# Estado del panel: `enabled` o `disabled`. Cerrado y abierto responden 200; solo el cuerpo los distingue
# («The admin panel is disabled...»). Falla si Vaultwarden no responde.
vaultwarden_admin_state() {
  contrasenas_isolated "$(python_image)" python -I -c '
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://vaultwarden:80/admin", timeout=10) as response:
    body = response.read(8192).decode("utf-8", "replace")
print("disabled" if "The admin panel is disabled" in body else "enabled")
' 2>/dev/null
}

# ¿Tiene el contenedor de Vaultwarden la variable ADMIN_TOKEN? Falla si no hay contenedor.
vaultwarden_has_admin_token() {
  container=$(dc ps -q vaultwarden 2>/dev/null || true)
  [ -n "$container" ] || return 2
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" | grep -q '^ADMIN_TOKEN='
}

# Argon2id (PHC, preajuste OWASP de Vaultwarden) del token de stdin. En un contenedor sin red, sin
# volúmenes ni entorno: el token no toca el disco. $1 = imagen de la app.
contrasenas_admin_hash() {
  docker run --rm -i --log-driver none --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user "$APP_UID:$APP_UID" "$1" python -I -c '
import os
import sys

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

token = sys.stdin.readline().rstrip("\n").encode()
kdf = Argon2id(salt=os.urandom(16), length=32, iterations=2, lanes=1, memory_cost=19456)
print(kdf.derive_phc_encoded(token))
'
}

kit_contrasenas_enable() {
  [ $# -eq 1 ] || usage
  package=$1
  [ -f "$package" ] && [ ! -L "$package" ] || die "$package no es un fichero normal."
  [ "$(wc -c <"$package")" -le 1048576 ] || die "$package pesa más de 1 MiB; no parece un paquete de contraseñas."
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  grep -q '^  vaultwarden:' "$REL/compose.yaml" ||
    die "La release $current no define el perfil contrasenas; actualiza la caja a una versión que lo incluya."
  [ -n "$CONTRASENAS_HOST" ] ||
    die "Define ENS_CONTRASENAS_HOST en $UI_ENV_FILE (p. ej. contrasenas.example.org) antes de activar las contraseñas."

  # Todo lo que puede fallar sin tocar nada, antes de instalar.
  new_work_dir
  extract_contrasenas_package "$package" "$WORK_DIR/pkg"
  pkg="$WORK_DIR/pkg/contrasenas"
  verify_contrasenas_package "$pkg"
  validate_contrasenas_fragment <"$pkg/passwords.yaml"
  # El volumen de Vaultwarden lo crea y lo posee Vaultwarden (root del contenedor); estos dos son del exportador.
  prepare_volume contrasenas-export "$EXPORT_UID"
  prepare_volume contrasenas-snapshot "$EXPORT_UID"

  [ ! -L "$CONTRASENAS_DIR" ] || die "$CONTRASENAS_DIR es un enlace simbólico; debe ser un directorio."
  mkdir -p "$CONTRASENAS_DIR"
  chmod 700 "$ROOT/deploy/kit" "$CONTRASENAS_DIR"
  (umask 022 && cp "$pkg/MANIFEST" "$CONTRASENAS_DIR/MANIFEST.new")
  mv "$CONTRASENAS_DIR/MANIFEST.new" "$CONTRASENAS_MANIFEST"
  rm -f "$CONTRASENAS_MANIFEST.disabled"
  installed=$(install_connector_fragment passwords <"$pkg/passwords.yaml") ||
    die "No se pudo escribir connectors/passwords.yaml en el workspace."
  cleanup
  [ "$installed" != conservado ] || warn "connectors/passwords.yaml ya existía y difiere del paquete: se conserva el actual."

  info "Arrancando Vaultwarden y publicando $CONTRASENAS_HOST..."
  dc up -d --no-build --no-deps vaultwarden
  wait_healthy vaultwarden || die "Vaultwarden no está sano; revisa: docker compose -p $PROJECT logs vaultwarden"
  dc up -d --no-build --no-deps contrasenas-export caddy
  wait_running contrasenas-export || die "El exportador no arranca; revisa: docker compose -p $PROJECT logs contrasenas-export"
  wait_healthy caddy || die "caddy no está sano; revisa: docker compose -p $PROJECT logs caddy"
  wait_export || warn "Aún no hay usuarios.json (el exportador reintenta cada 15 min); revisa: docker compose -p $PROJECT logs contrasenas-export"
  info "Contraseñas activadas. Siguiente paso: deploy/box.sh kit contrasenas alta (docs/runbooks/kit-contrasenas.md)."
}

# Espera hasta 60 s a que el exportador publique su primer usuarios.json.
wait_export() {
  waited=0
  while [ "$waited" -lt 60 ]; do
    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
      --user "$APP_UID:$APP_UID" -v "${PROJECT}_contrasenas-export:/e:ro" "$(python_image)" \
      test -s /e/usuarios.json 2>/dev/null && return 0
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# Abre el alta: ADMIN_TOKEN temporal (Argon2id) en deploy/kit/contrasenas/admin.env, modo 600, que
# entra en la release fijada con el override alta.yaml. El token en claro se muestra una vez, al final;
# en disco solo queda el hash. Un alta abierta no se pisa ni se repite: primero cerrar-alta.
kit_contrasenas_alta() {
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  contrasenas_enabled || die "Las contraseñas no están activadas; usa kit enable contrasenas <paquete>."
  if [ -e "$CONTRASENAS_ALTA" ] || [ -L "$CONTRASENAS_ALTA" ]; then
    die "Ya hay un alta abierta ($CONTRASENAS_ALTA). El token solo se mostró al abrirla; si no lo tienes, ejecuta kit contrasenas cerrar-alta y repite alta."
  fi
  [ ! -L "$CONTRASENAS_DIR" ] && [ ! -L "$CONTRASENAS_ADMIN_ENV" ] || die "Enlaces simbólicos en $CONTRASENAS_DIR; no se continúa."
  case $ROOT in *[\"\\]*) die "La ruta $ROOT lleva comillas o barras invertidas; no se puede escribir el override." ;; esac
  image=$(app_image_checked)
  token=$(head -c 48 /dev/urandom | base64 | tr -d '=+/\n' | cut -c1-32)
  [ "${#token}" -eq 32 ] || die "No se pudo generar el token del alta."
  phc=$(printf '%s\n' "$token" | contrasenas_admin_hash "$image") || phc=
  # shellcheck disable=SC2016 # el prefijo PHC lleva `$` literales
  case $phc in '$argon2id$'*) ;; *) die "No se pudo calcular el hash Argon2id del token." ;; esac

  # admin.env primero y alta.yaml después: sin override el token no entra en ningún contenedor.
  chmod 700 "$ROOT/deploy/kit" "$CONTRASENAS_DIR"
  (umask 077 && printf "ADMIN_TOKEN='%s'\n" "$phc" >"$CONTRASENAS_ADMIN_ENV.new")
  chmod 600 "$CONTRASENAS_ADMIN_ENV.new"
  mv "$CONTRASENAS_ADMIN_ENV.new" "$CONTRASENAS_ADMIN_ENV"
  (umask 077 && alta_override_text >"$CONTRASENAS_ALTA.new")
  mv "$CONTRASENAS_ALTA.new" "$CONTRASENAS_ALTA"

  info "Recreando Vaultwarden con el panel de administración..."
  dc up -d --no-build --no-deps --force-recreate vaultwarden ||
    die "No se pudo recrear Vaultwarden. El alta queda a medias y no se mostró ningún token: ejecuta kit contrasenas cerrar-alta y repite alta."
  wait_healthy vaultwarden ||
    die "Vaultwarden no está sano con el alta abierta. No se mostró ningún token: ejecuta kit contrasenas cerrar-alta y revisa: docker compose -p $PROJECT logs vaultwarden"
  state_now=$(vaultwarden_admin_state) || state_now=
  [ "$state_now" = enabled ] ||
    die "El panel /admin no quedó activo (estado: ${state_now:-desconocido}). No se mostró ningún token: ejecuta kit contrasenas cerrar-alta y repite alta."
  info "Alta abierta. Token del panel (se muestra UNA vez; en disco solo queda su hash):"
  info ""
  info "  $token"
  info ""
  info "Entra en https://$CONTRASENAS_HOST/admin, invita al primer propietario por su email y, cuando se haya registrado:"
  info "  deploy/box.sh kit contrasenas cerrar-alta"
}

# Cierra el alta: borra admin.env y el override, recrea Vaultwarden y comprueba las dos cosas: que el
# contenedor ya no tiene ADMIN_TOKEN y que /admin responde «panel desactivado». Se puede repetir.
kit_contrasenas_cerrar_alta() {
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  contrasenas_enabled || die "Las contraseñas no están activadas; usa kit enable contrasenas <paquete>."
  # Antes de tocar Docker: `dc` rechazaría un override corrupto y el alta no se podría cerrar.
  rm -f "$CONTRASENAS_ADMIN_ENV" "$CONTRASENAS_ALTA" "$CONTRASENAS_ADMIN_ENV.new" "$CONTRASENAS_ALTA.new"
  info "Recreando Vaultwarden sin el panel de administración..."
  dc up -d --no-build --no-deps --force-recreate vaultwarden ||
    die "NO se ha cerrado el alta: no se pudo recrear Vaultwarden. Puede conservar ADMIN_TOKEN; repite kit contrasenas cerrar-alta."
  wait_healthy vaultwarden ||
    die "NO se ha confirmado el cierre del alta: Vaultwarden no está sano. Revisa los logs y repite kit contrasenas cerrar-alta."
  if vaultwarden_has_admin_token; then
    die "NO se ha cerrado el alta: el contenedor de Vaultwarden sigue con ADMIN_TOKEN. Repite kit contrasenas cerrar-alta."
  fi
  state_now=$(vaultwarden_admin_state) || state_now=
  [ "$state_now" = disabled ] ||
    die "NO se ha confirmado el cierre del alta: /admin no responde «panel desactivado» (estado: ${state_now:-desconocido}). Repite kit contrasenas cerrar-alta."
  info "Alta cerrada: el contenedor no tiene ADMIN_TOKEN y /admin responde «panel desactivado»."
}

# Para y quita los contenedores. No borra volúmenes (cofres cifrados, export, instantánea) ni
# deploy/kit/contrasenas salvo el alta: el MANIFEST se renombra para que `dc` deje de activar el perfil.
kit_contrasenas_disable() {
  [ $# -eq 0 ] || usage
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  [ -e "$CONTRASENAS_MANIFEST" ] || die "Las contraseñas no están activadas (falta $CONTRASENAS_MANIFEST)."
  for service in contrasenas-export vaultwarden; do
    for container in $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
      --filter "label=com.docker.compose.service=$service"); do
      docker stop "$container" >/dev/null
      docker rm "$container" >/dev/null
    done
  done
  rm -f "$CONTRASENAS_ADMIN_ENV" "$CONTRASENAS_ALTA"
  mv "$CONTRASENAS_MANIFEST" "$CONTRASENAS_MANIFEST.disabled"
  info "Vaultwarden y el exportador parados y desactivados (un alta abierta queda cerrada en disco). No se ha borrado nada:"
  info "  - volúmenes ${PROJECT}_vaultwarden-data (cuentas y cofres cifrados), ${PROJECT}_contrasenas-export y ${PROJECT}_contrasenas-snapshot,"
  info "  - connectors/passwords.yaml del workspace."
  info "La ruta $CONTRASENAS_HOST sigue en Caddy y responde 503 mientras no haya Vaultwarden."
}

cmd_kit() {
  [ $# -ge 2 ] || usage
  case "$1 $2" in
    "copias reset-password")
      shift 2
      kit_copias_reset_password "$@"
      return
      ;;
    "claves init")
      shift 2
      kit_claves_init "$@"
      return
      ;;
    "claves unseal")
      shift 2
      kit_claves_unseal "$@"
      return
      ;;
    "contrasenas alta")
      shift 2
      kit_contrasenas_alta "$@"
      return
      ;;
    "contrasenas cerrar-alta")
      shift 2
      kit_contrasenas_cerrar_alta "$@"
      return
      ;;
  esac
  action=$1
  tool=$2
  shift 2
  case $tool in
    copias)
      case $action in
        enable) kit_copias_enable "$@" ;;
        disable) kit_copias_disable "$@" ;;
        *) usage ;;
      esac
      ;;
    claves)
      case $action in
        enable) kit_claves_enable "$@" ;;
        disable) kit_claves_disable "$@" ;;
        *) usage ;;
      esac
      ;;
    contrasenas)
      case $action in
        enable) kit_contrasenas_enable "$@" ;;
        disable) kit_contrasenas_disable "$@" ;;
        *) usage ;;
      esac
      ;;
    *) die "Herramienta del kit desconocida: $tool (copias, claves o contrasenas)." ;;
  esac
}

[ $# -ge 1 ] || usage
command=$1
shift
case "$command" in
  install) cmd_install "$@" ;;
  update) cmd_update "$@" ;;
  rollback) cmd_rollback "$@" ;;
  restart) cmd_restart "$@" ;;
  status) cmd_status "$@" ;;
  kit) cmd_kit "$@" ;;
  *) usage ;;
esac
