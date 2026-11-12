---
codename: ens-compliance
title: "Guía: Cloudflare Access con el proveedor de identidad de la pyme"
type: task
status: active
priority: P2
size: M
issue: null
prs: []
related_issues: [77]
depends_on: [collector-cloudflare]
supersedes: [kit-identity]
related_specs: [toolkit-server, setup-guides, kit-access-panel]
created: 2026-11-12
updated: 2026-11-12
---

# Guía: Cloudflare Access con el proveedor de identidad de la pyme

## Contexto / problema

Sustituye a `kit-identity` (archivada el 2026-11-12, decisión del usuario): la caja no lleva proveedor de identidad propio. La web de la caja y las herramientas del kit ya se publican detrás de Cloudflare Access (`toolkit-server`, ADR 0019). El inicio de sesión y el doble factor los pone el proveedor que la pyme ya usa, Google Workspace o Microsoft 365 / Entra ID, configurado como proveedor de identidad de Access.

Lo que falta es la guía para configurarlo y la evidencia de que se hizo: que las aplicaciones de Access de la caja solo admiten ese proveedor, sin código de un solo uso por correo ni reglas que dejen entrar a cualquiera. Instalar no es cumplir (regla 1): la guía no marca ninguna medida por sí sola.

## Scope

**Entra:**
- Guía en español, con fecha de verificación, para dar de alta Workspace o Entra ID como proveedor de identidad de Access (OIDC o SAML, a verificar cuál recomienda Cloudflare para cada uno) y exigirlo en las aplicaciones de la caja y del kit.
- Doble factor: la guía remite a forzarlo en el proveedor (Workspace o Entra ID), que es donde se evidencia (`collector-workspace`, `collector-m365`).
- Conector Cloudflare: nueva sección de solo lectura con los proveedores de identidad de la cuenta (tipo y nombre, sin secretos de cliente) y, en las políticas ya recogidas, qué métodos de acceso admiten.
- Check: las aplicaciones de Access que publican hosts de la caja o del kit solo admiten el proveedor de la pyme. Qué cláusulas de op.acc.6 apoya se decide leyendo el catálogo, en duda «parcial» o «no cubre».
- Acceso de emergencia documentado en el runbook: qué hacer si el proveedor cae, registrado y revisado, sin dejar la web abierta.

**No entra:**
- Proveedor de identidad dentro de la caja (Authentik, Keycloak): descartado.
- Federar las herramientas del kit contra el proveedor por OIDC dentro de su propia interfaz: cada una mantiene su inicio de sesión; Access va delante.
- Roles finos de la web (tramo 4 de `web-ui`).

## Criterios de aceptación

### Scenario: aplicación con código por correo
- **Given** una aplicación de Access que publica un host de la caja y admite el código de un solo uso por correo
- **When** corre el check
- **Then** da FAIL con el nombre de la aplicación y el método admitido, y la guía dice cómo quitarlo

### Scenario: sin proveedor configurado
- **Given** una cuenta de Cloudflare sin proveedores de identidad o sin permiso para leerlos
- **When** corre el check
- **Then** el resultado es desconocido (nunca PASS) y dice qué falta

### Scenario: acceso de emergencia
- **Given** el proveedor de identidad caído
- **When** se necesita entrar en la web de la caja
- **Then** el runbook da un acceso de emergencia registrado y revisado, sin dejar la web abierta

## Notas técnicas / riesgos

- Sin verificar: el endpoint y el permiso de solo lectura para listar proveedores de identidad de Access, y cómo expone la política los métodos admitidos (`login_method`, `auth_method`).
- Sin verificar: si Access puede exigir que el proveedor haya pedido doble factor (AMR) para Workspace y para Entra ID.
- El conector ya lee aplicaciones, grupos y políticas de Access (`src/ens_evidence/connectors/cloudflare/sections.py:299-365`).
- Review de seguridad obligatoria.

## Tasks (no autoritativo — el board manda)

- [ ] Verificar la API de proveedores de identidad y métodos de las políticas
- [ ] Sección del conector y check
- [ ] Guía Workspace y Entra ID
- [ ] Runbook de acceso de emergencia y tests

## Referencias

- docs/specs/archive/2026-11-12-kit-identity.md
- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
- docs/specs/setup-guides.md
