#!/bin/sh
# ens-evidence agent install: osquery @@VERSION@@ (@@PACKAGE@@). Ejecutar como root.
set -eu
# Rutas del paquete oficial: tools/deployment/linux_packaging (osqueryd.sysconfig, osqueryd.service).
CONFIG_DIR=/etc/osquery
FLAGS_FILE=/etc/osquery/osquery.flags
ENROLL_SECRET=/etc/osquery/enroll_secret
ROOT_GROUP=root
PACKAGE=@@PACKAGE@@
REQUIRED_TOOLS="id curl sha256sum cut mktemp rm tr cat chmod chown mkdir dirname systemctl"

sha256_of() {
  sha256sum "$1" | cut -d ' ' -f 1
}

@@COMMON@@

case "$PACKAGE" in
  deb) need dpkg && dpkg -i "$PACKAGE_FILE" ;;
  rpm) need rpm && rpm -U --replacepkgs "$PACKAGE_FILE" ;;
  *) die "paquete desconocido: $PACKAGE." ;;
esac

# El paquete puede arrancar osqueryd al instalar (con los flags previos, sin esta configuración): se para antes de escribirla.
# Con set -eu, si el servicio no existe o no se para, el script aborta aquí.
systemctl disable --now osqueryd
write_configuration
systemctl enable --now osqueryd
printf 'osquery @@VERSION@@ instalado y arrancado. Retira el token de %s cuando el equipo aparezca en agent hosts.\n' "$TOKEN_FILE"
