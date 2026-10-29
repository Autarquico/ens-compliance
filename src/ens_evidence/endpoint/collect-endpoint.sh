#!/bin/sh
# Recoge evidencia de un endpoint con osqueryi, en solo lectura.
#
# Uso: collect-endpoint.sh [DIRECTORIO_DE_SALIDA]   (por defecto, el actual)
#
# Requiere osqueryi y utilidades POSIX. No usa sudo, ni --config_path, ni
# extensiones, ni eval/source del paquete. Algunas tablas pueden devolver
# datos incompletos sin privilegios: el script lo avisa y NO eleva permisos.
# Sin `timeout`/`gtimeout` no hay límite de tiempo por consulta (se avisa).
# Los hashes detectan corrupción, no falsificación: es una atestación manual.

SCRIPT_VERSION=1
MAX_OUTPUT_KB=10240
QUERY_TIMEOUT=60

umask 077
export LC_ALL=C
export COPYFILE_DISABLE=1

die() {
    printf 'collect-endpoint: %s\n' "$*" >&2
    exit 1
}

sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        die "hace falta shasum o sha256sum"
    fi
}

json_escape() {
    printf '%s' "$1" | tr '\t\n\r' '   ' | tr -cd '\040-\176' | sed 's/\\/\\\\/g; s/"/\\"/g'
}

safe_id() {
    case "$1" in
        '' | [!a-z]* | *[!a-z0-9_]*) return 1 ;;
    esac
}

here=$(cd "$(dirname "$0")" && pwd) || die "no se puede resolver el directorio del script"
out_dir=${1:-$PWD}
[ -d "$out_dir" ] && [ -w "$out_dir" ] || die "directorio de salida no válido: $out_dir"
# Solo se escribe en un directorio del usuario que nadie más pueda alterar:
# propio y sin escritura de grupo/otros, salvo con sticky bit (como /tmp).
dir_mode=$(ls -ld "$out_dir/." | cut -c1-10)
[ -n "$(find "$out_dir/." -prune -user "$(id -u)" 2>/dev/null)" ] ||
    die "el directorio de salida no es del usuario actual: $out_dir"
case "$dir_mode" in
    ?????-??-? | *t) ;;
    *) die "el directorio de salida es escribible por otros sin sticky bit: $out_dir" ;;
esac

command -v osqueryi >/dev/null 2>&1 || die "osqueryi no está en el PATH"
case "$(uname -s)" in
    Darwin) platform=darwin ;;
    Linux) platform=linux ;;
    *) die "plataforma no soportada: $(uname -s)" ;;
esac

if command -v timeout >/dev/null 2>&1; then
    limiter="timeout $QUERY_TIMEOUT"
elif command -v gtimeout >/dev/null 2>&1; then
    limiter="gtimeout $QUERY_TIMEOUT"
else
    limiter=""
    printf 'aviso: sin timeout/gtimeout; las consultas no tienen límite de tiempo\n' >&2
fi

# Verificación del paquete: cada fichero listado en el manifiesto coincide, el
# manifiesto coincide con pack.sha256 y todo lo que se usa está en el manifiesto.
manifest="$here/pack.manifest"
[ -f "$manifest" ] && [ -f "$here/pack.sha256" ] || die "falta pack.manifest o pack.sha256"
read -r pack_sha256 <"$here/pack.sha256"
[ "$(sha256_of "$manifest")" = "$pack_sha256" ] || die "el manifiesto no coincide con pack.sha256"
while read -r expected path; do
    case "$path" in
        pack.yaml | pack.info | queries.list) ;;
        queries/*.sql) safe_id "$(basename "$path" .sql)" || die "ruta no válida en el manifiesto" ;;
        *) die "ruta no válida en el manifiesto" ;;
    esac
    [ "$(sha256_of "$here/$path")" = "$expected" ] || die "el fichero $path no coincide con el manifiesto"
done <"$manifest"

pack_id=$(sed -n 's/^id=//p' "$here/pack.info")
pack_version=$(sed -n 's/^version=//p' "$here/pack.info")
osquery_version=$(osqueryi --version 2>/dev/null | head -n 1) || osquery_version=""

host=$(hostname 2>/dev/null) || host=""
host_name=$(printf '%s' "$host" | tr -cd 'A-Za-z0-9._-' | sed 's/^[.-]*//' | cut -c1-64)
[ -n "$host_name" ] || host_name=host
collected_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
stamp=$(date -u +%Y%m%dT%H%M%SZ)

