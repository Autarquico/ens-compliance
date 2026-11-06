---
codename: ens-compliance
title: "Reservada nueva y dev ampliado para el umbral de v0.1"
type: task
status: draft
priority: P0
size: M
issue: null
prs: []
related_issues: []
depends_on: [audit-pack-tuning]
supersedes: []
related_specs: [audit-benchmark, audit-pack-tuning]
created: 2026-11-06
updated: 2026-11-06
---

# Reservada nueva y dev ampliado para el umbral de v0.1

## Contexto / problema

`audit-pack-tuning` dejó la partición de desarrollo por encima del umbral (falsas alarmas
A y B del 0 % con los dos pares), pero la medición única de la reservada no cerró v0.1
(`benchmark/measurements.jsonl`, acción `run-result` del 2026-11-06): claude-code detecta
el 92 % del juicio con tasas A 49 % y B 75 %; codex, el 76 % con A 42 % y B 25 %. Los
deterministas son 21/21 en los dos.

La reservada ya está vista: ajustar con ella invalidaría cualquier medición posterior
(ADR 0018). La decisión del usuario es rotar: la reservada actual pasa a desarrollo,
donde sí se puede diagnosticar y ajustar, y se crea una reservada nueva a ciegas.

## Scope

**Entra:**
- Pasar los perfiles y casos de la reservada actual a la partición de desarrollo,
  conservando su historial en `measurements.jsonl` y anotando las altas en la línea base.
- Diagnosticar las falsas alarmas y los fallos de juicio del dev ampliado: defecto real
  del banco frente a error del auditor.
- Corregir los perfiles (defectos del banco documentados) e iterar el pack solo sobre dev,
  con regrabaciones con el plan del usuario.
- Una reservada nueva, creada por un agente que no ve el pack, dev ni resultados, con las
  reglas de coherencia del repaso ciego (procedimientos que cubren cada letra de la
  cláusula enlazada; inventario y arquitectura coherentes; evidencias coherentes con
  procedimientos y justificaciones; fechas coherentes) y los mínimos de D7.
- Una única medición de la reservada nueva cuando dev pase la puerta `target`.

**No entra:**
- Cambiar el umbral, la puntuación ni los mínimos de D7.
- Leer la reservada nueva al ajustar.
- Proveedores con API como puerta.

## Criterios de aceptación

### Scenario: la reservada vista pasa a desarrollo
- **Given** la reservada medida por `audit-pack-tuning`
- **When** se ejecuta `benchmark validate` sobre dev
- **Then** sus casos están en dev, `measurements.jsonl` conserva todas sus líneas y la
  puerta de línea base pasa con las altas anotadas

### Scenario: desarrollo ampliado supera el umbral
- **Given** el pack ajustado y el dev ampliado regrabado
- **When** se ejecuta `benchmark run --partition dev --all-recorded --gate target`
- **Then** cada par de la puerta detecta el 100 % de lo determinista, ≥ 80 % del juicio,
  y sus tasas A y B son ≤ 20 %

### Scenario: la reservada nueva se crea a ciegas
- **Given** el historial de la spec
- **When** se revisa quién creó la reservada nueva
- **Then** el agente que la creó no leyó el pack, dev ni resultados, y sus miradas quedan
  en `measurements.jsonl` con `--measurement`

### Scenario: la reservada nueva se mide una vez
- **Given** dev ampliado por encima del umbral
- **When** se mide `--partition holdout --gate threshold --measurement` con cada par
- **Then** queda una línea `run-result` por par y el resultado dice si v0.1 cierra

## Notas técnicas / riesgos

- La reservada nueva debe cumplir los mínimos de D7 (≥ 20 de juicio, 10 deterministas,
  3 correctos; solo BÁSICA) con perfiles nuevos, distintos de los de dev.
- Coste: cada iteración en dev pasa a ~47 casos × 3 repeticiones × 2 pares.
- Codex devolvió `provider_error` transitorios al grabar en paralelo con claude-code;
  grabar en serie y comprobar los resultados antes de puntuar.
- Si tras 4 iteraciones dev no pasa, se vuelve al usuario.

## Tasks (no autoritativo — el board manda)

- [ ] Reservada actual → dev, con altas en la línea base
- [ ] Diagnóstico del dev ampliado
- [ ] Correcciones del banco e iteración del pack
- [ ] Reservada nueva a ciegas
- [ ] Medición única

## Referencias

- docs/specs/audit-pack-tuning.md
- docs/decisions/0018-banco-defectos.md, docs/runbooks/banco-defectos.md
