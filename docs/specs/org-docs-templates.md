---
codename: ens-compliance
title: Plantillas de documentación organizativa
type: task
status: shipped
priority: P0
size: L
issue: 21
prs: [47]
related_issues: [21]
depends_on: [repo-foundation]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-30
---

# Plantillas de documentación organizativa

## Contexto / problema

La mayor parte del valor para una pyme está en la documentación organizativa.
Plantillas versionadas que se rellenan desde respuestas YAML y generan los
documentos que exige el Anexo II para la categoría del sistema.

## Scope

**Entra:**
- Plantillas: política de seguridad, roles (responsable de la información, del servicio, de la seguridad y del sistema), normativa de uso, procedimientos operativos.
- Respuestas en `org/answers.yaml` con esquema validado; generación con `ens-evidence docs build`.
- Cada documento referencia las medidas del catálogo que soporta.
- Registro de aprobación de cada documento (quién, cuándo, versión).

**No entra:**
- Asistente web (v0.2).
- Revisión por auditor (v0.2, issue aparte).

## Criterios de aceptación

### Scenario: documento trazable
- **Given** respuestas completas
- **When** se generan los documentos
- **Then** cada documento lista las medidas que soporta y su versión

### Scenario: respuesta incompleta
- **Given** una respuesta obligatoria vacía
- **When** se generan los documentos
- **Then** la generación falla indicando la pregunta pendiente, sin texto de relleno

### Scenario: aprobación registrada
- **Given** un documento generado
- **When** se aprueba
- **Then** queda registrado con persona, fecha y hash del documento

## Notas técnicas / riesgos

- Riesgo: documentación "de papel". Lenguaje concreto, sin genéricos.
- Los requisitos salen del texto oficial, no de un LLM.

## Tasks (no autoritativo — el board manda)

- [ ] Inventario de documentos por categoría
- [ ] Plantillas
- [ ] Esquema de respuestas
- [ ] docs build
- [ ] Aprobaciones

## Referencias

- https://www.boe.es/eli/es/rd/2022/05/03/311/con
