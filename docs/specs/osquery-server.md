---
codename: ens-compliance
title: Servidor osquery embebido
type: task
status: active
priority: P1
size: M
issue: 28
prs: [78]
related_issues: [28]
depends_on: [evidence-storage, box-packaging]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-11-12
---

# Servidor osquery embebido

## Contexto / problema

Sustituye a `osquery-edge`: la API remota de osquery (enroll, config, log, distributed read/write) como módulo de la app, detrás de caddy en `/agent/*`.

## Scope

**Entra:**
- Los cinco endpoints POST del protocolo remoto de osquery: enroll, config, log y distributed read y write. Responden siempre 200; `node_invalid: true` obliga a volver a dar de alta el equipo.
- Alta por equipo: `agent admit` emite un token de alta con caducidad para un activo inscrito (uuid y hostname).
- `node_key` = HMAC(secreto, host_identifier, generación). La revocación sube la generación.
- Un servicio `agent` aparte, de la misma imagen, en una red interna sin salida y sin los secretos de los conectores (ADR 0020).
- Resultados al storage local de evidencias, con lock no bloqueante. Con el lock ocupado se responde 503 y osquery reintenta.
- Config servida desde el repo, con las mismas consultas que `endpoint-evidence` y una plataforma por consulta.
- Último contacto por host.

**No entra:**
- Dashboard propio (lo aporta `web-ui`).

## Criterios de aceptación

### Scenario: alta válida
- **Given** un host inscrito con su token de alta vigente
- **When** llama a enroll
- **Then** recibe node_key

### Scenario: alta inválida
- **Given** token erróneo, node_key revocado o de otra generación
- **When** llama a cualquier endpoint
- **Then** recibe `node_invalid: true` y no se escribe nada

### Scenario: token de alta caducado
- **Given** un host inscrito cuyo token de alta ha caducado
- **When** llama a enroll
- **Then** recibe `node_invalid: true` y no se escribe nada

### Scenario: logs de estado
- **Given** un host válido
- **When** envía `log_type: status`
- **Then** recibe 200 y no se guarda nada

### Scenario: lock ocupado
- **Given** un ciclo o una copia con el lock del workspace tomado
- **When** un host envía resultados
- **Then** recibe 503, no se escribe nada y el reintento posterior se importa una sola vez

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
