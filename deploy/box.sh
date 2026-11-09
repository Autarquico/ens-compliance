#!/bin/sh
# Opera la caja en Docker Compose (ADR 0016): install, update, rollback, status.
#
#   deploy/box.sh install <vX.Y.Z>
#   deploy/box.sh update [--no-backup] <vX.Y.Z>
#   deploy/box.sh rollback
#   deploy/box.sh restart [app|caddy|cloudflared|agent|ui]
#   deploy/box.sh status
#   deploy/box.sh kit enable copias [--replace-config] <paquete.tar.gz>
#   deploy/box.sh kit disable copias
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
COPIAS_DIR="$ROOT/deploy/kit/copias"
COPIAS_OVERRIDE="$COPIAS_DIR/compose.yaml"
BACKREST_UID=10001
STATE_FILE=/data/state/box-deploy.json
APP_UID=10001
HEALTH_TIMEOUT=${ENS_BOX_HEALTH_TIMEOUT:-180}
WORK_DIR=
LOCK_DIR=
REL=
COMMIT=
RELEASE_FILES="compose.yaml deploy/Caddyfile deploy/box.sh"

info() { printf '%s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
  WORK_DIR=
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
  die "uso: deploy/box.sh install <vX.Y.Z> | update [--no-backup] <vX.Y.Z> | rollback | restart [app|caddy|cloudflared|agent|ui|backrest] | status | kit enable copias [--replace-config] <paquete> | kit disable copias"
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
  # Vacíos, compose.yaml pone `.invalid` y Caddy contesta 403 a todo en ese servicio.
  COMPOSE_PROFILES=$profiles ENS_ENV_FILE=$ENV_FILE ENS_TUNNEL_ENV_FILE=$TUNNEL_ENV_FILE \
    ENS_AGENT_ENV_FILE=$AGENT_ENV_FILE ENS_UI_ENV_FILE=$UI_ENV_FILE \
    ENS_UI_HOST=$UI_HOST ENS_AGENT_HOST=$AGENT_HOST ENS_COPIAS_HOST=$COPIAS_HOST \
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
  REL="$releases/$commit"
  if [ ! -d "$REL" ]; then
    new_work_dir "$releases"
    # shellcheck disable=SC2086 # RELEASE_FILES son rutas fijas sin espacios
    git -C "$ROOT" archive --format=tar -o "$WORK_DIR/release.tar" "$commit" $RELEASE_FILES ||
      die "No se puede extraer la release $commit de git."
    mkdir "$WORK_DIR/release"
    tar -x -f "$WORK_DIR/release.tar" -C "$WORK_DIR/release"
    mv "$WORK_DIR/release" "$REL"
    cleanup
  fi
  for file in $RELEASE_FILES; do
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
  if [ "${1:-}" = "--no-backup" ]; then
    skip_backup=1
    shift
  fi
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
    *) usage ;;
  esac
  require_tools
  acquire_lock
  check_secret_files
  require_volume
  use_current_release
  recreate_service "$service"
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
  if [ -z "$attempt" ] && [ -n "$(dc ps -q app 2>/dev/null)" ]; then
    info ""
    dc exec -T app ens-evidence scheduler status || warn "scheduler status falló."
  fi
}

# --- kit de herramientas -------------------------------------------------------------------

# Volumen de Backrest: lo crea con las etiquetas de Compose y lo deja del UID de Backrest. Es el
# único paso con root, sin más capacidad que CHOWN; la imagen scratch no trae directorios propios.
prepare_backrest_volume() {
  volume="${PROJECT}_$1"
  docker volume inspect "$volume" >/dev/null 2>&1 ||
    docker volume create --label "com.docker.compose.project=$PROJECT" \
      --label "com.docker.compose.volume=$1" "$volume" >/dev/null
  # shellcheck disable=SC2016 # $(stat ...) lo expande el sh del contenedor
  docker run --rm --network none --read-only --cap-drop ALL --cap-add CHOWN \
    --security-opt no-new-privileges:true --user 0:0 -v "$volume:/v" "$(python_image)" \
    sh -c '[ "$(stat -c %u /v)" = "$1" ] || { chmod 700 /v && chown "$1:$1" /v; }' sh "$BACKREST_UID" ||
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
  [ -z "$(find "$dest" ! -type f ! -type d)" ] || die "El paquete contiene enlaces u otros tipos de fichero; no se instala."
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
  prepare_backrest_volume backrest-config
  prepare_backrest_volume backrest-data
  has_config=$(in_backrest_config 'test -e /config/config.json && echo 1 || echo 0')
  [ "$has_config" = 0 ] || [ "$replace" = 1 ] ||
    die "Backrest ya tiene configuración (con su usuario). Para sustituirla por la del paquete usa --replace-config (la anterior queda como config.json.prev en su volumen)."

  # La contraseña solo vive en esta variable: va al hash por stdin y se muestra al final, una vez.
  backrest_password=$(head -c 48 /dev/urandom | base64 | tr -d '=+/\n' | cut -c1-32)
  [ "${#backrest_password}" -eq 32 ] || die "No se pudo generar la contraseña de Backrest."
  password_hash=$(printf '%s\n' "$backrest_password" | backrest_password_hash) ||
    die "No se pudo calcular el bcrypt de la contraseña de Backrest."

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
  info "  Borra el paquete $package: lleva las claves de escritura."
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

cmd_kit() {
  [ $# -ge 2 ] || usage
  action=$1
  tool=$2
  shift 2
  [ "$tool" = copias ] || die "Herramienta del kit desconocida: $tool (solo copias)."
  case $action in
    enable) kit_copias_enable "$@" ;;
    disable) kit_copias_disable "$@" ;;
    *) usage ;;
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
