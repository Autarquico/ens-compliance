---
codename: ens-compliance
title: "UI web: base con Cloudflare Access, alcance y categorización"
type: task
status: active
priority: P1
size: L
issue: 62
prs: []
related_issues: [62]
depends_on: [osquery-server]
supersedes: []
related_specs: [web-ui]
created: 2026-11-08
updated: 2026-11-08
---

# UI web: base con Cloudflare Access, alcance y categorización

## Contexto / problema

Primer tramo de `web-ui` (#29). La UI se renderiza en el servidor con htmx y se monta sobre la base HTTP de `osquery-server` (Starlette, ADR 0020).

Decisión del usuario (2026-11-08): la UI autentica con Cloudflare Access. La app verifica el JWT de Access en cada petición, y la política de Access exige MFA. No hay contraseñas, segundo factor ni sesiones propias.

Diseño: `.claude/design-briefs/base.yml` (editorial sobria, acento azul institucional, de tú).

## Scope

**Entra:**
- Servicio `ui` de la misma imagen, en una red interna sin salida y sin los secretos de los conectores. Caddy lo separa del de agentes por hostname.
- Verificación del JWT de Access. Los certificados del team los refresca el scheduler de `app`, que sí tiene salida.
- Asistente de alcance y categorización, que escribe por las mismas APIs de producto que la CLI. Se añaden las APIs de edición y sus comandos CLI donde hoy se edita YAML a mano.
- Vistas de solo lectura: estado, matriz, evidencias (solo manifiestos), expediente y pre-auditoría.
- Lista blanca de lo que la UI puede escribir.

**No entra:**
- Riesgos, aplicabilidad, documentos y expediente en el asistente (tramos siguientes de `web-ui`).
- Login propio, OIDC directo y SQLite.
- Panel de agentes.

## Criterios de aceptación

### Scenario: sin Access no se entra
- **Given** una petición sin JWT de Access, con un JWT caducado o firmado por otra clave
- **When** llega a cualquier ruta de la UI
- **Then** recibe 401 y no se escribe nada

### Scenario: MFA exigido por Access
- **Given** la política de Access del hostname de la UI
- **When** se sigue el runbook de despliegue
- **Then** la política exige MFA o un IdP con MFA, y el runbook explica cómo comprobarlo

### Scenario: alcance y categorización equivalentes a la CLI
- **Given** el mismo guion de alcance y categorización y el mismo reloj
- **When** se completa por la UI y por la CLI en dos workspaces
- **Then** los ficheros del workspace y sus huellas coinciden

### Scenario: la UI no toca la configuración
- **Given** un usuario autenticado
- **When** intenta escribir fuera de la lista blanca (por ejemplo `connectors/`)
- **Then** se rechaza y no se escribe nada

## Notas técnicas / riesgos

- Review de seguridad obligatoria: es la superficie web y lee todas las evidencias.
- El texto nunca dice «certifica», «garantiza el cumplimiento» ni «cumple el ENS».

## Tasks (no autoritativo — el board manda)

- [ ] ADR 0021
- [ ] APIs de edición y CLI
- [ ] Base de la UI y Access
- [ ] Plantillas y estilos
- [ ] Asistente y vistas
- [ ] Caja
- [ ] Tests

## Referencias

- docs/specs/web-ui.md
- .claude/loop/web-ui/plan.md
