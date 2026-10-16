---
codename: ens-compliance
title: Servidor osquery embebido
type: task
status: active
priority: P1
size: M
issue: null
prs: []
related_issues: [28]
depends_on: [evidence-storage, box-packaging]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Servidor osquery embebido

## Contexto / problema

Sustituye a `osquery-edge`: la API remota de osquery (enroll, config, log, distributed read/write) como módulo de la app, detrás de caddy en `/agent/*`.

## Scope

**Entra:**
- Cinco endpoints POST.
- `node_key` = HMAC(secreto, host_identifier); revocación.
- Resultados al storage local de evidencias.
- Config servida desde el repo (mismas consultas que `endpoint-evidence`).
- Último contacto por host.

**No entra:**
- Dashboard propio (lo aporta `web-ui`).

## Criterios de aceptación

### Scenario: alta válida
- **Given** un host con el enroll_secret correcto
- **When** llama a enroll
- **Then** recibe node_key

### Scenario: alta inválida
- **Given** secreto erróneo o node_key revocado
- **When** llama a cualquier endpoint
- **Then** recibe `node_invalid: true` y no se escribe nada

### Scenario: consulta en vivo
- **Given** una consulta encolada
- **When** el host la recoge y responde
- **Then** el resultado queda disponible para la CLI y la UI

## Notas técnicas / riesgos

- Endpoint expuesto a Internet: review de seguridad obligatoria.
- Worker de Cloudflare como opción futura.

## Tasks (no autoritativo — el board manda)

- [ ] Endpoints
- [ ] HMAC + revocación
- [ ] Storage
- [ ] Tests de protocolo

## Referencias

- https://osquery.readthedocs.io/en/stable/deployment/remote/
