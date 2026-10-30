---
codename: ens-compliance
title: Categorización, riesgos y aplicabilidad
type: task
status: active
priority: P0
size: L
issue: 17
prs: [46]
related_issues: [17]
depends_on: [ens-catalog, discovery-inventory]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-30
---

# Categorización, riesgos y aplicabilidad

## Contexto / problema

Categorización del sistema, análisis de riesgos y declaración de
aplicabilidad. Es puerta de v0.1: si el sistema de Autarqui resulta MEDIA,
el resultado comprometido pasa a "preparado para certificación" (docs/vision.md).

## Scope

**Entra:**
- `scope/categorization.yaml` aprobado
- `risk/methodology.md`, `risk/register.yaml`, `risk/treatment-plan.yaml`
- `controls/applicability.yaml`: Declaración de Aplicabilidad firmada (art. 28.2), con la justificación por medida en `controls/measures.yaml` (ADR 0009)

**No entra:**
- Matriz de evidencias por medida: es `compliance-matrix`.

## Criterios de aceptación

### Scenario: categoría decide el compromiso
- **Given** la categorización aprobada
- **When** resulta MEDIA
- **Then** el epic y el expediente pasan a "preparado para certificación"

### Scenario: aplicabilidad justificada
- **Given** cada medida del catálogo
- **When** se valida la aplicabilidad (`controls/measures.yaml`) para firmar la Declaración de Aplicabilidad (`controls/applicability.yaml`)
- **Then** toda medida tiene estado y justificación

## Notas técnicas / riesgos

- La categoría depende del impacto en las dimensiones, no del tamaño de la empresa.

## Tasks (no autoritativo — el board manda)

- [ ] Categorización
- [ ] Riesgos
- [ ] Aplicabilidad

## Referencias

- docs/ens-compliance-guide.md:307-394
- https://www.boe.es/eli/es/rd/2022/05/03/311/con
