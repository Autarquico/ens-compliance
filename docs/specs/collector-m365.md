---
codename: ens-compliance
title: Conector de Microsoft 365
type: task
status: active
priority: P1
size: M
issue: null
prs: []
related_issues: [30]
depends_on: [repo-foundation, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Conector de Microsoft 365

## Contexto / problema

Conector de solo lectura para Microsoft 365 / Entra ID: usuarios, administradores, MFA, aplicaciones y eventos administrativos.

## Scope

**Entra:**
- Registro de aplicación con permisos de solo lectura mínimos (documentados en `setup-guides`).
- Checks equivalentes a los de Workspace.

**No entra:**
- Cambios en el tenant.

## Criterios de aceptación

### Scenario: credencial revocada
- **Given** un token de la fuente revocado o inválido
- **When** se ejecuta el collector
- **Then** el resultado es `ERROR` y ningún check derivado da `PASS`

### Scenario: permiso insuficiente frente a configuración inexistente
- **Given** un recurso al que el token no tiene acceso
- **When** se ejecuta el collector
- **Then** el dato queda como `PERMISSION_DENIED`/`UNKNOWN`, distinto de "no configurado"

### Scenario: sin secretos en la salida
- **Given** una ejecución con logging en nivel debug
- **When** se inspeccionan logs y evidencia normalizada
- **Then** no aparece ningún token, clave ni cabecera de autorización

### Scenario: usuario sin MFA
- **Given** un usuario activo sin MFA
- **When** se evalúan los checks
- **Then** el check de MFA da `FAIL`

## Notas técnicas / riesgos

- Sin verificar: qué datos exige licencia superior a la básica.

## Tasks (no autoritativo — el board manda)

- [ ] Modelos
- [ ] Cliente
- [ ] Checks
- [ ] Fixtures

## Referencias

- https://learn.microsoft.com/graph/
