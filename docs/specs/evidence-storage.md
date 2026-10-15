---
codename: ens-compliance
title: "Storage de evidencias en R2"
type: task
status: draft
priority: P1
size: L
issue: null
prs: []
related_issues: []
depends_on: [repo-foundation]
supersedes: []
related_specs: []
created: 2026-10-15
updated: 2026-10-15
---

# Storage de evidencias en R2

## Contexto / problema

Almacenamiento de evidencias sensibles fuera de Git (docs/ens-compliance-guide.md:708-733) en
Cloudflare R2, con manifiestos e integridad por sha256 (docs/ens-compliance-guide.md:681-704) y una
copia independiente fuera de Cloudflare, ya que Cloudflare también es una
fuente vigilada.

## Scope

**Entra:**
- Módulo `storage/` con interfaz S3-compatible y adaptador R2.
- Layout `raw/ normalized/ manifests/ reports/ assessments/` (docs/ens-compliance-guide.md:711-716).
- Manifiestos con sha256 y comando `ens-evidence evidence verify <id>`.
- Bucket locks de R2 por prefijo contra borrado/sobrescritura.
- Tokens R2 separados: escritura para el pipeline, lectura para informes.
- Copia independiente a un segundo proveedor y restore test documentado.

**No entra:**
- Backups de Restic de los sistemas: es `collector-restic`.

## Criterios de aceptación

### Scenario: evidencia alterada
- **Given** una evidencia almacenada y su manifiesto
- **When** se modifica un byte del objeto y se ejecuta `evidence verify`
- **Then** la verificación falla con el hash esperado y el obtenido

### Scenario: storage caído
- **Given** R2 inaccesible
- **When** se ejecuta una recogida
- **Then** la recogida no se marca OK y no se pierde ni se da por buena la evidencia

### Scenario: sin borrado
- **Given** un objeto bajo un prefijo con bucket lock
- **When** se intenta borrar o sobrescribir con el token del pipeline
- **Then** R2 lo rechaza

### Scenario: copia independiente
- **Given** evidencias de un día en R2
- **When** se ejecuta la copia y se restaura desde el segundo proveedor
- **Then** los hashes coinciden con los manifiestos

## Notas técnicas / riesgos

- R2: tokens con alcance por bucket, no por prefijo (docs R2 API tokens).
- Nivel gratuito 10 GB-mes en Standard; vigilar si crece por encima.
- Elegir segundo proveedor (decisión humana).

## Tasks (no autoritativo — el board manda)

- [ ] Interfaz storage + adaptador
- [ ] Manifiestos + verify
- [ ] Bucket locks
- [ ] Copia independiente
- [ ] Runbook de restore

## Referencias

- https://developers.cloudflare.com/r2/
- docs/ens-compliance-guide.md:681-733
