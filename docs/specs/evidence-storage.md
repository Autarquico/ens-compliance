---
codename: ens-compliance
title: "Storage de evidencias y copia independiente"
type: task
status: active
priority: P0
size: L
issue: null
prs: []
related_issues: [4]
depends_on: [repo-foundation]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Storage de evidencias y copia independiente

## Contexto / problema

Evidencias como ficheros locales en la caja con manifiestos sha256, y copia
diaria cifrada con restic a un S3 externo con bloqueo de objetos (R2 en
Autarqui). Las garantías de independencia e inmutabilidad solo se afirman
tras probarlas (docs/vision.md, "Copia independiente: qué garantiza y qué no").

## Scope

**Entra:**
- Layout `raw/ normalized/ manifests/ reports/ assessments/` (docs/ens-compliance-guide.md:711-716) en `/data/evidence`.
- Manifiestos con sha256 y `ens-evidence evidence verify <id>`.
- Registro de evidencia manual (fichero aportado por una persona) con hash, autor y fecha.
- Copia restic cifrada a S3 externo; credenciales separadas de cualquier fuente vigilada.
- Modelo de amenaza de la copia (incl. Cloudflare como punto único de fallo si se usa R2).
- Runbook de restauración completa.

**No entra:**
- Backups de los sistemas de la pyme: es `collector-restic`.

## Criterios de aceptación

### Scenario: evidencia alterada
- **Given** una evidencia y su manifiesto
- **When** se modifica un byte y se ejecuta `evidence verify`
- **Then** falla mostrando el hash esperado y el obtenido

### Scenario: storage caído
- **Given** el destino de la copia inaccesible
- **When** se ejecuta la copia
- **Then** se registra el fallo y la evidencia no se da por copiada

### Scenario: prueba de borrado
- **Given** objetos ya copiados dentro del periodo de retención
- **When** se intenta borrar o sobrescribir con las credenciales de la caja
- **Then** el destino lo rechaza

### Scenario: restauración completa
- **Given** una caja perdida
- **When** se restaura desde la copia en una máquina nueva
- **Then** evidencias, manifiestos y estado coinciden y `evidence verify` pasa

## Notas técnicas / riesgos

- Sin verificar: restic sobre bucket R2 con bloqueo de objetos.
- R2: tokens con alcance por bucket, no por prefijo.

## Tasks (no autoritativo — el board manda)

- [ ] Layout + manifiestos
- [ ] evidence verify
- [ ] Evidencia manual
- [ ] Copia restic
- [ ] Modelo de amenaza
- [ ] Pruebas de borrado y restore

## Referencias

- docs/vision.md
- docs/ens-compliance-guide.md:681-733
- https://restic.readthedocs.io/
- https://developers.cloudflare.com/r2/buckets/bucket-locks/
