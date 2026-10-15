---
codename: ens-compliance
title: Autoevaluación ENS
type: epic
status: active
priority: P0
size: XL
issue: null
prs: []
related_issues: [1]
depends_on: []
supersedes: []
sub_issues: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Autoevaluación ENS

## Contexto / problema

Epic que agrupa la construcción del repositorio de cumplimiento ENS descrito en
`docs/ens-compliance-guide.md`: documentación, evidencias, checks deterministas, informes y
expediente de autoevaluación para una empresa unipersonal.

Decisiones de planificación tomadas sobre la guía (sesión 2026-10-15):
- Inventario, alcance y categorización van al final: aún no hay infraestructura desplegada.
- Fleet se sustituye por osquery + un servidor TLS propio en Cloudflare Workers (`osquery-edge`); sin dashboard.
- Storage de evidencias en Cloudflare R2, con copia independiente fuera de Cloudflare.

## Scope

**Entra:**
- Las 17 specs hijas listadas en Tasks, en el orden de dependencias declarado en cada una.

**No entra:**
- Lo listado en "No implementar inicialmente" (docs/ens-compliance-guide.md:1090-1099).
- Declarar conformidad automáticamente: siempre requiere revisión humana.

## Criterios de aceptación

### Scenario: el sistema sabe decir NOT READY
- **Given** medidas aplicables sin evidencia o con checks en FAIL
- **When** se ejecuta `ens-evidence assessment status`
- **Then** el estado es `NOT READY` con la lista de hallazgos bloqueantes

### Scenario: expediente completo
- **Given** todas las specs hijas cerradas
- **When** se genera el informe de autoevaluación
- **Then** contiene las 11 secciones de docs/ens-compliance-guide.md:945-957 y queda pendiente de aprobación humana

## Notas técnicas / riesgos

- La numeración de fases de la guía es incoherente (tabla 63-75 frente a cuerpo FASE 0-14); manda el orden de dependencias de las specs.
- Inconsistencia de estados de check en la guía (274, 361-368, 511-517): se resuelve en `repo-foundation`.

## Tasks (no autoritativo — el board manda)

- [ ] repo-foundation
- [ ] ens-catalog
- [ ] evidence-storage
- [ ] collector-github
- [ ] collector-cloudflare
- [ ] collector-workspace
- [ ] osquery-edge
- [ ] osquery-agents
- [ ] collector-osquery
- [ ] wazuh-deploy
- [ ] collector-wazuh
- [ ] collector-restic
- [ ] reports-scheduling
- [ ] incidents-ai
- [ ] discovery-inventory
- [ ] ens-applicability
- [ ] validation-selfassessment

## Referencias

- docs/ens-compliance-guide.md
