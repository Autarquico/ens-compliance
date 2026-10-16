---
codename: ens-compliance
title: Collector de osquery y checks HOST
type: task
status: active
priority: P1
size: M
issue: 10
prs: []
related_issues: [10]
depends_on: [osquery-agents]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de osquery y checks HOST

## Contexto / problema

Evalúa HOST-001…007 sobre los resultados del servidor osquery; sustituye al script guiado de v0.1 como fuente principal.

## Scope

**Entra:**
- Lectura del storage local.
- HOST-002 por último contacto.
- CLI `hosts show` y `query`.

**No entra:**
- Panel (lo aporta `web-ui`).

## Criterios de aceptación

### Scenario: agente desconectado
- **Given** un host sin contacto por encima del umbral
- **When** se evalúa HOST-002
- **Then** da `FAIL`

### Scenario: misma evaluación que v0.1
- **Given** un host con evidencia de script y de agente
- **When** se evalúan los checks
- **Then** los resultados coinciden

## Notas técnicas / riesgos

- Reutiliza los checks de `endpoint-evidence`.

## Tasks (no autoritativo — el board manda)

- [ ] Lector
- [ ] Checks
- [ ] CLI
- [ ] Tests

## Referencias

- https://osquery.io/schema/
