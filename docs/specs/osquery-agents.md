---
codename: ens-compliance
title: Despliegue de agentes osquery
type: task
status: active
priority: P1
size: M
issue: 9
prs: []
related_issues: [9]
depends_on: [osquery-server]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Despliegue de agentes osquery

## Contexto / problema

Instalación de osqueryd en macOS y Linux apuntando al servidor osquery de la caja.

## Scope

**Entra:**
- Instalación reproducible por SO, versionada en `infrastructure/`.
- Flags TLS hacia `/agent/*` de la caja.
- Comando de instalación generado por la caja.
- Piloto y despliegue general.

**No entra:**
- Checks: `collector-osquery`.

## Criterios de aceptación

### Scenario: piloto
- **Given** el Mac con el agente
- **When** pasa un intervalo
- **Then** sus resultados llegan al storage de la caja

### Scenario: caja caída
- **Given** la caja inaccesible
- **When** el agente sigue
- **Then** el host no se degrada y reenvía al volver

## Notas técnicas / riesgos

- El enroll_secret no va en Git.
- Tamaño revisado a M: dos SO y recuperación tras caída.

## Tasks (no autoritativo — el board manda)

- [ ] Paquetes y flags
- [ ] macOS
- [ ] Linux
- [ ] Piloto

## Referencias

- https://osquery.readthedocs.io/
