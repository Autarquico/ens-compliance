# Conector de Restic

Comprueba, en solo lectura, que la copia de evidencias existe, está al día,
es íntegra, se ha restaurado con éxito y está fuera de la caja. Cubre los
checks BACKUP-001…006 de la guía (`docs/ens-compliance-guide.md:808-840`).

Spec: [collector-restic](../specs/collector-restic.md). Contrato de la copia:
[ADR 0008](../decisions/0008-evidencias-y-copia-independiente.md).

## Qué ejecuta y qué no

El collector solo lanza dos comandos de restic, los dos con
`--no-lock --no-cache` para no escribir nada en el repositorio:

- `restic cat config`: si el repositorio existe y su id.
- `restic snapshots --json --tag ens-evidence`: los snapshots de la caja.

Nunca ejecuta `check`, `restore`, `forget` ni `prune`. Lo que necesita más
que eso lo hacen comandos explícitos que dejan un registro en el workspace,
y el collector lee esos registros:

| Comando | Registro | Para qué check |
|---------|----------|----------------|
| `ens-evidence backup run` | `evidence/backups/<run_id>.yaml` | BACKUP-001, BACKUP-002 |
| `ens-evidence backup check [--read-data-subset N%\|--read-data]` | `evidence/backups/checks/` | BACKUP-004 |
| `ens-evidence backup restore --snapshot <id> --target <carpeta vacía>` | `evidence/backups/restores/` | BACKUP-005 |
| `ens-evidence backup restore-test record <fichero.yaml>` | `evidence/restore-tests/<test_id>.yaml` | BACKUP-005 |

`backup check` toma el lock del repositorio y puede tardar: sin opciones solo
comprueba metadatos; `--read-data-subset 10%` lee esa parte de los datos y
`--read-data` los lee todos.

Cada registro guarda el id del repositorio. Si el repositorio cambia, los
registros antiguos ya no cuentan ("registro de otro repositorio").

## Configuración

El repositorio y los secretos son los de la copia (`backup.*` en los ajustes
y `ENS_BACKUP_*` en el entorno, ver el
[runbook](../runbooks/restauracion-de-evidencias.md)). En el workspace,
`connectors/restic.yaml`:

```yaml
asset_id: copia-evidencias          # activo backup de scope/assets.yaml
max_backup_age_hours: 26             # BACKUP-001
max_check_age_days: 30               # BACKUP-004
min_check_read_data_percent: 10      # BACKUP-004: % mínimo de datos leídos por check
restore_test_validity_days: 180      # BACKUP-005
offsite:                             # BACKUP-006, opcional
  location: Cloudflare R2, cuenta distinta de la de las fuentes vigiladas
  administrative_separation: credenciales y administración separadas
  reviewer: Nombre Apellido
  role: security_officer
  reviewed_at: "2026-10-28T10:00:00+01:00"
  valid_until: "2027-10-28T10:00:00+01:00"
```

**Los umbrales no tienen valor por defecto:** la guía no los fija y los
decide la organización. Los valores del ejemplo son solo ilustrativos.

## Restore test

Restaura siempre en un destino aislado (una carpeta vacía), desde la caja de
origen, y registra la prueba citando esa restauración:

```yaml
test_id: RESTORE-2026-001
system: copia-evidencias                 # activo del inventario
performed_at: "2026-10-28T11:00:00+01:00"   # fecha y hora con zona
restore_id: <id del registro de backup restore>
isolated_target: true
backup:
  snapshot_id: <id completo del snapshot>
result:
  restored: true
  integrity_verified: true
  application_started: true              # o not_applicable con justificación
  # application_justification: "solo datos; no hay aplicación que arrancar"
review:
  approved: true
  reviewer: Nombre Apellido
  role: security_officer
  reviewed_at: "2026-10-28T12:00:00+01:00"
  valid_until: "2027-04-28T12:00:00+01:00"
```

Diferencias con el ejemplo de la guía: `performed_at` exige hora y zona, y
se añaden `restore_id`, `isolated_target` y los datos de la revisión. La
restauración citada tiene que ser del mismo snapshot y repositorio y con el
inventario verificado. `isolated_target` es una declaración de quien hizo la
prueba.

Reglas del registro: `performed_at` no puede ser anterior al fin de la
restauración citada ni estar a más de 7 días de él (una restauración antigua
no avala un test reciente), y una restauración solo la puede citar un restore
test. `backup restore` rechaza como destino una carpeta dentro del workspace
local en uso.

Una restauración en una caja nueva con el workspace vacío no deja registro
(se importaría como clave añadida): la prueba periódica se hace desde la caja
de origen hacia una carpeta aislada.

## Uso

```bash
ens-evidence collect restic
```

Mismos códigos que el resto de conectores: 0 todo recogido, 3 parcial, 1
error global (contraseña incorrecta o sin credenciales).

## Checks

| Check | `PASS` solo si | `FAIL` si |
|-------|----------------|-----------|
| `restic.backup_001_recent` | último snapshot propio con edad ≤ `max_backup_age_hours` | más viejo, o ninguno con repositorio accesible |
| `restic.backup_002_no_errors` | la última ejecución es `completed` y su snapshot está en el repositorio | la última es `failed` o quedó interrumpida (`started`) |
| `restic.backup_003_repository_accessible` | el repositorio existe y es accesible | no existe |
| `restic.backup_004_integrity` | el último `backup check` terminó sin errores, está dentro de plazo y leyó al menos el % configurado | errores, caducado o cobertura insuficiente |
| `restic.backup_005_restore_test` | el último restore test es correcto, está revisado, vigente y atado a una restauración verificada | caducado o con algún resultado falso |
| `restic.backup_006_offsite` | backend remoto, accesible y con la declaración `offsite` vigente | backend local |

Siempre cuenta el **último intento**, no el último éxito. Fechas futuras,
registros de otro repositorio, snapshots que ninguna ejecución de este
workspace creó y registros ilegibles dan `UNKNOWN`. La separación
administrativa del destino es un control organizativo declarado, no algo
que la herramienta pueda comprobar.
