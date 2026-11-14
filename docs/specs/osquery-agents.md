---
codename: ens-compliance
title: Despliegue de agentes osquery
type: task
status: active
priority: P1
size: L
issue: 9
prs: [81]
related_issues: [9]
depends_on: [osquery-server]
supersedes: []
related_specs: [collector-osquery, offline-bundle]
created: 2026-10-15
updated: 2026-11-13
---

# Despliegue de agentes osquery

## Contexto / problema

`osquery-server` (#28) da la API remota de osquery en `/agent/*` y el alta por equipo (`agent admit`). Falta que un equipo de la pyme (macOS, Linux o Windows) quede con osqueryd instalado, apuntando a la caja y entregando resultados, sin que nadie tenga que escribir flags a mano. El runbook `agentes-osquery.md` documenta hoy los flags de macOS y Linux; Windows no está soportado: el servidor solo admite `darwin` y `linux` (`KNOWN_PLATFORMS`) y el paquete de consultas no tiene consultas de Windows.

Un agente que entrega resultados no cumple nada por sí solo (regla 1): los checks son de `collector-osquery`.

## Scope

**Entra:**
- `ens-evidence agent install --asset <id> --os macos|linux|windows --server <host>` (en Linux, además `--package deb|rpm` y `--arch amd64|arm64`): imprime el script de instalación de ese equipo para su SO (paquete oficial de osquery con versión y sha256 fijados, fichero de flags y servicio). El token de alta no va en la salida: `agent admit` lo sigue emitiendo aparte, se lleva al equipo en un fichero y el script lo instala desde esa ruta.
- Fichero de flags por SO y versiones fijadas, versionados como recursos del paquete Python (la imagen de la caja no lleva `deploy/`), con los endpoints de `/agent/*`, `--host_identifier=uuid` y la política de cortes (`--buffered_log_max`, `--logger_tls_period`, `--logger_tls_backoff_max`, `--config_refresh`); sin `--logger_tls_compress` ni `--tls_secret_always`.
- Token de alta en un fichero legible solo por el administrador del equipo: `/etc/osquery/enroll_secret` en macOS y Linux (modo 600, root), `C:\Program Files\osquery\enroll_secret` en Windows (ACL de SYSTEM y Administradores).
- Windows en el servidor: `windows` en las plataformas admitidas, y consultas de Windows en el paquete para identidad, versión del SO y de osquery, cifrado de disco, cortafuegos, actualizaciones automáticas y software instalado.
- Runbook `agentes-osquery.md` por SO: instalar, comprobar, actualizar osquery, desinstalar.
- Integración automatizada: un contenedor Linux instala el paquete oficial fijado y entrega a un servidor real.
- Pilotos de aceptación: un Mac y un Linux reales y Windows en máquina virtual, con evidencia de cada uno. Un piloto no ejecutado deja su escenario pendiente.

**No entra:**
- Checks sobre los resultados, incluidos los de Windows: `collector-osquery`. Hasta entonces, un equipo Windows sin evidencia del script guiado da FAIL en HOST-001 y UNKNOWN en HOST-002…007; nunca PASS.
- Paquetes de agente preconfigurados y firmados por SO: `offline-bundle` (v0.3).
- Gestión remota del equipo (MDM, despliegue masivo).
- La UI que genera el comando: la aporta `web-ui` sobre `agent install`.

## Criterios de aceptación

### Scenario: piloto por SO
- **Given** un equipo macOS, Linux o Windows inscrito en `connectors/endpoint.yaml` y un token de `agent admit`
- **When** se siguen los pasos de `agent install` para su SO y pasa un intervalo
- **Then** `agent hosts` lo muestra con su último contacto, y sus snapshots llegan al storage de la caja con las consultas de su plataforma

### Scenario: el token no queda a la vista
- **Given** los pasos de `agent install`
- **When** se ejecutan
- **Then** el token solo está en el fichero de alta, creado con permisos de administrador antes de escribirlo; no aparece en la salida de `agent install` ni del script, en argumentos de procesos ni en el fichero de flags

### Scenario: caja caída
- **Given** un equipo dado de alta y la caja inaccesible durante varios intervalos
- **When** la caja vuelve
- **Then** el equipo no se ha degradado (reintentos con espera creciente, buffer acotado), y al volver entrega y el snapshot actual queda guardado; los atrasos se guardan dentro de la cota del servidor (la evidencia es el estado del equipo, no cada intervalo)

### Scenario: paquete alterado
- **Given** un paquete de osquery cuyo sha256 no coincide con el fijado
- **When** se siguen los pasos de instalación
- **Then** la instalación se detiene antes de invocar el instalador, y lo mismo ante una descarga truncada o un fallo de cualquier paso

### Scenario: Windows sin checks
- **Given** un equipo Windows que entrega resultados por el agente y no tiene evidencia del script guiado
- **When** corre el ciclo de checks
- **Then** HOST-001 (cobertura) da FAIL por falta de evidencia y HOST-002…007 dan UNKNOWN; ninguno PASS

## Notas técnicas / riesgos

- El alta es por equipo (`agent admit`, token con caducidad); ya no hay `enroll_secret` compartido. Nada de esto va en Git.
- Tablas de Windows previstas: `bitlocker_info`, `windows_security_center`, `programs`, además de `system_info`, `os_version` y `osquery_info`. Sin verificar contra la versión de osquery fijada; se marcan `verified: false` hasta contrastarlas, como el resto del paquete.
- El paquete de consultas pasa a `v2`: cambia su sha256, así que un paquete de script recogido con `v1` y aún sin importar se rechaza y hay que repetir la recogida con el script nuevo. Las evidencias ya guardadas siguen legibles.
- Semántica de los flags (documentación de osquery 5.24.1): `--buffered_log_max` cuenta registros y purga los más antiguos al llenarse; `--logger_tls_backoff_max` limita la espera entre reintentos y debe ser múltiplo de `--logger_tls_period`. La integración mide la recuperación tras un corte.
- La cota del servidor (un snapshot por consulta y equipo por intervalo, por hora de generación) ya contempla las entregas atrasadas tras un corte.
- El tamaño sube a L: tres SO y consultas nuevas de Windows.

## Tasks (no autoritativo — el board manda)

- [ ] `agent install` y ficheros de flags por SO
- [ ] Windows en el servidor y en el paquete de consultas
- [ ] Runbook por SO
- [ ] Integración Linux automatizada
- [ ] Pilotos macOS, Linux y Windows (VM)

## Referencias

- https://osquery.readthedocs.io/
- docs/specs/osquery-server.md, docs/runbooks/agentes-osquery.md, docs/decisions/0020-servidor-http-y-agentes.md
