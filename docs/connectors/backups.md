# Conector `backups`

Lee, en solo lectura, N repositorios restic (los que escribe Backrest en el
paquete de copias del kit) y evalúa sobre cada uno los checks BACKUP.
Spec: [kit-backups](../specs/kit-backups.md). Es distinto del conector
[restic](restic.md), que vigila la copia de evidencias de la propia caja.

## Qué lee

Por cada repositorio de `connectors/backups.yaml`, con `--no-lock --no-cache`
(regla 9, no escribe nada ni toma lock):

- `restic cat config`: si existe y su id.
- `restic snapshots --json`: todos los snapshots, sea cual sea su etiqueta
  (Backrest no usa la de la caja).
- `restic check --json [--read-data-subset=<N>%]`: en cada recogida, con el
  `min_check_read_data_percent` del repositorio (0 = solo estructura). Tiene
  un timeout propio de 3600 s. Sin ese umbral no se ejecuta.

Nunca ejecuta `backup`, `forget`, `prune` ni `restore`. Del workspace lee los
registros de restore tests y restauraciones atados al id del repositorio.

## Configuración

```yaml
repositories:
  - id: caja                      # [A-Za-z][A-Za-z0-9_]{0,39}, único sin distinguir mayúsculas
    asset_id: vps-caja            # activo de tipo backup del inventario
    repository: s3:https://s3.example.com/copias/caja
    max_backup_age_hours: 26      # BACKUP-001
    max_check_age_days: 35        # BACKUP-004
    min_check_read_data_percent: 5  # BACKUP-004 (0-100); sin él, UNKNOWN
    restore_test_validity_days: 365 # BACKUP-005
    offsite: {...}                # BACKUP-006, opcional (revisión humana)
```

Los umbrales no tienen valor por defecto: los decide la organización. El
asistente del kit propone 5 % para `min_check_read_data_percent` y lo muestra
al usuario. `offsite` lo añade después una persona (modelo de
[restic](restic.md)).

## Credenciales

Solo por entorno, con `<ID>` el `id` en mayúsculas. Usa claves de **lectura**
(las de escritura solo las tiene Backrest):

- `ENS_BACKUPS_<ID>_RESTIC_PASSWORD`
- `ENS_BACKUPS_<ID>_AWS_ACCESS_KEY_ID`
- `ENS_BACKUPS_<ID>_AWS_SECRET_ACCESS_KEY`

Sin ellas, ese repositorio queda en `ERROR` y sus checks no pasan; los demás
repositorios se siguen leyendo.

## Checks

Cada check produce un resultado por repositorio, con su `asset_id`.

| Check | Qué comprueba |
|-------|---------------|
| `backups.backup_001_recent` | Hay un snapshot (de cualquier etiqueta) más reciente que `max_backup_age_hours`. |
| `backups.backup_003_repository_accessible` | El repositorio existe y se abre con las credenciales de lectura. |
| `backups.backup_004_integrity` | El `restic check --no-lock` de esta recogida terminó sin errores y con cobertura de datos igual o superior a `min_check_read_data_percent`. |
| `backups.backup_005_restore_test` | El último restore test está completo, aprobado por una persona y vigente. |
| `backups.backup_006_offsite` | Destino remoto accesible con declaración `offsite` vigente. |

La ausencia de evidencia nunca es `PASS` (regla 5): sin umbral, sin registros o
con lecturas fallidas el resultado es `UNKNOWN`, `FAIL` o `ERROR`.

## Qué no cubre

- **BACKUP-002 (errores de ejecución) no aplica:** la app lee los
  repositorios, no la API de Backrest, y no ve sus ejecuciones. Una copia
  fallida se detecta por BACKUP-001 (frescura).
- No verifica que lo copiado sea lo que importa (rutas y volúmenes elegidos),
  ni la inmutabilidad del destino.
- Un `check` con subconjunto del 5 % no demuestra la integridad de todos los
  datos; la cobertura queda registrada en la evidencia.
- La restauración y la separación administrativa (offsite) son revisión
  humana: la herramienta no declara conformidad por sí sola.
