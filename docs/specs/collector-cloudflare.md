---
codename: ens-compliance
title: Collector de Cloudflare
type: task
status: active
priority: P0
size: M
issue: 6
prs: []
related_issues: [6]
depends_on: [repo-foundation, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de Cloudflare

## Contexto / problema

Collector read-only de Cloudflare según docs/ens-compliance-guide.md:452-492.

## Scope

**Entra:**
- Zonas, DNS, TLS, WAF, Access applications y policies, Tunnels, audit logs.
- API token con privilegio mínimo; nunca Global API Key.
- Check: hostname servido por Tunnel sin aplicación Access → hallazgo.

**No entra:**
- Cambios en Cloudflare ni migración a Terraform.

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

### Scenario: Tunnel sin Access
- **Given** un hostname publicado por Tunnel sin aplicación Access
- **When** se evalúan los checks
- **Then** el check correspondiente da `FAIL` (Tunnel no implica autenticación)

## Notas técnicas / riesgos

- Incluye el hostname de la caja (`/agent/*` público, UI privada) y el de cualquier UI de administración.

## Tasks (no autoritativo — el board manda)

- [ ] Modelos
- [ ] Cliente + paginación
- [ ] Checks
- [ ] Fixtures + tests
- [ ] Doc de permisos

## Referencias

- https://developers.cloudflare.com/
- docs/ens-compliance-guide.md:452-492
