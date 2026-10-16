---
codename: ens-compliance
title: Fundación del repo y modelos
type: task
status: active
priority: P0
size: L
issue: 2
prs: []
related_issues: [2]
depends_on: []
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Fundación del repo y modelos

## Contexto / problema

Base del producto: estructura del repo, paquete Python `ens_evidence`,
modelos de datos, interfaz de conectores como plugins, CLI, calidad y
decisiones documentadas. Sin integraciones reales.

## Scope

**Entra:**
- `LICENSE` AGPL-3.0 y aviso legal: el producto prepara, no certifica.
- `pyproject.toml` (Python 3.12+, Pydantic, Typer, HTTPX, Pytest, Ruff), `.gitignore` (incl. `.claude/loop/`), `.env.example`, pre-commit.
- `CLAUDE.md` del proyecto con las reglas no negociables (docs/ens-compliance-guide.md:269-282).
- Modelos Pydantic: contratos de collector, check y manifiesto de evidencia; enums de estado unificados; estados del expediente y regla de preparación (`READY`/`NOT READY`).
- Interfaz de conector (plugin): descubrimiento, configuración, recogida de solo lectura.
- CLI Typer con comandos stub que fallan explícitamente.
- CI: tests, lint, validación de YAML y esquemas.
- ADRs: stack, estados, conectores como plugins, caja con Compose, SQLite diferido a v0.2.

**No entra:**
- Integraciones con APIs, despliegues o credenciales.
- UI web (v0.2).

## Criterios de aceptación

### Scenario: estados unificados
- **Given** los conjuntos de estados de la guía (docs/ens-compliance-guide.md:274, 361-368, 511-517)
- **When** se define el modelo
- **Then** existe un único enum por eje documentado en un ADR y aprobado por el usuario

### Scenario: ausencia de evidencia no es PASS
- **Given** un check sin evidencias asociadas
- **When** se construye el resultado
- **Then** la validación del modelo impide `PASS` sin `evidence_ids`

### Scenario: PASS técnico no implica cumplimiento
- **Given** una medida cuyos checks están en PASS pero sin revisión registrada
- **When** se calcula su estado
- **Then** no cuenta como satisfecha para la regla de preparación

### Scenario: conector de ejemplo
- **Given** un conector ficticio que implementa la interfaz
- **When** se registra y se ejecuta en tests
- **Then** el núcleo lo descubre y procesa sin cambios

### Scenario: calidad verde
- **Given** el repo recién creado
- **When** se ejecutan `pytest` y `ruff check` en local y en CI
- **Then** ambos pasan sin credenciales

## Notas técnicas / riesgos

- Track heavy previsto: multi-archivo, spec activa.

## Tasks (no autoritativo — el board manda)

- [ ] ADR de estados (aprobación del usuario)
- [ ] Scaffolding + licencia
- [ ] Modelos + tests
- [ ] Interfaz de conectores
- [ ] CLI stubs
- [ ] CI
- [ ] ADRs

## Referencias

- docs/vision.md
- docs/ens-compliance-guide.md:151-303
- docs/ens-compliance-guide.md:630-704
