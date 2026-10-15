---
codename: ens-compliance
title: "Inventario y alcance"
type: task
status: draft
priority: P1
size: M
issue: null
prs: []
related_issues: []
depends_on: [collector-osquery, collector-wazuh]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Inventario y alcance

## Contexto / problema

Fase 0 de la guía (docs/ens-compliance-guide.md:82-147), aplazada al final porque aún no hay
infraestructura desplegada. Se apoya en los datos ya recogidos por osquery y
Wazuh.

## Scope

**Entra:**
- `scope/assets.yaml`, `services.yaml`, `dependencies.yaml` con los elementos de docs/ens-compliance-guide.md:86-98.
- `scope/system.yaml` con la delimitación del alcance.
- Cuestionario de descubrimiento y decisiones humanas registradas.

**No entra:**
- Secretos o tokens en el inventario.

## Criterios de aceptación

### Scenario: inventario reconciliado
- **Given** los hosts reportados por osquery y Wazuh
- **When** se compara con `scope/assets.yaml`
- **Then** cualquier host no inventariado o inventariado sin agente se lista como discrepancia

## Notas técnicas / riesgos

- La delimitación del alcance es decisión humana.

## Tasks (no autoritativo — el board manda)

- [ ] Cuestionario
- [ ] assets/services/dependencies
- [ ] Alcance
- [ ] Reconciliación con collectors

## Referencias

- docs/ens-compliance-guide.md:82-147
