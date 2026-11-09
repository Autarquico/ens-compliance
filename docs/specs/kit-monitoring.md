---
codename: ens-compliance
title: "Kit: panel de monitorización"
type: task
status: active
priority: P2
size: L
issue: null
prs: []
related_issues: [72]
depends_on: [kit-access-panel]
supersedes: []
related_specs: [toolkit-server]
created: 2026-11-09
updated: 2026-11-09
---

# Kit: panel de monitorización

## Contexto / problema

Herramienta del kit (`toolkit-server`, #65; ADR 0019), con el contrato que fija `kit-backups`: perfil de Compose tras Access, conector de solo lectura, checks de uso real, runbook y tarjeta en `/herramientas`. Instalar no es cumplir (regla 1).

Una página con la salud de todo lo desplegado, sin Grafana ni servicios extra (visión, «Fuera a propósito»). Es operación, no cumplimiento: un verde no marca ninguna medida.

## Scope

**Entra:**
- `/monitorizacion`, alimentado por el scheduler de la app.
- Por herramienta: responde o no, versión y actualización de seguridad pendiente, y su dato clave.
- Del VPS (por osquery): CPU, memoria, disco, certificados TLS próximos a caducar y actualizaciones del sistema pendientes.
- De la caja: últimas ejecuciones del scheduler, agentes que han dejado de reportar y copia externa al día.
- Histórico de 30 días y avisos por correo o en la web.
- Sin socket de Docker en la app: salud por HTTP y osquery; si hace falta el estado de los contenedores, un proxy de solo lectura del socket.

**No entra:**
- Métricas de aplicaciones de negocio de la pyme.
- Detección de intrusiones (`detection-profile`).

## Criterios de aceptación

### Scenario: el panel dice qué falla
- **Given** una herramienta caída y el disco del VPS por encima del umbral
- **When** el usuario abre `/monitorizacion`
- **Then** ve las dos cosas en rojo, desde cuándo y con el enlace a su runbook, recibe el aviso, y ninguna medida de la matriz cambia por el panel

### Scenario: sin root para la app
- **Given** la app en marcha
- **When** se inspecciona su contenedor
- **Then** no tiene montado el socket de Docker


## Notas técnicas / riesgos

- El envío de avisos por correo necesita un SMTP configurado por la pyme; sin él, solo en la web.

## Tasks (no autoritativo — el board manda)

- [ ] Recogida de salud por herramienta, VPS y caja
- [ ] Histórico y umbrales
- [ ] Avisos
- [ ] Página y tests

## Referencias

- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
