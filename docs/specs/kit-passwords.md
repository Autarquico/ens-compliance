---
codename: ens-compliance
title: "Kit: gestor de contraseñas del equipo con Vaultwarden"
type: task
status: active
priority: P2
size: M
issue: 69
prs: [76]
related_issues: [69]
depends_on: [kit-backups]
supersedes: []
related_specs: [toolkit-server]
created: 2026-11-09
updated: 2026-11-12
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
- **Then** da FAIL con el usuario (sin datos personales en el texto: identificador interno) y la tarjeta sale «con problemas»

### Scenario: la app no ve cofres
- **Given** el acceso de la app
- **When** consulta Vaultwarden
- **Then** solo obtiene usuarios y estado de doble factor, nunca elementos de los cofres


## Notas técnicas / riesgos

- **Vaultwarden 1.37.5 (AGPL-3.0), verificado el 2026-11-11 en su repositorio:** ninguna API da el estado del doble factor con una credencial que solo lea.
  - `GET /admin/users` (`src/api/admin.rs`) devuelve `twoFactorEnabled` y `lastActive`, pero exige el `ADMIN_TOKEN`, que también borra usuarios, quita el doble factor y cambia la configuración.
  - `GET /api/organizations/<org>/users` exige un gestor con acceso total, que también gestiona los miembros.
  - La API pública solo tiene `POST /public/organization/import`, que escribe.
- **Decisión (usuario, 2026-11-11): exportador aparte.**
  - Un contenedor del perfil monta la base SQLite de Vaultwarden y vuelca a un volumen compartido un JSON por usuario: id interno, si está activo, doble factor y última actividad.
  - La app solo lee ese JSON. No tiene credenciales de Vaultwarden ni ve la base, que contiene los cofres.
  - El panel de administración queda desactivado (sin `ADMIN_TOKEN`) tras el alta.
- **Qué cuenta como doble factor.**
  - Vaultwarden marca `twoFactorEnabled` con cualquier fila de `twofactor` con `atype < 1000`, sin mirar `enabled`. Eso incluye «recordar dispositivo» (5) y el código de recuperación (8).
  - El check es más estricto: cuenta solo las filas con `enabled` y un método real (0 TOTP, 1 email, 2 Duo, 3 YubiKey, 4 U2F, 6 Duo de la organización, 7 WebAuthn).
  - La última actividad es el `updated_at` más reciente de `devices`.
- **Sin verificar:**
  - leer SQLite en modo WAL con el montaje en solo lectura (el `-shm` necesita escritura salvo con `immutable=1`);
  - la retención de `devices` cuando se borra un dispositivo.
- Review de seguridad obligatoria.

## Tasks (no autoritativo — el board manda)

- [ ] Verificar API de Vaultwarden
- [ ] Perfil y alta
- [ ] Conector y checks
- [ ] Runbook y tests

## Referencias

- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
- https://github.com/dani-garcia/vaultwarden
