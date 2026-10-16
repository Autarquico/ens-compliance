---
codename: ens-compliance
title: "Landing pública"
type: task
status: draft
priority: P1
size: M
issue: null
prs: []
related_issues: []
depends_on: []
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Landing pública

## Contexto / problema

Landing estática en el repo (GitHub Pages) con el estilo de `.claude/design-briefs/base.yml`: qué es, camino BÁSICA/MEDIA, instalación.

## Scope

**Entra:**
- Página estática, light y dark.
- SEO en español: metadatos, datos estructurados, contenido por dimensión y tipo de medida.
- Iconografía propia con Tabler.

**No entra:**
- Logotipos o distintivos del CCN.
- Afirmar certificación o garantía de cumplimiento.

## Criterios de aceptación

### Scenario: lenguaje honesto
- **Given** el copy de la landing
- **When** se revisa
- **Then** nunca dice que el producto certifica o garantiza el cumplimiento

### Scenario: SEO
- **Given** la página publicada
- **When** se valida con herramientas de datos estructurados
- **Then** no hay errores

## Notas técnicas / riesgos

- Exige repo público o plan de pago: decisión pendiente.
- Pendientes del brief: nombre, dominio, acento, tono.

## Tasks (no autoritativo — el board manda)

- [ ] Completar brief de diseño
- [ ] Contenido
- [ ] Maquetación
- [ ] SEO
- [ ] Publicación

## Referencias

- docs/vision.md
- .claude/design-briefs/base.yml
