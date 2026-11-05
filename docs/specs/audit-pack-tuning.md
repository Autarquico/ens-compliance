---
codename: ens-compliance
title: "Ajuste del audit pack hasta el umbral de v0.1"
type: task
status: draft
priority: P0
size: M
issue: null
prs: []
related_issues: []
depends_on: [audit-benchmark]
supersedes: []
related_specs: []
created: 2026-11-05
updated: 2026-11-05
---

# Ajuste del audit pack hasta el umbral de v0.1

## Contexto / problema

La línea base de desarrollo del banco de defectos (PR #55, `benchmark/baseline/dev.json`)
queda lejos del umbral de v0.1 (docs/vision.md, «Validación del auditor», punto 4):
detección de juicio del 54 % (claude-code/claude-opus-5-5) y del 62 % (codex/gpt-6.1-sol)
frente al ≥ 80 % exigido, y un 83 % de falsas alarmas en claude-code. Parte de las falsas
alarmas viene del propio banco (por ejemplo, justificaciones de medidas no seleccionadas
idénticas y genéricas en los perfiles). Medir la reservada así solo confirmaría que v0.1
no cierra.

## Scope

**Entra:**
- Diagnóstico por defecto y por hallazgo de las grabaciones de desarrollo: qué no se
  detecta y por qué, y qué falsas alarmas son defectos reales no declarados del banco.
- Realismo de los perfiles del banco: corregir lo que hace que un expediente «correcto»
  no lo sea. En la reservada, lo aplica un agente que no ve el pack ni resultados, con la
  misma regla genérica.
- Nueva versión del audit pack (prompt y criterios), versionada en su manifiesto.
- Regrabar la partición de desarrollo con los dos pares de la puerta (envíos reales con el
  plan del usuario) y escribir la línea base, iterando solo sobre desarrollo.
- Una única medición de la reservada cuando desarrollo supere el umbral.

**No entra:**
- Cambiar el umbral ni la puntuación para que pase.
- Leer la reservada al ajustar (ADR 0018, AGENT.md).
- Proveedores con API como puerta.

## Criterios de aceptación

### Scenario: desarrollo supera el umbral
- **Given** el pack ajustado y la partición de desarrollo regrabada
- **When** se ejecuta `benchmark run --partition dev --all-recorded`
- **Then** cada par de la puerta detecta el 100 % de lo determinista, ≥ 80 % del juicio,
  y sus tasas A y B de falsas alarmas son ≤ 20 %

### Scenario: la reservada se mide una vez
- **Given** desarrollo por encima del umbral
- **When** se mide `--partition holdout --gate threshold --measurement` con cada par
- **Then** queda una línea en `measurements.jsonl` por par y el resultado dice si v0.1 cierra

### Scenario: el ajuste no mira la reservada
- **Given** el historial de la spec
- **When** se revisa
- **Then** `measurements.jsonl` no tiene miradas a la reservada salvo la medición final

### Scenario: el cambio de pack no rompe el CI
- **Given** una versión nueva del pack
- **When** corre el job `benchmark`
- **Then** las grabaciones están ligadas a esa versión y la puerta de línea base pasa

## Notas técnicas / riesgos

- Cada iteración cuesta 21 casos × 3 repeticiones × 2 pares de envíos reales; tope de 4
  iteraciones antes de volver al usuario.
- Sobreajuste a desarrollo: la medición única de la reservada es la que cuenta.
- Las retiradas o cambios de casos de desarrollo se anotan en `retired` (ADR 0018).

## Tasks (no autoritativo — el board manda)

- [ ] Diagnóstico de las grabaciones de desarrollo
- [ ] Realismo de los perfiles (dev; reservada a ciegas)
- [ ] Pack v2
- [ ] Regrabación e iteración en desarrollo
- [ ] Medición única de la reservada

## Referencias

- docs/vision.md, «Validación del auditor»
- docs/decisions/0017-pre-auditoria.md, docs/decisions/0018-banco-defectos.md
- docs/runbooks/banco-defectos.md
