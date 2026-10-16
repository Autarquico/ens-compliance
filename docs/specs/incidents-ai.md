---
codename: ens-compliance
title: Incidentes y análisis con IA opcional
type: task
status: active
priority: P2
size: L
issue: 15
prs: []
related_issues: [15]
depends_on: [collector-wazuh, incident-register]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Incidentes y análisis con IA opcional

## Contexto / problema

Flujo alerta → correlación determinista → incidente, con análisis LLM opcional (docs/ens-compliance-guide.md:844-901).

## Scope

**Entra:**
- Modelo de incidente de docs/ens-compliance-guide.md:867-890.
- Correlación determinista y notificaciones.
- Adaptador LLM intercambiable con redacción de secretos y salida estructurada.

**No entra:**
- Acciones destructivas automáticas.
- Dependencia crítica de un LLM.

## Criterios de aceptación

### Scenario: sin LLM
- **Given** el adaptador de IA desactivado
- **When** llega una alerta crítica
- **Then** se crea el incidente y se notifica igualmente

### Scenario: logs no confiables
- **Given** una alerta con contenido que intenta dar instrucciones al modelo
- **When** se analiza con LLM
- **Then** la salida no ejecuta acciones y requiere revisión humana

## Notas técnicas / riesgos

- No enviar datos sensibles a modelos sin autorización.

## Tasks (no autoritativo — el board manda)

- [ ] Modelo
- [ ] Correlación
- [ ] Notificaciones
- [ ] Adaptador LLM
- [ ] Tests

## Referencias

- docs/ens-compliance-guide.md:844-901
