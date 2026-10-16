---
codename: ens-compliance
title: "Procedimiento y registro de incidentes"
type: task
status: draft
priority: P0
size: S
issue: null
prs: []
related_issues: []
depends_on: [org-docs-templates, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Procedimiento y registro de incidentes

## Contexto / problema

op.exp.7 (gestión de incidentes) y op.exp.9 (registro de la gestión de
incidentes) aplican también en BÁSICA. v0.1 cubre el procedimiento, los
responsables y un registro manual verificable; la automatización y la
correlación quedan para v0.3 (`incidents-ai`).

## Scope

**Entra:**
- Procedimiento de gestión de incidentes como documento de `org-docs-templates`.
- Registro con el modelo de incidente de docs/ens-compliance-guide.md:867-890, gestionado por CLI (`incident new|update|close`).
- Cada cambio queda como evidencia con hash.
- Prueba del procedimiento con un incidente simulado.

**No entra:**
- Ingesta automática de alertas (v0.3).

## Criterios de aceptación

### Scenario: incidente simulado
- **Given** el procedimiento aprobado
- **When** se registra y cierra un incidente de prueba
- **Then** queda el historial completo como evidencia de op.exp.7 y op.exp.9

### Scenario: registro vacío
- **Given** ningún incidente ni prueba registrados
- **When** se evalúa la matriz de cumplimiento
- **Then** op.exp.9 aparece sin evidencia y bloquea

## Notas técnicas / riesgos

- Comprobado en el BOE: op.exp.7 y op.exp.9 aplican en BÁSICA.

## Tasks (no autoritativo — el board manda)

- [ ] Plantilla de procedimiento
- [ ] Modelo y CLI
- [ ] Evidencia por cambio
- [ ] Simulacro

## Referencias

- docs/ens-compliance-guide.md:844-901
- https://www.boe.es/eli/es/rd/2022/05/03/311/con
