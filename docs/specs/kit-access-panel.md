---
codename: ens-compliance
title: "Kit: panel de acceso a las herramientas"
type: task
status: active
priority: P1
size: M
issue: null
prs: [75]
related_issues: [68]
depends_on: [kit-backups, kit-secrets]
supersedes: []
related_specs: [toolkit-server]
created: 2026-11-09
updated: 2026-11-11
---

# Kit: panel de acceso a las herramientas

## Contexto / problema

Herramienta del kit (`toolkit-server`, #65; ADR 0019), con el contrato que fija `kit-backups`: perfil de Compose tras Access, conector de solo lectura, checks de uso real, runbook y tarjeta en `/herramientas`. Instalar no es cumplir (regla 1).

`kit-backups` crea la primera tarjeta de `/herramientas`. Esta spec extrae el contrato común cuando hay dos herramientas (copias y claves) y completa el panel.

## Scope

**Entra:**
- Registro del kit: medida → herramienta, con lo que cubre y lo que no, y la RAM medida.
- `/herramientas`: por cada herramienta, para qué sirve en una frase, estado, medidas que apoya y «Abrir»; las no desplegadas como propuesta con «Añadir».
- Cada herramienta en su subdominio tras Access; inicio de sesión único cuando exista `kit-identity`.
- Recomendación desde la ficha de cada medida de la matriz.

**No entra:**
- Monitorización (`kit-monitoring`).

## Criterios de aceptación

### Scenario: una sola entrada al kit
- **Given** el kit con varias herramientas desplegadas
- **When** el usuario abre `/herramientas`
- **Then** ve cada una con su estado y un botón que abre su interfaz, y ninguna responde desde Internet sin pasar por Access

### Scenario: la matriz propone la herramienta
- **Given** una medida aplicable sin evidencia que tiene herramienta en el kit
- **When** se abre su ficha
- **Then** se ve la herramienta, qué cláusulas cubre y cuáles no, y el enlace para añadirla


## Notas técnicas / riesgos

- El contrato común se extrae de dos implementaciones reales, no antes.

## Tasks (no autoritativo — el board manda)

- [ ] Extraer el contrato de kit-backups y kit-secrets
- [ ] Registro medida → herramienta
- [ ] Panel y recomendación en la matriz
- [ ] Tests

## Referencias

- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
