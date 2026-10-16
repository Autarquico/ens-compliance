---
codename: ens-compliance
title: Bundle offline y releases firmadas
type: task
status: active
priority: P2
size: L
issue: null
prs: []
related_issues: [35]
depends_on: [installer]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Bundle offline y releases firmadas

## Contexto / problema

Releases autocontenidas: imágenes exportadas con checksums, firma verificable y paquetes de agente por SO.

## Scope

**Entra:**
- Bundle offline por release.
- Firma de releases y paquetes.
- Paquetes de agente (osquery, Wazuh) generados por la caja.
- Inventario de licencias de redistribución.

**No entra:**
- Imagen de VM precompilada.

## Criterios de aceptación

### Scenario: instalación sin Internet
- **Given** una VM sin salida y el bundle
- **When** se ejecuta el instalador
- **Then** arranca con las versiones y checksums de la release

### Scenario: firma inválida
- **Given** un bundle alterado
- **When** se verifica
- **Then** el instalador lo rechaza

## Notas técnicas / riesgos

- Sin verificar: licencias de redistribución de cada componente.

## Tasks (no autoritativo — el board manda)

- [ ] Bundle
- [ ] Firma
- [ ] Paquetes de agente
- [ ] Licencias

## Referencias

- docs/vision.md
