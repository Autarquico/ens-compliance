---
codename: ens-compliance
title: Inventario y alcance
type: task
status: active
priority: P0
size: M
issue: 16
prs: [38]
related_issues: [16]
depends_on: [repo-foundation]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-27
---

# Inventario y alcance

## Contexto / problema

Puerta inicial de v0.1: inventario declarado por CLI y delimitación del
alcance (docs/ens-compliance-guide.md:82-147). La reconciliación con la evidencia de endpoints es
opcional y no bloquea.

## Scope

**Entra:**
- `scope/assets.yaml`, `services.yaml`, `dependencies.yaml` con los elementos de docs/ens-compliance-guide.md:86-98.
- `scope/system.yaml` con el alcance aprobado (persona y fecha).
- Cuestionario de descubrimiento.
- Reconciliación opcional con `endpoint-evidence`.

**No entra:**
- Secretos en el inventario.
- Dependencia de Wazuh u osquery-server.

## Criterios de aceptación

### Scenario: alcance aprobado
- **Given** el inventario completo
- **When** se aprueba el alcance
- **Then** queda registrado y desbloquea la categorización

### Scenario: host no inventariado
- **Given** evidencia de un endpoint que no está en `assets.yaml`
- **When** se reconcilia
- **Then** se lista como discrepancia

## Notas técnicas / riesgos

- La delimitación del alcance es decisión humana.

## Tasks (no autoritativo — el board manda)

- [ ] Cuestionario
- [ ] Esquemas YAML
- [ ] CLI
- [ ] Reconciliación opcional

## Referencias

- docs/ens-compliance-guide.md:82-147
