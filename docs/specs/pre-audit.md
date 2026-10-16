---
codename: ens-compliance
title: Pre-auditoría y audit pack
type: task
status: active
priority: P0
size: L
issue: null
prs: []
related_issues: [25]
depends_on: [assessment-dossier]
supersedes: []
related_specs: []
created: 2026-10-16
updated: 2026-10-16
---

# Pre-auditoría y audit pack

## Contexto / problema

Revisión del expediente final en dos capas (docs/vision.md): determinista (motor) y de
juicio mediante un audit pack portable ejecutado con un adaptador LLM
agnóstico. v0.1 incluye clientes nativos de Anthropic y OpenAI. Nunca
dictamina conformidad.

## Scope

**Entra:**
- Capa determinista: evidencias ausentes o caducadas, hashes, checks en `FAIL`/`UNKNOWN`, estados del expediente.
- Audit pack versionado: criterios, prompts y esquema JSON de hallazgos (no conformidad mayor, menor, observación).
- Adaptador LLM con clientes Anthropic y OpenAI (`base_url` configurable).
- Minimización y redacción de secretos antes de enviar datos; consentimiento explícito.

**No entra:**
- Banco de defectos sembrados: es `audit-benchmark`.
- Modelos locales (v0.3).

## Criterios de aceptación

### Scenario: hallazgo trazable
- **Given** una política que contradice la evidencia técnica
- **When** se ejecuta la pre-auditoría
- **Then** aparece un hallazgo clasificado que cita medida, documento y evidencia

### Scenario: sin LLM
- **Given** el adaptador desactivado
- **When** se ejecuta la pre-auditoría
- **Then** la capa determinista se completa y el estado indica "revisión humana pendiente"

### Scenario: sin secretos
- **Given** un expediente con un token en un log
- **When** se prepara el envío al proveedor
- **Then** el token está redactado

### Scenario: salida inválida
- **Given** una respuesta del proveedor que no cumple el esquema
- **When** se procesa
- **Then** se descarta y se registra como `ERROR`, sin hallazgos inventados

## Notas técnicas / riesgos

- Pregunta abierta: guía CCN-STIC que rige la auditoría y sus criterios de clasificación.

## Tasks (no autoritativo — el board manda)

- [ ] Capa determinista
- [ ] Esquema de hallazgos
- [ ] Audit pack v1
- [ ] Adaptador + clientes
- [ ] Redacción y consentimiento

## Referencias

- docs/vision.md
