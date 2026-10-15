---
codename: ens-compliance
title: Collector de Wazuh
type: task
status: active
priority: P1
size: M
issue: null
prs: []
related_issues: [12]
depends_on: [evidence-storage, wazuh-deploy]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de Wazuh

## Contexto / problema

Collector read-only de la API de Wazuh: agentes, cobertura y alertas.

## Scope

**Entra:**
- Estado de agentes, cobertura por host, alertas críticas, eventos para incidentes.

**No entra:**
- Respuesta automática.

## Criterios de aceptación

### Scenario: credencial revocada
- **Given** un token de la fuente revocado o inválido
- **When** se ejecuta el collector
- **Then** el resultado es `collection_status: ERROR` y ningún check derivado da `PASS`

### Scenario: permiso insuficiente frente a configuración inexistente
- **Given** un recurso al que el token no tiene acceso
- **When** se ejecuta el collector
- **Then** el dato queda como `PERMISSION_DENIED`/`UNKNOWN`, distinto de "no configurado"

### Scenario: sin secretos en la salida
- **Given** una ejecución con logging en nivel debug
- **When** se inspeccionan logs y evidencia normalizada
- **Then** no aparece ningún token, clave ni cabecera de autorización

### Scenario: host sin cobertura
- **Given** un host del inventario sin agente Wazuh activo
- **When** se evalúan los checks
- **Then** aparece como hallazgo de cobertura

## Notas técnicas / riesgos

- API de Wazuh accesible solo desde la red interna o el ejecutor.

## Tasks (no autoritativo — el board manda)

- [ ] Modelos
- [ ] Cliente
- [ ] Checks
- [ ] Fixtures + tests

## Referencias

- https://documentation.wazuh.com/
