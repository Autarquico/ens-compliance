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

Catálogo de medidas del anexo II del RD 311/2022 como datos de referencia
empaquetados en el producto, extraídos de forma determinista del texto
consolidado del BOE. El estado de cada organización frente a cada medida
vive en su workspace (`controls/measures.yaml`, ADR 0006 y 0007), con estado
inicial `UNDETERMINED` / `NOT_ASSESSED`.

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
- **Then** todas las fichas pasan

### Scenario: estado inicial de la organización
- **Given** un workspace sin `controls/measures.yaml`
- **When** se ejecuta `ens-evidence controls init`
- **Then** cada medida del catálogo queda con aplicabilidad `UNDETERMINED` y evaluación `NOT_ASSESSED`, y no se sobrescriben estados existentes

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
