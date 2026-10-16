---
codename: ens-compliance
title: Caja mínima en Docker Compose
type: task
status: active
priority: P0
size: M
issue: 27
prs: []
related_issues: [27]
depends_on: [repo-foundation, reports-scheduling, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Caja mínima en Docker Compose

## Contexto / problema

Caja de v0.1: caddy + app en Docker Compose, con el scheduler dentro de la app (docs/vision.md, Arquitectura).

## Scope

**Entra:**
- `compose.yaml` con imágenes de versión fijada; volúmenes de datos.
- Script de instalación y actualización.
- Runbook: publicación con Cloudflare Tunnel, backup y rollback.
- La caja inscrita como activo del alcance de Autarqui.

**No entra:**
- Instalador de un comando para terceros (v0.2).
- Bundle offline (v0.3).

## Criterios de aceptación

### Scenario: arranque limpio
- **Given** una VM con Docker
- **When** se ejecuta el script de instalación
- **Then** la caja arranca y el scheduler ejecuta un ciclo

### Scenario: UI no pública
- **Given** la caja publicada con Tunnel
- **When** se accede desde Internet a rutas no permitidas
- **Then** se deniega

### Scenario: rollback
- **Given** una actualización fallida
- **When** se sigue el runbook
- **Then** se vuelve a la versión anterior sin perder datos

## Notas técnicas / riesgos

- Presupuesto de recursos a medir aquí por primera vez.

## Tasks (no autoritativo — el board manda)

- [ ] compose.yaml
- [ ] Script
- [ ] Runbook
- [ ] Medición de recursos

## Referencias

- docs/vision.md
