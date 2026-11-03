---
codename: ens-compliance
title: Generación del expediente y sus estados
type: task
status: active
priority: P0
size: M
issue: 24
prs: [52]
related_issues: [24]
depends_on:
  - compliance-matrix
  - org-docs-templates
  - incident-register
  - checks-engine
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-11-03
---

# Generación del expediente y sus estados

## Contexto / problema

Genera el expediente de autoevaluación (docs/ens-compliance-guide.md:945-957) y gestiona sus estados:
generado → autoevaluación realizada → analizada por el responsable de la
seguridad (que eleva conclusiones al responsable del sistema) → declaración
de conformidad (solo BÁSICA). RD 311/2022, anexo III, 2.1.

## Scope

**Entra:**
- Expediente con las 11 secciones, enlazando matriz, documentos y evidencias.
- Transiciones de estado con persona, fecha y hash.
- Conclusiones y medidas correctoras del análisis.
- En MEDIA: aviso explícito de auditoría de certificación y paquete por medida.

**No entra:**
- Pre-auditoría: es `pre-audit`.

## Criterios de aceptación

### Scenario: estados en orden
- **Given** un expediente generado
- **When** se intenta declarar conformidad sin análisis
- **Then** se rechaza: falta el paso del responsable de la seguridad

### Scenario: MEDIA
- **Given** un sistema categorizado como MEDIA
- **When** se genera el expediente
- **Then** indica que requiere auditoría de certificación y no ofrece declaración

## Notas técnicas / riesgos

- Cita verificada en el BOE: anexo III, 2.1.

## Tasks (no autoritativo — el board manda)

- [ ] Generación
- [ ] Estados y roles
- [ ] Conclusiones
- [ ] Variante MEDIA

## Referencias

- docs/ens-compliance-guide.md:943-959
- https://www.boe.es/eli/es/rd/2022/05/03/311/con
