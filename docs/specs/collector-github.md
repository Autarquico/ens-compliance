---
codename: ens-compliance
title: Collector de GitHub
type: task
status: active
priority: P0
size: M
issue: null
prs: []
related_issues: [5]
depends_on: [repo-foundation, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de GitHub

## Contexto / problema

Collector read-only de GitHub según docs/ens-compliance-guide.md:398-448.

## Scope

**Entra:**
- GitHub App o token read-only; permisos mínimos documentados.
- Paginación; visibilidad, ramas, rulesets, Dependabot, secret scanning, colaboradores, entornos, tokens/apps.
- Salida normalizada como en docs/ens-compliance-guide.md:415-427 y checks asociados.

**No entra:**
- Cambios de configuración en GitHub.

## Criterios de aceptación

### Scenario: credencial revocada
- **Given** un token de la fuente revocado o inválido
- **When** se ejecuta el collector
- **Then** el resultado es `collection_status: ERROR` y ningún check derivado da `PASS`

### Scenario: permiso insuficiente frente a configuración inexistente
- **Given** un recurso al que el token no tiene acceso
- **When** se ejecuta el collector
- **Then** el dato queda como `PERMISSION_DENIED`/`UNKNOWN`, distinto de "no configurado"

### Scenario: sin secretos en la salida
- **Given** una ejecución con logging en nivel debug
- **When** se inspeccionan logs y evidencia normalizada
- **Then** no aparece ningún token, clave ni cabecera de autorización

### Scenario: función no disponible en el plan
- **Given** un repo sin secret scanning disponible
- **When** se recoge su configuración
- **Then** el campo queda `unknown`/`NOT_SUPPORTED`, nunca `enabled`

## Notas técnicas / riesgos

- Tests solo con fixtures; nunca credenciales reales en unit tests.

## Tasks (no autoritativo — el board manda)

- [ ] Modelos
- [ ] Cliente HTTPX + paginación
- [ ] Checks
- [ ] Fixtures + tests
- [ ] Doc de permisos

## Referencias

- https://docs.github.com/
- docs/ens-compliance-guide.md:398-448
