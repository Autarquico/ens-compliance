#!/bin/sh
# Opera la caja en Docker Compose (ADR 0016): install, update, rollback, status.
#
#   deploy/box.sh install <vX.Y.Z>
#   deploy/box.sh update [--no-backup] <vX.Y.Z>
#   deploy/box.sh rollback
#   deploy/box.sh status
#
# Lo ejecuta una persona con Docker en la VM, desde un clon del repositorio. La imagen se
# construye con `git archive` de la etiqueta: no se ejecuta nada del árbol de trabajo.
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

info() { printf '%s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
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
  [ -e "$1" ] || return 0
  [ ! -L "$1" ] || die "$1 es un enlace simbólico; debe ser un fichero normal."
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

# ENS_VERSION es obligatoria en compose.yaml; las operaciones sin versión usan un marcador.
dc() {
  if [ -e "$TUNNEL_ENV_FILE" ]; then
    docker compose -p "$PROJECT" -f "$ROOT/compose.yaml" --profile tunnel "$@"
  else
    docker compose -p "$PROJECT" -f "$ROOT/compose.yaml" "$@"
  fi
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

state_get() { state get "$1" | tr -d '\r'; }

# Espera a que un servicio esté healthy. 0 sano; 1 no sano o sin arrancar.
wait_healthy() {
  waited=0
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    container=$(dc ps -q "$1" 2>/dev/null || true)
    if [ -n "$container" ]; then
      case "$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container")" in
        "running healthy") return 0 ;;
        "running unhealthy" | exited* | dead* ) return 1 ;;
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

# --- construcción --------------------------------------------------------------------------

build_image() {
  tag=$1
  git -C "$ROOT" rev-parse --verify --quiet "refs/tags/$tag^{commit}" >/dev/null ||
    die "La etiqueta $tag no existe en $ROOT (¿git fetch --tags?)."
  COMMIT=$(git -C "$ROOT" rev-parse "refs/tags/$tag^{commit}")
  WORK_DIR=$(mktemp -d)
  git -C "$ROOT" archive --format=tar "$tag" | tar -x -C "$WORK_DIR"
  info "Construyendo $(image_ref "$tag") desde $tag ($COMMIT)..."
  docker build --label "org.opencontainers.image.revision=$COMMIT" \
    --label "org.opencontainers.image.version=$tag" -t "$(image_ref "$tag")" "$WORK_DIR"
  rm -rf "$WORK_DIR"
  WORK_DIR=
}

# Argumentos clave=valor del registro de un despliegue.
record_args() {
  printf 'tag=%s commit=%s image_id=%s compose_sha256=%s caddyfile_sha256=%s backup=%s at=%s' \
    "$1" "$COMMIT" "$(image_id "$1")" "$(sha256_of "$ROOT/compose.yaml")" \
    "$(sha256_of "$ROOT/deploy/Caddyfile")" "${2:-none}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
  ensure_env_file
  export ENS_VERSION="$tag"

  build_image "$tag"

  # La primera vez que se monta el volumen lo crea la imagen de la app con el propietario 10001.
  in_app sh -c 'test -w /data' ||
    die "El volumen $VOLUME no es escribible por el UID $APP_UID. No lo toques desde el host: elimínalo a mano solo si está vacío."
  if [ -n "$(state_get current.tag)" ]; then
    die "Ya hay una caja instalada ($(state_get current.tag)); usa update."
  fi
  in_app ens-evidence init --path /data/workspace

  info "Ejecutando el primer ciclo (antes de arrancar el scheduler)..."
  code=0
  in_app ens-evidence report daily --cycle || code=$?
  case "$code" in
    0) ;;
    3) warn "El informe se escribió con conectores en ERROR (normal sin credenciales)." ;;
    *) die "El primer ciclo falló (código $code)." ;;
  esac
  # shellcheck disable=SC2016 # el glob lo expande el sh del contenedor, no este script
  if in_app sh -c 'grep -Eqs "\"backup_code\": \"(not_configured|disabled)\"" /data/workspace/evidence/reports/cycles/*.backup_*.json'; then
    warn "La copia a R2 aún no está configurada; configúrala en deploy/.env (ver runbook)."
  fi

  dc up -d --no-build
  verify_running || die "La caja no está sana; revisa: docker compose -p $PROJECT logs app"
  # shellcheck disable=SC2046 # record_args emite clave=valor sin espacios
  state set current $(record_args "$tag" none)
  info "Caja instalada: $tag"
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
  export ENS_VERSION="$current"

  backup=not_configured
  configured=$(in_app python -I -c "
import yaml
s = yaml.safe_load(open('/data/settings.yaml', encoding='utf-8')) or {}
print(1 if (s.get('backup') or {}).get('restic_repository') else 0)" | tr -d '\r')
  if [ "$skip_backup" = 1 ]; then
    backup=skipped_by_flag
    warn "Se omite la copia previa (--no-backup); queda registrado."
  elif [ "$configured" = 1 ]; then
    info "Copia previa (backup run)..."
    in_app ens-evidence backup run || die "La copia previa falló: no se actualiza. Corrígela o usa --no-backup."
    backup="done"
  else
    warn "La copia no está configurada: se actualiza sin copia previa."
  fi

  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  # shellcheck disable=SC2016 # $1 y $d los expande el sh del contenedor
  helper sh -c 'umask 077; d=/data/state/pre-update-$1; mkdir -p "$d"
    cp -p /data/settings.yaml "$d"/ 2>/dev/null || true
    cp -p /data/state/box-deploy.json "$d"/ 2>/dev/null || true' sh "$stamp"

  build_image "$tag"
  # shellcheck disable=SC2046 # record_args emite clave=valor sin espacios
  state set attempt $(record_args "$tag" "$backup")
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
  if [ -n "$(state_get attempt.tag)" ]; then
    target=$(state_get current.tag)
    wanted_id=$(state_get current.image_id)
    recorded_compose=$(state_get current.compose_sha256)
  else
    target=$(state_get previous.tag)
    wanted_id=$(state_get previous.image_id)
    recorded_compose=$(state_get previous.compose_sha256)
  fi
  [ -n "$target" ] || die "No hay versión anterior registrada a la que volver."
  [ "$(image_id "$target")" = "$wanted_id" ] ||
    die "La imagen local $(image_ref "$target") no es la registrada; no se revierte sola."
  if [ "$recorded_compose" != "$(sha256_of "$ROOT/compose.yaml")" ]; then
    warn "compose.yaml del clon difiere del usado con $target; haz git checkout $target para igualarlo."
  fi
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
  ENS_VERSION=$(state_get current.tag)
  export ENS_VERSION="${ENS_VERSION:-none}"
  print_status
  if [ -n "$(dc ps -q app 2>/dev/null)" ]; then
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
