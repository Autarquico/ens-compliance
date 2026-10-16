---
codename: ens-compliance
title: Perfil de detección
type: task
status: active
priority: P2
size: L
issue: null
prs: []
related_issues: [34]
depends_on: [box-packaging]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Perfil de detección

## Contexto / problema

Perfil opcional de la caja: wazuh-manager sin indexer ni dashboard ni Filebeat, y VictoriaLogs como índice de alertas (docs/vision.md).

## Scope

**Entra:**
- Servicios en un perfil de Compose.
- ADR con medición de RAM real del manager y de VictoriaLogs.
- Licencias verificadas frente a AGPL-3.0.

**No entra:**
- Indexer y dashboard de Wazuh.

## Criterios de aceptación

### Scenario: alerta llega
- **Given** un agente Wazuh y un evento de prueba
- **When** se genera
- **Then** aparece en `alerts.json`

### Scenario: recursos
- **Given** el perfil activo con agentes de prueba
- **When** se mide durante 24 h
- **Then** el consumo queda registrado en el ADR

## Notas técnicas / riesgos

- Sin verificar: RAM del manager sin indexer; licencia de VictoriaLogs.

## Tasks (no autoritativo — el board manda)

- [ ] Compose profile
- [ ] Medición
- [ ] ADR
- [ ] Licencias

## Referencias

- https://documentation.wazuh.com/current/user-manual/manager/alert-management.html
- https://docs.victoriametrics.com/victorialogs/
