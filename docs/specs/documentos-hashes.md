---
codename: ens-compliance
title: UI — Origin en los POST del navegador y huellas que parten línea
type: bug
status: active
priority: P0
size: S
issue: 101
prs: []
related_issues: [101]
depends_on: [web-ui-flujo, ui-documentos]
supersedes: []
related_specs: [web-ui-flujo, ui-documentos]
created: 2026-11-19
updated: 2026-11-19
---

# UI — Origin en los POST del navegador y huellas que parten línea

## Contexto / problema

Con `Referrer-Policy: no-referrer` (`ui/security.py`, ADR 0021) un navegador envía
`Origin: null` en todo POST de formulario, y `OriginCheckMiddleware` responde 403: ninguna
escritura de la web funciona en un navegador real. Los tests fijan `Origin` a mano y no lo
ven. Además, en `/documentos` las huellas sha256 no parten línea y la tabla desborda a 1280 px.

## Scenarios

### Escenario 1 — un formulario de la web escribe desde un navegador
- **Given** la interfaz servida en su origen
- **When** se pulsa «Generar los documentos» en Chrome
- **Then** el navegador envía `Origin` igual al de la interfaz y la respuesta es 303, no 403

### Escenario 2 — la tabla de documentos cabe a 1280 px
- **Given** seis documentos generados
- **When** se abre `/documentos` a 1280 px
- **Then** la tabla no desborda su contenedor y las huellas se leen completas

## Cambio

`Referrer-Policy: same-origin` (sin referer hacia otros sitios); ADR 0021 enmendado con el
motivo. Clase `.hash` con `overflow-wrap: anywhere` en las huellas del listado.
