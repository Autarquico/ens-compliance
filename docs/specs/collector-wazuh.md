---
codename: ens-compliance
title: "Collector de Wazuh"
type: task
status: active
priority: P2
size: M
issue: null
prs: []
related_issues: [12]
depends_on: [detection-profile, evidence-storage]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Collector de Wazuh

## Contexto / problema

Lee `alerts.json` y la API del manager (agentes, Syscollector, SCA) y reenvía las alertas a VictoriaLogs.

## Scope

**Entra:**
- Lectura incremental de `alerts.json`.
- Reenvío a VictoriaLogs (`/insert/jsonline`).
- Checks de cobertura de agentes.

**No entra:**
- Respuesta automática.

## Criterios de aceptación

### Scenario: credencial revocada
- **Given** un token de la fuente revocado o inválido
- **When** se ejecuta el collector
- **Then** el resultado es `ERROR` y ningún check derivado da `PASS`

### Scenario: permiso insuficiente frente a configuración inexistente
- **Given** un recurso al que el token no tiene acceso
- **When** se ejecuta el collector
- **Then** el dato queda como `PERMISSION_DENIED`/`UNKNOWN`, distinto de "no configurado"

### Scenario: sin secretos en la salida
- **Given** una ejecución con logging en nivel debug
- **When** se inspeccionan logs y evidencia normalizada
- **Then** no aparece ningún token, clave ni cabecera de autorización

### Scenario: host sin cobertura
- **Given** un host del inventario sin agente activo
- **When** se evalúan los checks
- **Then** aparece como hallazgo

## Notas técnicas / riesgos

- Sin Filebeat: el collector hace de shipper.

## Tasks (no autoritativo — el board manda)

- [ ] Lector
- [ ] Reenvío
- [ ] Checks
- [ ] Tests

## Referencias

- https://docs.victoriametrics.com/victorialogs/data-ingestion/
