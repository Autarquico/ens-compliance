---
codename: ens-compliance
title: "Fundación del repo y modelos"
type: task
status: draft
priority: P0
size: L
issue: null
prs: []
related_issues: []
depends_on: []
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Fundación del repo y modelos

## Contexto / problema

Primera iteración del "prompt maestro" (docs/ens-compliance-guide.md:981-1070): estructura del
repositorio, paquete Python `ens_evidence`, modelos de datos de los contratos
(collector, check, manifiesto de evidencia), CLI básica, tests, calidad y
decisiones documentadas. Sin integraciones reales.

## Scope

**Entra:**
- Estructura de directorios de docs/ens-compliance-guide.md:186-255, adaptada (sin `collectors/fleet.py`; añade `osquery`).
- `pyproject.toml` (Python 3.12+, Pydantic, Typer, HTTPX, Pytest, Ruff), `.gitignore`, `.env.example`, `.pre-commit-config.yaml`.
- `CLAUDE.md` del proyecto con las reglas no negociables (docs/ens-compliance-guide.md:261-303).
- Modelos Pydantic de los contratos de docs/ens-compliance-guide.md:650-701 y enums de estado.
- CLI Typer con los comandos de docs/ens-compliance-guide.md:790-804 como stubs que fallan explícitamente (`not implemented`).
- CI en GitHub Actions: tests, lint, validación de YAML/esquemas.
- ADRs en `docs/decisions/`: stack, estados, R2, osquery-edge en lugar de Fleet.
- Añadir `.claude/loop/` al `.gitignore`.

**No entra:**
- Integraciones con APIs, despliegues o automatizaciones con credenciales (docs/ens-compliance-guide.md:1053-1060).

## Criterios de aceptación

### Scenario: estados unificados
- **Given** los tres conjuntos de estados de la guía (274, 361-368, 511-517)
- **When** se define el modelo
- **Then** existe un único enum por eje (recogida, check, aplicabilidad, evaluación) documentado en un ADR y aprobado por el usuario

### Scenario: ausencia de evidencia no es PASS
- **Given** un check sin evidencias asociadas
- **When** se construye el resultado
- **Then** la validación del modelo impide `PASS` sin `evidence_ids`

### Scenario: PASS técnico no implica COMPLIANT
- **Given** una medida cuyos checks están todos en PASS
- **When** se calcula su evaluación
- **Then** queda en `REQUIRES_REVIEW` hasta revisión humana

### Scenario: calidad verde
- **Given** el repo recién creado
- **When** se ejecutan `pytest` y `ruff check` en local y en CI
- **Then** ambos pasan sin credenciales de producción

## Notas técnicas / riesgos

- Track heavy previsto: multi-archivo y con la spec activa.
- Los contratos JSON de la guía son ejemplos: el `schema_version` arranca en 1.0.

## Tasks (no autoritativo — el board manda)

- [ ] Decidir y documentar enums de estado
- [ ] Scaffolding + pyproject
- [ ] Modelos + tests
- [ ] CLI stubs
- [ ] CI
- [ ] ADRs

## Referencias

- docs/ens-compliance-guide.md:151-303
- docs/ens-compliance-guide.md:630-704
- docs/ens-compliance-guide.md:981-1070
