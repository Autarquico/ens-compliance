---
codename: ens-compliance
title: "El audit pack generaliza más allá de dev"
type: task
status: draft
priority: P1
size: L
issue: null
prs: []
related_issues: [57, 59]
depends_on: [audit-holdout-rotation]
supersedes: []
related_specs: [audit-benchmark, audit-pack-tuning, audit-holdout-rotation, audit-openai-pair]
created: 2026-11-08
updated: 2026-11-08
---

# El audit pack generaliza más allá de dev

## Contexto / problema

Al cerrar `audit-holdout-rotation` (PR #60), el pack v3 con `claude-code/claude-opus-5-5` da estos resultados en dev:

- deterministas: 31/31;
- juicio: 48/50 (96 %);
- falsas alarmas A: 2 %;
- falsas alarmas B: 0 %.

La reservada nueva, creada a ciegas y medida una sola vez (`measurements.jsonl`, `run-result` del 2026-11-08T16:31Z), da:

- deterministas: 18/18;
- juicio: 9/24 (37.5 %);
- falsas alarmas A: 35.7 %;
- falsas alarmas B: 25 %.

La rotación anterior ya mostró la misma brecha: el 92 % del juicio con A 49 % en su reservada (`audit-pack-tuning`). Por tanto, el resultado en dev no predice el de la reservada, y v0.1 no cierra.

Esta reservada ya está vista. Diagnosticar con ella la gasta, así que pasa a dev y hace falta otra nueva, también a ciegas.

## Scope

**Entra:**
- Rotar de nuevo: la reservada medida pasa a dev, como en `audit-holdout-rotation`.
- Diagnosticar cada fallo de juicio y cada falsa alarma de esa medición. Cada uno se clasifica como defecto del banco, error del auditor o sobreajuste: una regla del pack que solo encaja con la redacción de los casos de dev.
- Ajustar el pack y el banco. Cada cambio del pack lo justifica una clase de fallo, nunca un caso concreto.
- Crear una reservada nueva a ciegas, con los mínimos de D7.
- Medirla una sola vez con claude-code.

**No entra:**
- Cambiar el umbral o los mínimos de D7.
- Volver a poner codex en la puerta; eso es #59. Si el pack cambia, #59 mide sobre la reservada nueva.

## Criterios de aceptación

### Scenario: la reservada medida pasa a desarrollo
- **Given** la reservada medida en `audit-holdout-rotation`
- **When** se ejecuta `benchmark validate` sobre dev
- **Then** sus casos están en dev, `measurements.jsonl` conserva todas sus líneas y la puerta de línea base pasa con las altas anotadas

### Scenario: el diagnóstico clasifica cada fallo
- **Given** la medición del 2026-11-08 con sus grabaciones
- **When** se revisa el diagnóstico
- **Then** cada defecto de juicio no detectado y cada falsa alarma tiene una clase (banco, auditor o sobreajuste) y la corrección que le corresponde

### Scenario: dev ampliado supera el umbral
- **Given** el pack ajustado y dev regrabado
- **When** se ejecuta `benchmark run --partition dev --all-recorded --gate target`
- **Then** claude-code detecta el 100 % de lo determinista y ≥ 80 % del juicio, y sus tasas A y B son ≤ 20 %

### Scenario: la reservada nueva se mide una vez
- **Given** dev por encima del umbral y una reservada nueva creada a ciegas
- **When** se mide `--partition holdout --gate threshold --measurement` con claude-code
- **Then** queda una línea `run-result` y el resultado dice si v0.1 cierra

## Notas técnicas / riesgos

- La brecha se ha repetido en dos rotaciones. Si la tercera tampoco cierra, se vuelve al usuario antes de rotar otra vez.
- Cada reservada gastada sale de una generación nueva de casos a ciegas, que cuesta mucho. Por eso conviene diagnosticar a fondo antes de crear la siguiente.
- Para revisar el diagnóstico hay que leer las grabaciones de la reservada medida, que dejan de estar reservadas en cuanto pasan a dev.

## Tasks (no autoritativo — el board manda)

- [ ] Reservada medida → dev, con altas en la línea base
- [ ] Diagnóstico por clase de fallo
- [ ] Ajuste del pack y del banco
- [ ] Regrabación de dev y puerta target
- [ ] Reservada nueva a ciegas
- [ ] Medición única

## Referencias

- docs/specs/audit-holdout-rotation.md
- docs/specs/audit-openai-pair.md
- docs/decisions/0018-banco-defectos.md
