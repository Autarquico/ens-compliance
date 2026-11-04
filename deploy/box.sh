#!/bin/sh
# Opera la caja en Docker Compose (ADR 0016): install, update, rollback, status.
#
#   deploy/box.sh install <vX.Y.Z>
#   deploy/box.sh update [--no-backup] <vX.Y.Z>
#   deploy/box.sh rollback
#   deploy/box.sh status
#
# Lo ejecuta una persona con Docker en la VM, desde un clon del repositorio con la etiqueta
# desplegada (git checkout <tag>). La imagen, compose.yaml y el Caddyfile salen de `git archive`
# del commit al que apunta refs/tags/<tag>, nunca del árbol de trabajo; este script tiene que ser
# idéntico al de la etiqueta. Se guardan en <git-dir>/ens-box/releases/<commit>.
# Nunca borra volúmenes ni imágenes y nunca revierte sola.
#
# ENS_BOX_PROJECT cambia el nombre del proyecto Compose y del volumen (solo para pruebas).

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROJECT=${ENS_BOX_PROJECT:-ens-box}
VOLUME="${PROJECT}_data"
ENV_FILE="$ROOT/deploy/.env"
TUNNEL_ENV_FILE="$ROOT/deploy/tunnel.env"
STATE_FILE=/data/state/box-deploy.json
APP_UID=10001
HEALTH_TIMEOUT=${ENS_BOX_HEALTH_TIMEOUT:-180}
WORK_DIR=
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
trap cleanup EXIT
trap 'exit 130' INT TERM

usage() {
  die "uso: deploy/box.sh install <vX.Y.Z> | update [--no-backup] <vX.Y.Z> | rollback | status"
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

# --- docker --------------------------------------------------------------------------------

# ENS_VERSION es obligatoria en compose.yaml; compose y Caddyfile son los de la release en uso.
dc() {
  [ -n "$REL" ] || die "interno: no hay release seleccionada."
  profiles=
  [ ! -e "$TUNNEL_ENV_FILE" ] || profiles=tunnel
  COMPOSE_PROFILES=$profiles ENS_ENV_FILE=$ENV_FILE ENS_TUNNEL_ENV_FILE=$TUNNEL_ENV_FILE \
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

verify_running() {
  wait_healthy app || return 1
  wait_healthy caddy || return 1
  dc exec -T app ens-evidence scheduler status >/dev/null || return 1
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
  releases="$(cd "$ROOT" && cd "$(git rev-parse --git-dir)" && pwd)/ens-box/releases"
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
  new_work_dir
  git -C "$ROOT" archive --format=tar -o "$WORK_DIR/source.tar" "$COMMIT" ||
    die "No se puede extraer $COMMIT de git."
  mkdir "$WORK_DIR/source"
  tar -x -f "$WORK_DIR/source.tar" -C "$WORK_DIR/source"
  info "Construyendo $(image_ref "$tag") desde $tag ($COMMIT)..."
  docker build --label "org.opencontainers.image.revision=$COMMIT" \
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

  build_image "$tag"

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
  new_release=$REL

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

  COMMIT=$new_commit
  build_image "$tag"
  REL=$new_release
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
  verify_running || die "$target no está sana tras el rollback; revisa los logs."
  state rolled-back
  info "Rollback hecho: $target"
  print_status
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
  if [ -z "$attempt" ] && [ -n "$(dc ps -q app 2>/dev/null)" ]; then
    info ""
    dc exec -T app ens-evidence scheduler status || warn "scheduler status falló."
  fi
}

[ $# -ge 1 ] || usage
command=$1
shift
case "$command" in
  install) cmd_install "$@" ;;
  update) cmd_update "$@" ;;
  rollback) cmd_rollback "$@" ;;
  status) cmd_status "$@" ;;
  *) usage ;;
esac
