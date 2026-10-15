---
codename: ens-compliance
title: "Collector de osquery y checks HOST"
type: task
status: draft
priority: P1
size: M
issue: null
prs: []
related_issues: []
depends_on: [evidence-storage, osquery-agents]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de osquery y checks HOST

## Contexto / problema

Lee los snapshots de osquery en R2 y evalúa los checks HOST-001…007 (docs/ens-compliance-guide.md:551-563).

## Scope

**Entra:**
- HOST-001 inventariado, HOST-002 agente activo (último contacto < umbral), HOST-003 SO soportado, HOST-004 cifrado, HOST-005 firewall, HOST-006 updates, HOST-007 inventario de software.
- CLI: `ens-evidence hosts show <host>` y `ens-evidence query <host> "SQL"`.

**No entra:**
- Dashboard.

## Criterios de aceptación

### Scenario: agente desconectado
- **Given** un host sin contacto por encima del umbral
- **When** se evalúa HOST-002
- **Then** da `FAIL` y aparece en el informe como cobertura perdida

### Scenario: tabla no disponible
- **Given** un host donde la tabla de cifrado o firewall no devuelve datos
- **When** se evalúa HOST-004/005
- **Then** da `UNKNOWN`, nunca `PASS`

## Notas técnicas / riesgos

- Sin verificar: tablas de cifrado y firewall en las versiones de macOS/Linux en uso.

## Tasks (no autoritativo — el board manda)

- [ ] Lector R2
- [ ] Checks HOST
- [ ] CLI hosts/query
- [ ] Fixtures + tests

## Referencias

- docs/ens-compliance-guide.md:551-563
- https://osquery.io/schema/
