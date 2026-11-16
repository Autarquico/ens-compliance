---
codename: ens-compliance
title: UI web con asistente
type: task
status: active
priority: P1
size: XL
issue: 29
prs: []
related_issues: [29]
depends_on: [assessment-dossier, pre-audit]
supersedes: []
related_specs: [web-ui-base, web-ui-flujo, collector-osquery]
created: 2026-10-16
updated: 2026-11-16
---

# UI web: roles, MFA comprobada, JWKS y panel de agentes

## Contexto / problema

UI única renderizada en servidor con htmx (docs/vision.md). El asistente completo
(alcance, categorización, riesgos, aplicabilidad, documentos), las vistas de matriz,
evidencias, expediente y pre-auditoría ya están entregados por `web-ui-base` (#62) y
`web-ui-flujo` (#64). El ADR 0021 sustituyó el login propio, el OIDC directo y SQLite
por Cloudflare Access. Este tramo final cierra lo que queda: hoy cualquier identidad
autenticada puede escribir y aprobar, la MFA depende solo de la política de Access, los
certificados de Access se aprovisionan a mano y no hay panel de agentes.

## Scope

**Entra:**
- **Roles.** Una asignación email → roles, editable solo por CLI (`ens-evidence ui roles`)
  y fuera de la lista blanca de escritura de la UI: nadie se concede permisos desde la UI.
  Permisos: lectura (cualquier identidad autenticada), edición (rellenar el asistente y
  la matriz) y, para cada aprobación o firma que el producto ya ata a un rol ENS
  (`Role`), tener asignado ese rol. Sin asignación, la UI es de solo lectura y explica
  cómo asignar roles; nunca concede escritura por defecto. Cada denegación queda en el log
  de auditoría.
- **MFA comprobada.** Además de la política de Access, la UI rechaza un JWT cuyo `amr` no
  acredite un segundo factor, con una página que explica cómo configurarlo. Se puede
  desactivar solo por entorno y queda advertido en la UI y en el log de arranque.
- **Refresco del JWKS.** Trabajo del scheduler en `app` que descarga los certificados del
  team de Access, los valida y los escribe de forma atómica en state (ADR 0021). Si falla,
  conserva el anterior y lo registra.
- **Panel de agentes.** Vista de solo lectura: por equipo inscrito, alta, último contacto
  (evidencia), último snapshot por consulta y resultados HOST-00x, como `agent hosts show`.
  Sin acciones (alta, revocación y consultas en vivo siguen en la CLI).

**No entra:**
- Panel de seguridad con alertas (v0.3).
- Login propio, OIDC directo y SQLite (ADR 0021).
- Altas, revocaciones o consultas en vivo desde la UI.

## Criterios de aceptación

### Scenario: asistente completo
- **Given** un usuario apañado sin conocimientos de ENS con rol de edición y los roles ENS de cada aprobación
- **When** completa el asistente
- **Then** obtiene los mismos artefactos que el flujo CLI

### Scenario: MFA
- **Given** un usuario cuyo JWT de Access no acredita segundo factor
- **When** intenta entrar
- **Then** se le niega el acceso y se le explica cómo configurarlo; no se escribe nada

### Scenario: aprobación sin el rol
- **Given** un usuario con edición pero sin el rol ENS que exige una aprobación
- **When** intenta aprobar o firmar
- **Then** se rechaza (403), no se escribe nada y queda en la auditoría

### Scenario: sin roles asignados
- **Given** una instalación sin asignación de roles
- **When** entra cualquier usuario autenticado
- **Then** la UI es de solo lectura y explica cómo asignar roles por CLI

### Scenario: JWKS caído
- **Given** que el refresco del JWKS falla
- **When** corre el trabajo
- **Then** se conserva el fichero anterior y queda registrado el error

### Scenario: panel de agentes
- **Given** un equipo con agente silencioso por encima del umbral
- **When** se abre el panel
- **Then** muestra HOST-002 en FAIL con su último contacto y no ofrece acciones

## Notas técnicas / riesgos

- Contenido exacto de `amr` en los JWT de Access sin verificar en cuenta real: depende del
  método (OTP de Access, IdP con MFA). Probar con fixtures y marcar como no verificado.
- Cambio de comportamiento al actualizar: sin roles asignados, quien antes escribía pasa a
  solo lectura hasta ejecutar `ui roles`. Documentarlo en el runbook de actualización.
- `ui` no tiene salida a Internet ni `deploy/.env`: el JWKS lo escribe `app` en state.

## Referencias

- docs/vision.md
- docs/decisions/0021-interfaz-web.md