stage=$(mktemp -d "${TMPDIR:-/tmp}/ens-endpoint.XXXXXX") || die "no se puede crear el directorio temporal"
trap 'rm -rf "$stage"' EXIT
trap 'exit 130' HUP INT TERM
mkdir "$stage/results"

queries_json=""
members="meta.json"
permission_hint=0
while read -r id platforms; do
    safe_id "$id" || die "identificador de consulta no válido en queries.list"
    grep -qF "  queries/$id.sql" "$manifest" || die "la consulta $id no está en el manifiesto"
    case ",$platforms," in
        *",$platform,"*) ;;
        *) continue ;;
    esac

    result="$stage/results/$id.json"
    sql=$(cat "$here/queries/$id.sql")
    # shellcheck disable=SC2086  # $limiter son dos palabras fijas o vacío
    (ulimit -f $((MAX_OUTPUT_KB * 2)) 2>/dev/null; exec $limiter osqueryi --json "$sql") \
        </dev/null >"$result" 2>"$stage/stderr"
    code=$?
    size=$(wc -c <"$result" | tr -d ' ')
    if [ "$code" -eq 0 ] && [ "$size" -gt $((MAX_OUTPUT_KB * 1024)) ]; then
        code=153
    fi
    [ "$code" -eq 0 ] || : >"$result"

    hint=false
    if grep -qiE 'permission|denied|privilege|root' "$stage/stderr"; then
        hint=true
        permission_hint=1
        printf 'aviso: la consulta %s parece requerir privilegios; el script no los eleva\n' "$id" >&2
    fi
    [ "$code" -eq 0 ] || printf 'aviso: la consulta %s terminó con código %s\n' "$id" "$code" >&2

    entry="{\"id\": \"$id\", \"exit_code\": $code, \"permission_hint\": $hint}"
    queries_json="${queries_json:+$queries_json, }$entry"
    members="$members results/$id.json"
done <"$here/queries.list"

cat >"$stage/meta.json" <<META
{
  "script_version": $SCRIPT_VERSION,
  "pack_id": "$(json_escape "$pack_id")",
  "pack_version": "$(json_escape "$pack_version")",
  "pack_sha256": "$(json_escape "$pack_sha256")",
  "osquery_version": "$(json_escape "$osquery_version")",
  "platform": "$platform",
  "hostname": "$(json_escape "$host")",
  "collected_at": "$collected_at",
  "queries": [$queries_json]
}
META

(
    cd "$stage" || exit 1
    : >SHA256SUMS
    for member in $members; do
        printf '%s  %s\n' "$(sha256_of "$member")" "$member" >>SHA256SUMS
    done
) || die "no se pudo calcular SHA256SUMS"

archive="$out_dir/$host_name-$stamp.endpoint.tar.gz"

# Las cabeceras del tar no deben llevar el usuario/grupo del endpoint.
if tar --version 2>/dev/null | grep -qi bsdtar; then
    owner_flags="--uid 0 --gid 0 --uname= --gname="
else
    owner_flags="--owner=0 --group=0 --numeric-owner"
fi
# El tar se crea en el temporal privado; tar no sigue enlaces (sin -h) y solo
# recibe la lista blanca de miembros.
# shellcheck disable=SC2086  # los miembros y las opciones son palabras fijas seguras
tar $owner_flags -czf "$stage/package.tar.gz" -C "$stage" $members SHA256SUMS || die "no se pudo crear el paquete"

# Publicación exclusiva: con noclobber el shell abre el destino con O_EXCL, que
# falla si ya existe cualquier cosa (fichero, enlace, directorio) y nunca sigue
# enlaces. Es POSIX y vale igual en macOS y Linux; `mv`/`ln` tratarían un enlace
# a directorio como carpeta destino o dependerían de que el temporal esté en el
# mismo sistema de ficheros.
if ! (set -C; cat "$stage/package.tar.gz" >"$archive") 2>/dev/null; then
    die "no se pudo crear $archive (¿ya existe?)"
fi

[ "$permission_hint" -eq 0 ] || printf 'aviso: revisa las consultas marcadas con permission_hint en meta.json\n' >&2
printf '%s\n' "$archive"
