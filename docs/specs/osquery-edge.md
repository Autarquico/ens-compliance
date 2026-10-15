---
codename: ens-compliance
title: Servidor osquery en Cloudflare Workers
type: task
status: active
priority: P1
size: M
issue: null
prs: []
related_issues: [8]
depends_on: [evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Servidor osquery en Cloudflare Workers

## Contexto / problema

Sustituye a Fleet (docs/ens-compliance-guide.md:524-563). Un Worker implementa la API remota de
osquery (enroll, config, log, distributed read/write) y escribe los resultados
en R2 mediante binding, sin repartir credenciales de storage a los hosts. Sin
dashboard: la consulta se hace con informes y CLI.

## Scope

**Entra:**
- Endpoints POST: enroll, config, log, distributed/read, distributed/write.
- `node_key` = HMAC(secreto del Worker, host_identifier); lista de revocación; sin base de datos.
- Escritura de resultados en R2 bajo `raw/osquery/<host>/`, con bucket lock.
- Config (agenda de consultas) versionada en el repo y servida por `config`.
- Registro de último contacto por host (para HOST-002).
- Despliegue con wrangler versionado en `infrastructure/`.

**No entra:**
- Dashboard o UI.
- Compatibilidad con Fleet.

## Criterios de aceptación

### Scenario: alta válida
- **Given** un host con el enroll_secret correcto
- **When** llama a enroll
- **Then** recibe un node_key y queda registrado

### Scenario: alta inválida
- **Given** un enroll_secret erróneo o un node_key revocado
- **When** llama a cualquier endpoint
- **Then** recibe `node_invalid: true` y no se escribe nada en R2

### Scenario: resultados en R2
- **Given** un host dado de alta
- **When** envía resultados por log
- **Then** aparecen en R2 bajo su prefijo y no se pueden sobrescribir

### Scenario: límite de CPU
- **Given** un snapshot de paquetes de tamaño real
- **When** se envía al Worker en plan gratuito
- **Then** se procesa por debajo de 10 ms de CPU o se documenta la necesidad de Workers Paid

### Scenario: consulta en vivo
- **Given** una consulta encolada para un host
- **When** el host llama a distributed/read y luego a distributed/write
- **Then** el resultado queda disponible para la CLI

## Notas técnicas / riesgos

- Endpoint expuesto a Internet con autenticación propia: review de seguridad obligatoria.
- Workers Free: 100 000 peticiones/día, 10 ms CPU por petición.
- Decisión abierta: TypeScript o Python Workers.
- Más dependencia de Cloudflare: requiere la copia independiente de `evidence-storage`.

## Tasks (no autoritativo — el board manda)

- [ ] Elegir lenguaje (ADR)
- [ ] Endpoints + tests de protocolo
- [ ] HMAC + revocación
- [ ] Binding R2
- [ ] wrangler + runbook
- [ ] Medición de CPU

## Referencias

- https://osquery.readthedocs.io/en/stable/deployment/remote/
- https://developers.cloudflare.com/workers/platform/limits/
