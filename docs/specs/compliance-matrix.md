---
codename: ens-compliance
title: Matriz de cumplimiento por medida
type: task
status: active
priority: P0
size: L
issue: 20
prs: []
related_issues: [20]
depends_on: [ens-applicability, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Matriz de cumplimiento por medida

## Contexto / problema

La autoevaluación BÁSICA debe indicar "si cada medida de seguridad está
implantada y sujeta a revisión regular", con las evidencias que lo sustentan
(RD 311/2022, anexo III, 2.1). La matriz lo hace explícito para cada medida
aplicable.

## Scope

**Entra:**
- Por medida aplicable: requisitos, evidencias técnicas (checks) o manuales, responsable, periodicidad y fecha de la última revisión.
- CLI para registrar revisiones y vincular evidencias manuales.
- Cálculo de bloqueos: medida sin evidencia, con evidencia caducada o sin revisión vigente.

**No entra:**
- Generación del expediente: es `assessment-dossier`.

## Criterios de aceptación

### Scenario: medida sin revisión
- **Given** una medida aplicable con evidencia pero sin revisión vigente
- **When** se evalúa la matriz
- **Then** aparece como bloqueo

### Scenario: evidencia manual
- **Given** una medida organizativa
- **When** se vincula un documento aprobado y se registra la revisión
- **Then** la medida cuenta como implantada y revisada

### Scenario: cobertura completa
- **Given** todas las medidas aplicables
- **When** se exporta la matriz
- **Then** ninguna medida aplicable queda fuera

## Notas técnicas / riesgos

- Cita verificada en el BOE: anexo III, 2.1.
- Contrato y decisiones: ADR 0011. Guía: `docs/runbooks/matriz-de-cumplimiento.md`.
- Umbrales decididos por el usuario (2026-10-30): periodicidad explícita por
  medida, máximo 365 días; evidencia técnica sin caducidad, 30 días; evidencia
  manual sin caducidad, dentro de la periodicidad; revisan el administrador
  del sistema o su delegado (anexo III, 2.1.a).

## Tasks (no autoritativo — el board manda)

- [ ] Modelo de la matriz
- [ ] CLI de revisiones
- [ ] Bloqueos
- [ ] Export

## Referencias

- https://www.boe.es/eli/es/rd/2022/05/03/311/con
