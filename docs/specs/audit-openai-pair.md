---
codename: ens-compliance
title: Par OpenAI como puerta del umbral de v0.1
type: task
status: active
priority: P1
size: S
issue: 59
prs: []
related_issues: [59]
depends_on: [audit-holdout-rotation]
supersedes: []
related_specs: [audit-benchmark, audit-pack-tuning, audit-holdout-rotation]
created: 2026-11-08
updated: 2026-11-08
---

# Par OpenAI como puerta del umbral de v0.1

## Contexto / problema

La visión promete la pre-auditoría con clientes Anthropic y OpenAI (`docs/vision.md`,
«Entrega por fases», v0.1), y el umbral se exige por proveedor. En `audit-holdout-rotation`
el plan contratado de codex agotó su límite semanal al grabar dev (2026-11-07). El usuario
decidió el 2026-11-08 aplazar la prueba de codex: `claude-code/claude-opus-5-5` queda como
único par de puerta y `codex/gpt-6.1-sol` sigue soportado sin puerta (ADR 0018, enmienda).
v0.1 cierra de forma provisional solo con Anthropic.

## Scope

**Entra:**
- Grabar dev con `codex/gpt-6.1-sol` (`record --pending`, en serie) con el pack vigente.
- Volver a añadir el par a la puerta, cambiando una sola línea en `REQUIRED_PAIRS`, y revertir la enmienda de ADR 0018 y de la visión.
- Diagnosticar los fallos de codex en dev: defecto del banco o error del auditor.
- Medir la reservada con codex una sola vez, si el pack no ha cambiado desde la medición
  de claude-code.

**No entra:**
- Ajustar el pack mirando la reservada.
- Cambiar el umbral ni los mínimos de D7.

## Criterios de aceptación

### Scenario: codex supera el umbral en desarrollo
- **Given** el pack de la medición de claude-code y dev grabado con codex
- **When** se ejecuta `benchmark run --partition dev --all-recorded --gate target`
- **Then** codex detecta el 100 % de lo determinista y ≥ 80 % del juicio, y sus tasas A y B son ≤ 20 %

### Scenario: el par vuelve a ser puerta
- **Given** el par añadido a `REQUIRED_PAIRS`
- **When** corre el job `benchmark` del CI
- **Then** falta de grabación de cualquiera de los dos pares sale con 3

### Scenario: la reservada se mide una vez con codex
- **Given** el mismo pack que midió claude-code
- **When** se mide `--partition holdout --gate threshold --measurement` con codex
- **Then** queda una línea `run-result` de codex y el resultado dice si v0.1 cierra con los dos proveedores

## Notas técnicas / riesgos

- **Pack ajustado tras la medición de claude-code:** la reservada actual queda gastada para ese pack. Hay que rotarla otra vez, como en `audit-holdout-rotation`.
- **Cuota:** comprobarla antes con `codex exec "ok"`. Al agotarse, la CLI solo deja `provider_error` en la grabación.
- **Coste:** ~48 casos de juicio × 3 repeticiones en dev.

## Tasks (no autoritativo — el board manda)

- [ ] Grabar dev con codex
- [ ] Diagnóstico y, si hace falta, correcciones del banco
- [ ] Par de vuelta en la puerta (código, ADR, visión)
- [ ] Medición única de la reservada con codex

## Referencias

- docs/specs/audit-holdout-rotation.md
- docs/decisions/0018-banco-defectos.md
