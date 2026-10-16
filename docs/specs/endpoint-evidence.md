---
codename: ens-compliance
title: Evidencia de endpoints con script guiado
type: task
status: active
priority: P0
size: S
issue: null
prs: []
related_issues: [19]
depends_on: [evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Evidencia de endpoints con script guiado

## Contexto / problema

En v0.1 no hay servidor osquery. La evidencia de endpoints (el Mac y los
servidores de Autarqui) se obtiene con un script guiado que ejecuta en local
las mismas consultas osquery que usará v0.2 y entrega el resultado a la caja
como evidencia con hash.

## Scope

**Entra:**
- Script para macOS y Linux que ejecuta `osqueryi` con un conjunto versionado de consultas (SO, cifrado, firewall, actualizaciones, software).
- Salida JSON firmada con hash e importada con `ens-evidence evidence import`.
- Checks HOST-001…007 evaluados sobre esa evidencia; antigüedad máxima configurable.

**No entra:**
- Agente permanente ni servidor osquery (v0.2).

## Criterios de aceptación

### Scenario: evidencia de un portátil
- **Given** osquery instalado en el Mac
- **When** se ejecuta el script e importa el resultado
- **Then** aparece la evidencia con hash y los checks HOST se evalúan

### Scenario: evidencia caducada
- **Given** la última ejecución de un host más antigua que el umbral
- **When** se evalúan los checks
- **Then** el host aparece como evidencia caducada y bloquea

### Scenario: tabla no disponible
- **Given** una consulta que no devuelve datos en ese SO
- **When** se evalúa el check
- **Then** da `UNKNOWN`, nunca `PASS`

## Notas técnicas / riesgos

- Sin verificar: tablas de cifrado y firewall en las versiones en uso.
- Las consultas se reutilizan en `osquery-server`.

## Tasks (no autoritativo — el board manda)

- [ ] Consultas versionadas
- [ ] Script macOS/Linux
- [ ] evidence import
- [ ] Checks HOST
- [ ] Tests con fixtures

## Referencias

- https://osquery.io/schema/
- docs/ens-compliance-guide.md:551-563
