---
codename: ens-compliance
title: Validación y cierre de la autoevaluación
type: task
status: active
priority: P0
size: L
issue: 18
prs: []
related_issues: [18]
depends_on: [audit-benchmark, box-packaging]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Validación y cierre de la autoevaluación

## Contexto / problema

Cierre de v0.1: pruebas de validación (docs/ens-compliance-guide.md:963-977), autoauditoría de Autarqui y de la caja desplegada, y paso humano por los estados del expediente.

## Scope

**Entra:**
- Pruebas de docs/ens-compliance-guide.md:965-975 ejecutadas y registradas.
- Pre-auditoría sobre el expediente final de Autarqui y sobre el de la caja.
- Análisis del responsable de la seguridad y conclusiones al responsable del sistema.
- Declaración de conformidad si es BÁSICA y `READY`.

**No entra:**
- Aprobación automática.

## Criterios de aceptación

### Scenario: API revocada
- **Given** un token de prueba revocado
- **When** se ejecuta la recogida
- **Then** da `ERROR`, nunca `PASS`

### Scenario: la caja se audita
- **Given** la caja desplegada como activo del alcance
- **When** se ejecuta la pre-auditoría
- **Then** cumple la regla de preparación o v0.1 no cierra

### Scenario: cierre humano
- **Given** expediente `READY`
- **When** se completan los estados
- **Then** queda la declaración con persona y fecha

## Notas técnicas / riesgos

- Revisión humana obligatoria.

## Tasks (no autoritativo — el board manda)

- [ ] Pruebas de validación
- [ ] Autoauditoría Autarqui
- [ ] Autoauditoría caja
- [ ] Cierre humano

## Referencias

- docs/ens-compliance-guide.md:943-977
- docs/vision.md
