---
codename: ens-compliance
title: Informes y scheduling
type: task
status: shipped
priority: P0
size: M
issue: 14
prs: [51]
related_issues: [14]
depends_on: [checks-engine]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-11-03
---

# Informes y scheduling

## Contexto / problema

Informes diario y mensual (docs/ens-compliance-guide.md:909-941) y ejecución programada dentro de la app.

## Scope

**Entra:**
- Informe diario y mensual en HTML y JSON con fuentes y timestamps.
- Scheduler dentro de la app (no systemd) que encadena recogida → checks → informes.

**No entra:**
- Dashboard interactivo (v0.2).

## Criterios de aceptación

### Scenario: informe trazable
- **Given** una ejecución diaria
- **When** se genera el informe
- **Then** cada dato cita su fuente y su timestamp

### Scenario: fuente caída
- **Given** un conector con error
- **When** se ejecuta el ciclo
- **Then** el informe lo muestra como `ERROR` y el resto continúa

## Notas técnicas / riesgos

- El scheduler corre en el contenedor de la app.

## Tasks (no autoritativo — el board manda)

- [ ] Informe diario
- [ ] Informe mensual
- [ ] Scheduler

## Referencias

- docs/ens-compliance-guide.md:905-941
