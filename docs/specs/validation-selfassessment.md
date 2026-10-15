---
codename: ens-compliance
title: "Validación y expediente de autoevaluación"
type: task
status: draft
priority: P1
size: L
issue: null
prs: []
related_issues: []
depends_on: [reports-scheduling, ens-applicability]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Validación y expediente de autoevaluación

## Contexto / problema

Pruebas de validación (docs/ens-compliance-guide.md:963-977) y expediente de autoevaluación (docs/ens-compliance-guide.md:943-959).

## Scope

**Entra:**
- Cada prueba de la tabla docs/ens-compliance-guide.md:965-975 ejecutada y registrada.
- Prueba de recuperación de `ens-evidence`.
- Informe de autoevaluación con las 11 secciones.

**No entra:**
- Aprobación: la firma el usuario.

## Criterios de aceptación

### Scenario: API revocada
- **Given** un token de prueba revocado
- **When** se ejecuta la recogida
- **Then** da `ERROR`, nunca `PASS`

### Scenario: quitar protección
- **Given** una protección de prueba desactivada
- **When** se ejecutan los checks
- **Then** se detecta la desviación

### Scenario: expediente
- **Given** todas las pruebas registradas
- **When** se genera la autoevaluación
- **Then** queda pendiente de aprobación humana con fecha

## Notas técnicas / riesgos

- Revisión humana obligatoria antes de concluir conformidad.

## Tasks (no autoritativo — el board manda)

- [ ] Pruebas de validación
- [ ] Recuperación de ens-evidence
- [ ] Expediente
- [ ] Revisión humana

## Referencias

- docs/ens-compliance-guide.md:943-977
