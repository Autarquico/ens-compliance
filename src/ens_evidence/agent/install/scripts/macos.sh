#!/bin/sh
# ens-evidence agent install: osquery @@VERSION@@ (pkg). Ejecutar como root.
set -eu
# El pkg oficial no instala ningún LaunchDaemon: se copia el plist que trae, ya con flags y token.
CONFIG_DIR=/etc/osquery
FLAGS_FILE=/private/var/osquery/osquery.flags
ENROLL_SECRET=/etc/osquery/enroll_secret
ROOT_GROUP=wheel
PLIST_SOURCE=/private/var/osquery/io.osquery.agent.plist
PLIST=/Library/LaunchDaemons/io.osquery.agent.plist
REQUIRED_TOOLS="id curl shasum cut mktemp rm tr cat chmod chown mkdir dirname find cp installer launchctl"

sha256_of() {
  shasum -a 256 "$1" | cut -d ' ' -f 1
}

@@COMMON@@

installer -pkg "$PACKAGE_FILE" -target /
if launchctl print system/io.osquery.agent >/dev/null 2>&1; then
  launchctl bootout system/io.osquery.agent
fi
[ -f "$PLIST_SOURCE" ] || die "el pkg no trae $PLIST_SOURCE; el servicio no se arranca."
write_configuration
cp "$PLIST_SOURCE" "$PLIST"
chown root:wheel "$PLIST"
chmod 644 "$PLIST"
launchctl bootstrap system "$PLIST"
printf 'osquery @@VERSION@@ instalado y arrancado. Retira el token de %s cuando el equipo aparezca en agent hosts.\n' "$TOKEN_FILE"
