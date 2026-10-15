---
codename: ens-compliance
title: "Categorización, riesgos y aplicabilidad"
type: task
status: draft
priority: P1
size: L
issue: null
prs: []
related_issues: []
depends_on: [ens-catalog, discovery-inventory]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Categorización, riesgos y aplicabilidad

## Contexto / problema

Categorización del sistema, análisis de riesgos y declaración de aplicabilidad (docs/ens-compliance-guide.md:381-394).

## Scope

**Entra:**
- `scope/categorization.yaml`
- `risk/methodology.md`, `risk/register.yaml`, `risk/treatment-plan.yaml`
- `controls/applicability.yaml` con justificación por medida.
- Mapeo checks → medidas.

**No entra:**
- Evaluación final de conformidad.

## Criterios de aceptación

### Scenario: aplicabilidad justificada
- **Given** cada medida del catálogo
- **When** se valida `applicability.yaml`
- **Then** toda medida tiene estado y justificación; ninguna queda sin decidir

### Scenario: checks huérfanos
- **Given** checks sin medida aplicable asociada
- **When** se genera el mapeo
- **Then** se listan para decidir si se mantienen

## Notas técnicas / riesgos

- La categoría depende del impacto en las dimensiones de seguridad, no del tamaño de la empresa.

## Tasks (no autoritativo — el board manda)

- [ ] Categorización
- [ ] Registro de riesgos
- [ ] Aplicabilidad
- [ ] Mapeo checks

## Referencias

- docs/ens-compliance-guide.md:307-394
