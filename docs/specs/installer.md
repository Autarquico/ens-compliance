---
codename: ens-compliance
title: "Instalador de un comando"
type: task
status: draft
priority: P1
size: M
issue: null
prs: []
related_issues: []
depends_on: [box-packaging, web-ui]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Instalador de un comando

## Contexto / problema

Instalación para terceros: un comando en una VM con Docker y el resto en el asistente web.

## Scope

**Entra:**
- Script de un comando con comprobaciones previas (Docker, recursos, puertos).
- Primer arranque guiado en la UI.
- Actualización y desinstalación documentadas.

**No entra:**
- Bundle offline (v0.3).

## Criterios de aceptación

### Scenario: una hora
- **Given** una VM limpia con Docker
- **When** un usuario apañado sigue la guía
- **Then** la caja queda operativa en menos de 1 hora sin editar ficheros

### Scenario: requisitos insuficientes
- **Given** una VM por debajo del mínimo
- **When** se ejecuta el comando
- **Then** se detiene explicando qué falta

## Notas técnicas / riesgos


## Tasks (no autoritativo — el board manda)

- [ ] Script
- [ ] Comprobaciones
- [ ] Primer arranque
- [ ] Docs

## Referencias

- docs/vision.md
