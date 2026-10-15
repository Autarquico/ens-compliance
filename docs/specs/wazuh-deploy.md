---
codename: ens-compliance
title: "Despliegue de Wazuh"
type: task
status: draft
priority: P1
size: L
issue: null
prs: []
related_issues: []
depends_on: []
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Despliegue de Wazuh

## Contexto / problema

Despliegue de Wazuh como núcleo de detección (docs/ens-compliance-guide.md:567-626). Quickstart
all-in-one recomienda 4 vCPU / 8 GiB / 50 GB para 1-25 agentes. Antes de
desplegar hay que decidir con datos qué aporta frente a osquery (detección,
FIM, alertas) y si se justifica ese coste.

## Scope

**Entra:**
- ADR: alcance de Wazuh frente a osquery y dimensionamiento.
- Pasos de docs/ens-compliance-guide.md:586-597 como playbook y runbook, con aprobación humana antes de aplicar.
- Dashboard y APIs administrativas no expuestas a Internet.
- Backup y health checks.

**No entra:**
- Agentes dentro de cada contenedor.

## Criterios de aceptación

### Scenario: alta y desconexión
- **Given** un agente piloto
- **When** se da de alta y luego se desconecta
- **Then** Wazuh registra el alta y detecta la desconexión

### Scenario: evento controlado
- **Given** un evento de prueba en un host
- **When** se genera
- **Then** aparece una alerta en Wazuh

## Notas técnicas / riesgos

- Infraestructura real: nada se aplica sin aprobación explícita.

## Tasks (no autoritativo — el board manda)

- [ ] ADR alcance y tamaño
- [ ] Playbook
- [ ] Runbook despliegue/rollback
- [ ] Piloto
- [ ] Pruebas de 619-626

## Referencias

- https://documentation.wazuh.com/current/quickstart.html
- docs/ens-compliance-guide.md:567-626
