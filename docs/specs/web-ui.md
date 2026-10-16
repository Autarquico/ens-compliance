---
codename: ens-compliance
title: UI web con asistente
type: task
status: active
priority: P1
size: XL
issue: null
prs: []
related_issues: [29]
depends_on: [assessment-dossier, pre-audit]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# UI web con asistente

## Contexto / problema

UI única renderizada en servidor con htmx: asistente de cumplimiento, generador documental, estado y panel de agentes (docs/vision.md). Diseño según `.claude/design-briefs/base.yml`.

## Scope

**Entra:**
- Asistente: alcance, categorización, riesgos, aplicabilidad, respuestas de documentos.
- Vistas de matriz, evidencias, expediente y pre-auditoría.
- Login con MFA obligatorio u OIDC (M365/Google).
- SQLite para estado, usuarios y respuestas.

**No entra:**
- Panel de seguridad con alertas (v0.3).

## Criterios de aceptación

### Scenario: asistente completo
- **Given** un usuario apañado sin conocimientos de ENS
- **When** completa el asistente
- **Then** obtiene los mismos artefactos que el flujo CLI

### Scenario: MFA
- **Given** un usuario sin segundo factor
- **When** intenta entrar
- **Then** se le exige configurarlo

## Notas técnicas / riesgos

- Antes de diseñar: completar acento, tono y familia estética del brief de diseño.

## Tasks (no autoritativo — el board manda)

- [ ] Brief de diseño pendiente
- [ ] Auth
- [ ] Asistente
- [ ] Vistas
- [ ] SQLite

## Referencias

- docs/vision.md
