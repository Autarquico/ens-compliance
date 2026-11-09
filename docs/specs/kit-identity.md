---
codename: ens-compliance
title: "Kit: identidad y doble factor (Authentik o Keycloak)"
type: task
status: active
priority: P2
size: L
issue: null
prs: []
related_issues: [70]
depends_on: [kit-backups]
supersedes: []
related_specs: [toolkit-server]
created: 2026-11-09
updated: 2026-11-09
---

# Kit: identidad y doble factor (Authentik o Keycloak)

## Contexto / problema

Herramienta del kit (`toolkit-server`, #65; ADR 0019), con el contrato que fija `kit-backups`: perfil de Compose tras Access, conector de solo lectura, checks de uso real, runbook y tarjeta en `/herramientas`. Instalar no es cumplir (regla 1).

Un proveedor de identidad con doble factor apoya op.acc.6 (refuerzos de doble factor) y op.acc.5 solo si hay usuarios externos, y solo para los servicios que federan contra él. También da el inicio de sesión único de la web de la caja y del resto del kit.

## Scope

**Entra:**
- Decisión Authentik o Keycloak, con medida de RAM y mantenimiento, en un ADR.
- Perfil `identidad`, doble factor obligatorio por política.
- OIDC para la web de la caja y para las herramientas del kit que lo admitan.
- Conector: usuarios activos, doble factor, altas y bajas, y qué servicios federan.
- Checks condicionados a los servicios federados.
- Runbook `kit-identidad.md`, incluida la recuperación si cae el proveedor (acceso de emergencia).

**No entra:**
- Federar los SaaS de la pyme (Workspace, M365): guía, no automatización.
- Roles finos de la web (tramo 4 de `web-ui`).

## Criterios de aceptación

### Scenario: solo cuenta lo federado
- **Given** un servicio del alcance que no federa contra el proveedor
- **When** corre el check de doble factor
- **Then** ese servicio no recibe evidencia del proveedor y el check lo dice

### Scenario: acceso de emergencia
- **Given** el proveedor de identidad caído
- **When** se necesita entrar en la web de la caja
- **Then** el runbook da un acceso de emergencia registrado y revisado, sin dejar la web abierta


## Notas técnicas / riesgos

- Si la web de la caja depende del proveedor y este cae, el usuario se queda fuera: hace falta el acceso de emergencia.
- Review de seguridad obligatoria.

## Tasks (no autoritativo — el board manda)

- [ ] ADR Authentik vs Keycloak
- [ ] Perfil y política de doble factor
- [ ] OIDC de la caja y del kit
- [ ] Conector y checks
- [ ] Runbook y tests

## Referencias

- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
- https://goauthentik.io/
- https://www.keycloak.org/
