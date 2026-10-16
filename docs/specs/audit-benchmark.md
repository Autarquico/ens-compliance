---
codename: ens-compliance
title: Banco de defectos sembrados
type: task
status: active
priority: P0
size: M
issue: 26
prs: []
related_issues: [26]
depends_on: [pre-audit]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Banco de defectos sembrados

## Contexto / problema

Valida la pre-auditoría contra la legislación con el método y el umbral de docs/vision.md ("Validación del auditor").

## Scope

**Entra:**
- Casos: variantes del expediente de Autarqui con defectos sembrados, expedientes correctos y sintéticos de otras pymes.
- Cada defecto cita el texto literal del requisito del Anexo II que incumple.
- Particiones de desarrollo y reservada; el umbral se mide solo en la reservada.
- Puntuación: detección por medida + evidencia/documento, duplicados una vez, falsas alarmas sobre hallazgos emitidos y aparte sobre expedientes correctos, 3 repeticiones con mediana.
- Registro por ejecución: versión del audit pack, proveedor, modelo, temperatura.

**No entra:**
- Ajustar prompts con la partición reservada.

## Criterios de aceptación

### Scenario: umbral
- **Given** la partición reservada
- **When** se ejecuta con Anthropic y con OpenAI
- **Then** 100 % deterministas y ≥ 80 % de juicio con ≤ 20 % de falsas alarmas por proveedor, o v0.1 no cierra

### Scenario: regresión
- **Given** un cambio en el audit pack
- **When** se ejecuta en CI la partición de desarrollo
- **Then** una bajada de detección hace fallar el pipeline

## Notas técnicas / riesgos

- El 100 % determinista es cobertura del banco, no garantía universal.

## Tasks (no autoritativo — el board manda)

- [ ] Formato de caso
- [ ] Defectos sembrados
- [ ] Casos correctos y sintéticos
- [ ] Puntuación
- [ ] Runner + CI

## Referencias

- docs/vision.md
