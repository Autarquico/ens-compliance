---
codename: ens-compliance
title: Catálogo de medidas del Anexo II
type: task
status: active
priority: P0
size: L
issue: 3
prs: []
related_issues: [3]
depends_on: [repo-foundation]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Catálogo de medidas del Anexo II

## Contexto / problema

Catálogo de medidas del Anexo II del RD 311/2022 como fichas YAML
(`controls/catalog/*.yaml`) con el formato de docs/ens-compliance-guide.md:320-347. Es independiente
de la infraestructura: todas las fichas arrancan con aplicabilidad `REVIEW` y
evaluación `NOT_ASSESSED`.

## Scope

**Entra:**
- Una ficha por medida del Anexo II con identificador y nombre oficiales.
- Esquema de validación de fichas y test que lo aplica a todo el catálogo.
- Referencia a la fuente (BOE, versión consolidada) en cada ficha.

**No entra:**
- Decidir aplicabilidad o categoría: es `ens-applicability`.
- Requisitos generados por un LLM sin contrastar con el texto oficial.

## Criterios de aceptación

### Scenario: catálogo trazable a la fuente
- **Given** el texto consolidado del RD 311/2022 en el BOE
- **When** se compara el listado de IDs del catálogo
- **Then** coincide uno a uno; cualquier diferencia queda listada para revisión humana

### Scenario: fichas válidas
- **Given** el catálogo completo
- **When** se ejecuta la validación de esquema en CI
- **Then** todas las fichas pasan y ninguna tiene estado distinto de `REVIEW`/`NOT_ASSESSED`

## Notas técnicas / riesgos

- Riesgo: transcripción errónea de IDs o requisitos. Revisión humana obligatoria antes de cerrar.
- Las guías CCN-STIC serie 800 complementan, no sustituyen, al Anexo II.

## Tasks (no autoritativo — el board manda)

- [ ] Extraer listado oficial
- [ ] Esquema de ficha
- [ ] Generar fichas
- [ ] Revisión humana

## Referencias

- https://www.boe.es/eli/es/rd/2022/05/03/311/con
- docs/ens-compliance-guide.md:307-394
