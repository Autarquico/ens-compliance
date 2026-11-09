---
codename: ens-compliance
title: "Kit: gestor de contraseñas del equipo con Vaultwarden"
type: task
status: draft
priority: P2
size: M
issue: null
prs: []
related_issues: []
depends_on: [kit-backups]
supersedes: []
related_specs: [toolkit-server]
created: 2026-11-09
updated: 2026-11-09
---

# Kit: gestor de contraseñas del equipo con Vaultwarden

## Contexto / problema

Herramienta del kit (`toolkit-server`, #65; ADR 0019), con el contrato que fija `kit-backups`: perfil de Compose tras Access, conector de solo lectura, checks de uso real, runbook y tarjeta en `/herramientas`. Instalar no es cumplir (regla 1).

Un gestor de contraseñas no es el mecanismo de autenticación del sistema (op.acc.6). Apoya en parte op.acc.6.4 (cambio periódico) y su refuerzo R1 (complejidad). Vaultwarden es compatible con los clientes de Bitwarden (licencia a verificar).

## Scope

**Entra:**
- Perfil `contrasenas` con Vaultwarden, registro cerrado (solo por invitación) y panel de administración desactivado tras el alta.
- Conector con la API de administración o de organización (a verificar): usuarios, doble factor activado, cuentas sin uso.
- Checks: todos los usuarios con doble factor, sin cuentas inactivas por encima del umbral de la política.
- Copia en el plan de `kit-backups`.
- Runbook `kit-contrasenas.md`.

**No entra:**
- Leer contraseñas o cofres: nunca.
- Sustituir el mecanismo de autenticación de los sistemas (`kit-identity`).

## Criterios de aceptación

### Scenario: doble factor obligatorio
- **Given** un usuario sin doble factor
- **When** corre el check
- **Then** da FAIL con el usuario (sin datos personales en el texto: identificador interno) y la tarjeta sale en amarillo

### Scenario: la app no ve cofres
- **Given** el acceso de la app
- **When** consulta Vaultwarden
- **Then** solo obtiene usuarios y estado de doble factor, nunca elementos de los cofres


## Notas técnicas / riesgos

- Sin verificar: qué API expone Vaultwarden para leer el estado de doble factor sin privilegios de administración plena.
- Review de seguridad obligatoria.

## Tasks (no autoritativo — el board manda)

- [ ] Verificar API de Vaultwarden
- [ ] Perfil y alta
- [ ] Conector y checks
- [ ] Runbook y tests

## Referencias

- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
- https://github.com/dani-garcia/vaultwarden
