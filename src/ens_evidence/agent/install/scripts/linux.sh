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

WITH_ANTIMALWARE=@@WITH_ANTIMALWARE@@

# Antes de tocar nada: se prevalidan las herramientas del gestor de paquetes (`need`, en el bloque común)
# y ClamAV solo se instala con el gestor de la distribución (deb). En RPM debe estar ya instalado a mano.
INSTALL_CLAMAV=0
case "$PACKAGE" in
  deb) REQUIRED_TOOLS="$REQUIRED_TOOLS dpkg" ;;
  rpm) REQUIRED_TOOLS="$REQUIRED_TOOLS rpm" ;;
esac
if [ "$WITH_ANTIMALWARE" = 1 ]; then
  case "$PACKAGE" in
    deb)
      INSTALL_CLAMAV=1
      REQUIRED_TOOLS="$REQUIRED_TOOLS apt-get" ;;
    *)
      if ! command -v clamscan >/dev/null 2>&1 || ! command -v freshclam >/dev/null 2>&1; then
        printf 'Error: este equipo es un servidor y necesita ClamAV, pero el instalador no añade repositorios (EPEL). Instálalo a mano: habilita EPEL, instala clamav y clamav-update, activa la actualización de firmas y vuelve a ejecutar este script (continuará sin tocar ClamAV). No se ha instalado nada.\n' >&2
        exit 1
      fi ;;
  esac
fi

@@COMMON@@

# Desde aquí hay cambios en el equipo: un fallo deja ClamAV instalado y osquery sin instalar.
partial() {
  [ "$INSTALL_CLAMAV" = 1 ] || die "$1"
  die "$1 ClamAV ya está instalado y osquery no; corrige la causa y vuelve a ejecutar el script (ver «Estado parcial» en el runbook)."
}

if [ "$INSTALL_CLAMAV" = 1 ]; then
  DEBIAN_FRONTEND=noninteractive apt-get update || die "no se pudo actualizar el índice de paquetes; no se ha instalado nada."
  DEBIAN_FRONTEND=noninteractive apt-get install -y clamav clamav-freshclam || die "no se pudo instalar ClamAV; puede haber quedado instalado a medias (ver «Estado parcial» en el runbook)."
  systemctl enable --now clamav-freshclam || partial "no se pudo activar clamav-freshclam."
fi

case "$PACKAGE" in
  deb) dpkg -i "$PACKAGE_FILE" || partial "no se pudo instalar el paquete de osquery." ;;
  rpm) rpm -U --replacepkgs "$PACKAGE_FILE" || partial "no se pudo instalar el paquete de osquery." ;;
  *) die "paquete desconocido: $PACKAGE." ;;
esac

# El paquete puede arrancar osqueryd al instalar (con los flags previos, sin esta configuración): se para antes de escribirla.
# Con set -eu, si el servicio no existe o no se para, el script aborta aquí.
systemctl disable --now osqueryd
write_configuration
systemctl enable --now osqueryd
printf 'osquery @@VERSION@@ instalado y arrancado. Retira el token de %s cuando el equipo aparezca en agent hosts.\n' "$TOKEN_FILE"
