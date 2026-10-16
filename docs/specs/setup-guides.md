---
codename: ens-compliance
title: Guías de servicios externos
type: task
status: active
priority: P1
size: L
issue: 31
prs: []
related_issues: [31]
depends_on: [collector-m365]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Guías de servicios externos

## Contexto / problema

Guías paso a paso en español, con capturas, para lo que no va dentro de la caja: Google Workspace, Microsoft 365 / Entra ID, GitHub, Cloudflare y el proveedor S3 de la copia.

## Scope

**Entra:**
- Una guía por servicio que crea credenciales de solo lectura con privilegio mínimo.
- Prueba de conexión y detección de privilegios sobrantes desde la UI.
- Fecha de revisión de cada guía y proceso para detectar guías desactualizadas.

**No entra:**
- Soporte personalizado.

## Criterios de aceptación

### Scenario: guía M365
- **Given** una pyme sin conocimiento de Entra ID
- **When** sigue la guía y pega las credenciales
- **Then** la conexión funciona y se confirma el solo lectura

### Scenario: privilegios de más
- **Given** credenciales con permisos de escritura
- **When** se prueba la conexión
- **Then** la UI avisa y recomienda reducirlos

## Notas técnicas / riesgos

- Las consolas cambian: cada guía lleva fecha de verificación.

## Tasks (no autoritativo — el board manda)

- [ ] Workspace
- [ ] M365
- [ ] GitHub
- [ ] Cloudflare
- [ ] S3
- [ ] Prueba de conexión

## Referencias

- docs/vision.md
