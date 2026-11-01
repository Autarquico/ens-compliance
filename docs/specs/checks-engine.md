---
codename: ens-compliance
title: Motor de checks y regla de preparación
type: task
status: shipped
priority: P0
size: M
issue: 23
prs: [50]
related_issues: [23]
depends_on:
  - collector-github
  - collector-cloudflare
  - collector-workspace
  - collector-restic
  - endpoint-evidence
  - compliance-matrix
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-31
---

# Motor de checks y regla de preparación

## Contexto / problema

`check --all` sobre la evidencia normalizada y la regla de preparación única de docs/vision.md.

## Scope

**Entra:**
- Motor `check --all`.
- Regla de preparación: `READY` solo con todas las medidas aplicables satisfechas, con evidencia vigente y revisión, y sin no conformidades abiertas.
- `ens-evidence assessment status` con la lista de bloqueos.

**No entra:**
- Informes periódicos: es `reports-scheduling`.

## Criterios de aceptación

### Scenario: NOT READY
- **Given** una medida aplicable bloqueada
- **When** se ejecuta `assessment status`
- **Then** devuelve `NOT READY` y la lista

### Scenario: juicio no ejecutado
- **Given** la pre-auditoría sin capa de juicio
- **When** se calcula el estado
- **Then** indica "revisión humana pendiente" en lugar de omitirlo

### Scenario: idempotencia
- **Given** una evaluación ya hecha
- **When** se repite
- **Then** los resultados no cambian ni se duplican

## Notas técnicas / riesgos

- La regla vive en un solo sitio; el resto la consulta.

## Tasks (no autoritativo — el board manda)

- [ ] Motor
- [ ] Regla de preparación
- [ ] assessment status
- [ ] Tests

## Referencias

- docs/vision.md
