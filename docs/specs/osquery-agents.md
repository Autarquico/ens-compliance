---
codename: ens-compliance
title: Despliegue de agentes osquery
type: task
status: active
priority: P1
size: L
issue: 9
prs: []
related_issues: [9]
depends_on: [osquery-server]
supersedes: []
related_specs: [collector-osquery, offline-bundle]
created: 2026-10-15
updated: 2026-11-12
---

# Despliegue de agentes osquery

## Contexto / problema

`osquery-server` (#28) da la API remota de osquery en `/agent/*` y el alta por equipo (`agent admit`). Falta que un equipo de la pyme (macOS, Linux o Windows) quede con osqueryd instalado, apuntando a la caja y entregando resultados, sin que nadie tenga que escribir flags a mano. El runbook `agentes-osquery.md` documenta hoy los flags de macOS y Linux; Windows no está soportado: el servidor solo admite `darwin` y `linux` (`KNOWN_PLATFORMS`) y el paquete de consultas no tiene consultas de Windows.

Un agente que entrega resultados no cumple nada por sí solo (regla 1): los checks son de `collector-osquery`.

## Scope

**Entra:**
- `ens-evidence agent install --asset <id> --os macos|linux|windows`: imprime los pasos de instalación de ese equipo para su SO (paquete oficial de osquery con versión y sha256 fijados, fichero de flags y servicio). El token de alta no va en la salida: `agent admit` lo sigue emitiendo aparte y los pasos lo leen de un fichero.
- Fichero de flags por SO, versionado en `deploy/agentes/<so>/osquery.flags`, con los endpoints de `/agent/*`, `--host_identifier=uuid` y el buffer de resultados para cortes; sin `--logger_tls_compress` ni `--tls_secret_always`.
- Token de alta en un fichero legible solo por el administrador del equipo: `/etc/osquery/enroll_secret` en macOS y Linux (modo 600, root), `C:\Program Files\osquery\enroll_secret` en Windows (ACL de SYSTEM y Administradores).
- Windows en el servidor: `windows` en las plataformas admitidas, y consultas de Windows en el paquete para identidad, versión del SO y de osquery, cifrado de disco, cortafuegos, actualizaciones automáticas y software instalado.
- Runbook `agentes-osquery.md` por SO: instalar, comprobar, actualizar osquery, desinstalar.
- Piloto: un Mac y un Linux reales; Windows en máquina virtual.

**No entra:**
- Checks sobre los resultados, incluidos los de Windows: `collector-osquery`. Hasta entonces, las medidas que dependan de un equipo Windows quedan en UNKNOWN.
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
- **Then** el token solo está en el fichero de alta con permisos de administrador; no aparece en la salida de `agent install`, en argumentos de procesos ni en el fichero de flags

### Scenario: caja caída
- **Given** un equipo dado de alta y la caja inaccesible durante varios intervalos
- **When** la caja vuelve
- **Then** el equipo no se ha degradado (sin bucles de reintento que consuman CPU), entrega lo acumulado y el snapshot actual queda guardado

### Scenario: paquete alterado
- **Given** un paquete de osquery cuyo sha256 no coincide con el fijado
- **When** se siguen los pasos de instalación
- **Then** la instalación se detiene antes de instalar nada

### Scenario: Windows sin checks
- **Given** un equipo Windows que entrega resultados
- **When** corre el ciclo de checks
- **Then** sus medidas de endpoint quedan en UNKNOWN, nunca en PASS

## Notas técnicas / riesgos

- El alta es por equipo (`agent admit`, token con caducidad); ya no hay `enroll_secret` compartido. Nada de esto va en Git.
- Tablas de Windows previstas: `bitlocker_info`, `windows_security_center`, `programs`, además de `system_info`, `os_version` y `osquery_info`. Sin verificar contra la versión de osquery fijada; se marcan `verified: false` hasta contrastarlas, como el resto del paquete.
- Añadir consultas cambia el sha256 del paquete, que figura en las evidencias. Si se amplía `v1` o se crea `v2` se decide en el plan, con las evidencias ya guardadas legibles en ambos casos.
- El buffer de resultados para cortes (`--buffered_log_max`) y el comportamiento de reintento de osquery: sin verificar; el piloto lo mide.
- La cota del servidor (un snapshot por consulta y equipo por intervalo, por hora de generación) ya contempla las entregas atrasadas tras un corte.
- El tamaño sube a L: tres SO y consultas nuevas de Windows.

## Tasks (no autoritativo — el board manda)

- [ ] `agent install` y ficheros de flags por SO
- [ ] Windows en el servidor y en el paquete de consultas
- [ ] Runbook por SO
- [ ] Piloto macOS, Linux y Windows (VM)

## Referencias

- https://osquery.readthedocs.io/
- docs/specs/osquery-server.md, docs/runbooks/agentes-osquery.md, docs/decisions/0020-servidor-http-y-agentes.md
