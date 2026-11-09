---
codename: ens-compliance
title: "Kit: almacén de claves y secretos con OpenBao"
type: task
status: active
priority: P1
size: L
issue: null
prs: []
related_issues: [67]
depends_on: [kit-backups]
supersedes: []
related_specs: [toolkit-server]
created: 2026-11-09
updated: 2026-11-09
---

# Kit: almacén de claves y secretos con OpenBao

## Contexto / problema

Herramienta del kit (`toolkit-server`, #65; ADR 0019), con el contrato que fija `kit-backups`: perfil de Compose tras Access, conector de solo lectura, checks de uso real, runbook y tarjeta en `/herramientas`. Instalar no es cumplir (regla 1).

Cubre op.exp.10, «Protección de claves criptográficas»: protección de las claves en todo su ciclo de vida (op.exp.10.1, `src/ens_evidence/catalog/data/op.yaml:2160`). OpenBao es el fork abierto de Vault (licencia a verificar).

## Scope

**Entra:**
- Perfil `claves` con OpenBao, almacenamiento integrado y auditoría activada desde el arranque.
- Inicialización guiada: desbloqueo Shamir (N partes, umbral M) entregado a custodios; la web registra quiénes y desde cuándo, como revisión humana. Sin desbloqueo automático.
- El root token se revoca tras la instalación. La app lee con un token de política de solo lectura sobre metadatos (nunca valores) y de vida corta.
- Conector: inventario de motores y claves (nombres, tipo, versión, fecha de rotación), políticas, auditoría activa y estado sellado.
- Checks: auditoría activada, claves con rotación dentro de la política, sin root token vivo, servidor desellado.
- Copia: el almacenamiento de OpenBao entra en el plan de `kit-backups`.
- Runbook `kit-claves.md`: inicialización, desbloqueo tras reinicio, rotación, pérdida de un custodio, restauración.

**No entra:**
- Gestión de contraseñas de personas (`kit-passwords`).
- HSM o KMS en la nube.
- Migrar secretos desde otros almacenes.

## Criterios de aceptación

### Scenario: la app nunca lee un secreto
- **Given** el token de la app
- **When** se intenta leer el valor de un secreto con él
- **Then** OpenBao lo rechaza; el conector solo obtiene metadatos

### Scenario: OpenBao sellado se ve
- **Given** OpenBao reiniciado y sin desbloquear
- **When** la app recoge evidencias
- **Then** la tarjeta de Claves sale en rojo con el enlace al runbook y op.exp.10 pierde la evidencia vigente

### Scenario: instalar no es cumplir
- **Given** OpenBao desplegado sin claves ni auditoría
- **When** corren los checks
- **Then** op.exp.10 no tiene evidencia vigente y el check dice qué falta


## Notas técnicas / riesgos

- Si se pierden partes por debajo del umbral, los secretos son irrecuperables: el runbook y la web lo dicen antes de inicializar.
- Review de seguridad obligatoria.
- Sin verificar: licencia de OpenBao y API de auditoría con la versión fijada.

## Tasks (no autoritativo — el board manda)

- [ ] Verificar OpenBao con la imagen fijada
- [ ] Perfil `claves` e inicialización guiada con custodios
- [ ] Token de solo lectura de metadatos y conector
- [ ] Checks y evidencia de op.exp.10
- [ ] Runbook y tests

## Referencias

- docs/specs/toolkit-server.md, docs/decisions/0019-caja-y-herramientas-en-un-vps.md
- https://openbao.org/
