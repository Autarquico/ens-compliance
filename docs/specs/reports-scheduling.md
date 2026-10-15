---
codename: ens-compliance
title: "Checks, informes y scheduling"
type: task
status: draft
priority: P1
size: L
issue: null
prs: []
related_issues: []
depends_on: [collector-github, collector-cloudflare, collector-workspace, collector-osquery, collector-wazuh, collector-restic]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Checks, informes y scheduling

## Contexto / problema

`check --all`, informes diario y mensual, `assessment status` y ejecución programada (docs/ens-compliance-guide.md:735-804, 905-959).

## Scope

**Entra:**
- Motor `check --all` sobre la evidencia normalizada.
- Informe diario (docs/ens-compliance-guide.md:909-923) y mensual (docs/ens-compliance-guide.md:929-941) en HTML y JSON con fuentes y timestamps.
- `assessment status` capaz de devolver `NOT READY` con hallazgos bloqueantes.
- systemd service + timer (docs/ens-compliance-guide.md:760-784) desplegados con Ansible.

**No entra:**
- Dashboard interactivo.

## Criterios de aceptación

### Scenario: reejecución idempotente
- **Given** una recogida ya hecha hoy
- **When** se vuelve a ejecutar
- **Then** no duplica evidencias ni cambia resultados

### Scenario: LLM caído
- **Given** el adaptador de IA no disponible
- **When** se ejecutan checks e informes
- **Then** se completan sin él

### Scenario: NOT READY
- **Given** hallazgos bloqueantes abiertos
- **When** se ejecuta `assessment status`
- **Then** devuelve `NOT READY` y los lista

## Notas técnicas / riesgos

- Ejecutor interno para fuentes no accesibles desde CI.

## Tasks (no autoritativo — el board manda)

- [ ] Motor de checks
- [ ] Informe diario
- [ ] Informe mensual
- [ ] assessment status
- [ ] systemd + Ansible

## Referencias

- docs/ens-compliance-guide.md:735-804
- docs/ens-compliance-guide.md:905-959
