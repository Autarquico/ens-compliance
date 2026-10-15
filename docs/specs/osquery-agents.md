---
codename: ens-compliance
title: Despliegue de agentes osquery
type: task
status: active
priority: P1
size: S
issue: null
prs: []
related_issues: [9]
depends_on: [osquery-edge]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Despliegue de agentes osquery

## Contexto / problema

Instalación y configuración de osqueryd en macOS y Linux apuntando a `osquery-edge`.

## Scope

**Entra:**
- Instalación reproducible (Ansible para Linux, script o perfil para macOS) versionada en `infrastructure/`.
- Flags TLS: hostname, enroll_secret, plugins tls para config, logger y distributed.
- Piloto en el Mac del usuario y luego el resto de hosts.

**No entra:**
- Checks: son `collector-osquery`.

## Criterios de aceptación

### Scenario: piloto
- **Given** el Mac del usuario con el agente instalado
- **When** pasa un intervalo de agenda
- **Then** sus resultados aparecen en R2 vía osquery-edge

### Scenario: servidor caído
- **Given** osquery-edge inaccesible
- **When** el agente sigue en marcha
- **Then** no se degrada el host y reenvía al volver el servicio

## Notas técnicas / riesgos

- El enroll_secret no se guarda en Git; se distribuye desde Bitwarden.

## Tasks (no autoritativo — el board manda)

- [ ] Paquetes y flags
- [ ] Ansible Linux
- [ ] Instalación macOS
- [ ] Piloto
- [ ] Despliegue general

## Referencias

- https://osquery.readthedocs.io/
