---
codename: ens-compliance
title: "Autoevaluación ENS"
type: epic
status: active
priority: P0
size: XL
issue: null
prs: []
related_issues: [1]
depends_on: []
supersedes: []
sub_issues: [2, 3, 4, 5, 6, 7, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36]
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Autoevaluación ENS

## Contexto / problema

Epic del producto "ENS en una caja para pymes" (docs/vision.md). Agrupa las specs por
fases (milestones v0.1, v0.2, v0.3). v0.1 = Autarqui hace su autoevaluación
con la herramienta; v0.2 = la usa una pyme que no es Autarqui; v0.3 y
siguientes = detección, incidentes automatizados, bundle offline.

## Scope

**Entra:**
- Las specs hijas (sub-issues), organizadas por milestone.

**No entra:**
- Lo listado como no-goals en docs/vision.md.
- Declarar conformidad automáticamente.

## Criterios de aceptación

### Scenario: cierre de v0.1
- **Given** todas las specs del milestone v0.1 cerradas
- **When** se revisa el expediente de Autarqui y el de la caja
- **Then** ambos cumplen la regla de preparación y el banco de defectos supera el umbral

### Scenario: el sistema sabe decir NOT READY
- **Given** medidas aplicables sin evidencia o sin revisión
- **When** se ejecuta `ens-evidence assessment status`
- **Then** el estado es `NOT READY` con la lista de bloqueos

## Notas técnicas / riesgos

- Fases, regla de preparación y validación del auditor: docs/vision.md.

## Tasks (no autoritativo — el board manda)

- [ ] v0.1: ver milestone v0.1
- [ ] v0.2: ver milestone v0.2
- [ ] v0.3: ver milestone v0.3

## Referencias

- docs/vision.md
- docs/ens-compliance-guide.md
