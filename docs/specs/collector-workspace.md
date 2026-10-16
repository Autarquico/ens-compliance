---
codename: ens-compliance
title: Collector de Google Workspace
type: task
status: active
priority: P0
size: M
issue: 7
prs: []
related_issues: [7]
depends_on: [repo-foundation, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de Google Workspace

## Contexto / problema

Collector read-only de Google Workspace según docs/ens-compliance-guide.md:496-520.

## Scope

**Entra:**
- Usuarios, suspendidos, administradores, 2SV, recuperación, OAuth apps, eventos administrativos.
- Scopes de lectura limitados; sin privilegios administrativos generales.

**No entra:**
- Cambios de configuración en Workspace.

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

### Scenario: usuario sin 2SV
- **Given** un usuario activo sin 2SV
- **When** se evalúan los checks
- **Then** el check de MFA da `FAIL` identificando al usuario

## Notas técnicas / riesgos

- Validar qué datos ofrece la edición de Workspace contratada.

## Tasks (no autoritativo — el board manda)

- [ ] Cuenta de servicio y scopes
- [ ] Modelos
- [ ] Checks
- [ ] Fixtures + tests

## Referencias

- docs/ens-compliance-guide.md:496-520
