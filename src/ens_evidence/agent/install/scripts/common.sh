die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

need() {
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die "falta la herramienta $tool; no se ha instalado nada."
  done
}

[ "$(id -u)" = 0 ] || die "ejecuta este script como root."
# shellcheck disable=SC2086
need $REQUIRED_TOOLS

TOKEN_FILE=@@TOKEN_FILE@@
FILENAME=@@FILENAME@@
URL=@@URL@@
EXPECTED_SHA256=@@SHA256@@

[ ! -L "$TOKEN_FILE" ] || die "--token-file no puede ser un enlace simbólico."
[ -f "$TOKEN_FILE" ] || die "--token-file no es un fichero normal."
[ -s "$TOKEN_FILE" ] || die "--token-file está vacío."

umask 077
WORKDIR=$(mktemp -d) || die "no se pudo crear el directorio temporal."
trap 'rm -rf "$WORKDIR"' EXIT
PACKAGE_FILE="$WORKDIR/$FILENAME"

curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --tlsv1.2 --output "$PACKAGE_FILE" "$URL" || die "la descarga de $FILENAME ha fallado."
ACTUAL_SHA256=$(sha256_of "$PACKAGE_FILE") || die "no se pudo calcular el sha256."
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] ||
  die "el sha256 de $FILENAME no coincide con el esperado; no se instala."

# Tras instalar: el servicio queda parado hasta tener flags y token.
write_configuration() {
  umask 022
  mkdir -p "$CONFIG_DIR" "$(dirname "$FLAGS_FILE")"
  cat >"$FLAGS_FILE" <<'ENS_FLAGS_END'
@@FLAGS@@
ENS_FLAGS_END
  umask 077
  [ ! -L "$ENROLL_SECRET" ] || die "$ENROLL_SECRET es un enlace simbólico."
  : >"$ENROLL_SECRET"
  chmod 600 "$ENROLL_SECRET"
  chown root:"$ROOT_GROUP" "$ENROLL_SECRET"
  tr -d '\r\n' <"$TOKEN_FILE" >"$ENROLL_SECRET"
  [ -s "$ENROLL_SECRET" ] || die "no se pudo copiar el token."
}
