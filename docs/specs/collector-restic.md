---
codename: ens-compliance
title: "Collector de Restic y pruebas de restauración"
type: task
status: draft
priority: P1
size: M
issue: null
prs: []
related_issues: []
depends_on: [evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de Restic y pruebas de restauración

## Contexto / problema

Checks BACKUP-001…006 y registro de restore tests (docs/ens-compliance-guide.md:808-840).

## Scope

**Entra:**
- Snapshots recientes, errores, acceso al repositorio, `check` de integridad, restore test vigente, copia externa.
- Registro de restore tests con el formato de docs/ens-compliance-guide.md:823-838.

**No entra:**
- Elegir destino de backups (decisión humana; afecta al nivel gratuito de R2).

## Criterios de aceptación

### Scenario: backup fallido
- **Given** una ejecución de backup con error
- **When** se evalúan los checks
- **Then** BACKUP-002 da `FAIL` y aparece como hallazgo

### Scenario: restore caducado
- **Given** el último restore test fuera de vigencia
- **When** se evalúa BACKUP-005
- **Then** da `FAIL`

## Notas técnicas / riesgos

- Restaurar siempre en destino aislado.

## Tasks (no autoritativo — el board manda)

- [ ] Lectura de restic
- [ ] Checks
- [ ] Formato restore test
- [ ] Tests

## Referencias

- https://restic.readthedocs.io/
- docs/ens-compliance-guide.md:808-840
