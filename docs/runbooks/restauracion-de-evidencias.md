# Runbook — copia y restauración de evidencias

Contrato: [ADR 0008](../decisions/0008-evidencias-y-copia-independiente.md).
Modelo de amenaza: [copia de evidencias](../security/copia-de-evidencias.md).

## Qué tiene que existir fuera de la caja

Sin esto la copia no se puede restaurar. Guárdalo en tu gestor de
contraseñas, no en la caja:

- `ENS_BACKUP_RESTIC_PASSWORD`: contraseña del repositorio restic.
- `ENS_BACKUP_AWS_ACCESS_KEY_ID` y `ENS_BACKUP_AWS_SECRET_ACCESS_KEY`:
  credenciales del destino de la copia, distintas de las del workspace.
- El fichero de ajustes (`settings.yaml`), con `backup.restic_repository` y,
  si aplica, `backup.region` (R2: `auto`).

## Configuración de la copia

El fichero de ajustes está en `~/.config/ens-evidence/settings.yaml` (o en
la ruta de `--settings` / `ENS_SETTINGS`). `ens-evidence init` crea la
sección `workspace`; la sección `backup` se añade a mano:

```yaml
workspace:
  backend: local
  local: { path: /ruta/a/mi-workspace }
backup:
  # Formato restic. Ejemplo con Cloudflare R2 (otra cuenta que la de tus
  # fuentes vigiladas):
  restic_repository: s3:https://<account_id>.r2.cloudflarestorage.com/<bucket>/ens
  region: auto            # R2 usa "auto"; en AWS S3, la región del bucket
```

El fichero de ajustes no lleva secretos: la contraseña y las credenciales
van solo en las variables de entorno de abajo.

## Puesta en marcha (una vez)

```bash
export ENS_BACKUP_RESTIC_PASSWORD=...           # desde el gestor de contraseñas
export ENS_BACKUP_AWS_ACCESS_KEY_ID=...
export ENS_BACKUP_AWS_SECRET_ACCESS_KEY=...
ens-evidence backup init
ens-evidence backup run
ens-evidence backup status
```

Después, contra el bucket real y con el bloqueo de objetos ya configurado:

```bash
ens-evidence backup verify-immutability
```

Solo usa objetos canario. Su resultado queda registrado como evidencia y
solo vale si todos los prefijos salen "protegido". "NO protegido": algún
borrado o sobrescritura surtió efecto y la copia **no** es inmutable; revisa
la configuración del bloqueo. "NO CONCLUYENTE": algún canario no se pudo leer
(red, permisos); corrige el acceso y repite. En ambos casos el comando sale
con error.

## Comprobaciones periódicas

Con la frecuencia que fije la organización en `connectors/restic.yaml`
(ver [conector de Restic](../connectors/restic.md)):

```bash
ens-evidence backup check --read-data-subset 10%   # integridad; toma el lock
ens-evidence backup restore --snapshot latest --target /ruta/vacia/aislada
ens-evidence backup restore-test record prueba.yaml # cita el restore_id anterior
ens-evidence collect restic                         # BACKUP-001…006
```

La prueba de restauración se hace desde la caja de origen y siempre en una
carpeta vacía y aislada; nunca sobre el workspace en uso.

## Restauración completa en una máquina nueva

1. Instala `ens-evidence` y restic.
2. Coloca el `settings.yaml` recuperado en `~/.config/ens-evidence/` (o
   indica su ruta con `--settings`), con un `workspace` vacío de destino, y
   exporta los secretos del gestor de contraseñas como arriba.
3. Restaura en una carpeta vacía:
   ```bash
   ens-evidence backup restore --snapshot latest --target /ruta/vacia
   ```
4. Devuelve el contenido al workspace (local o S3) que indique el fichero de
   ajustes:
   ```bash
   ens-evidence workspace import --all /ruta/vacia
   ```
5. Comprueba la integridad:
   ```bash
   ens-evidence evidence verify --all
   ens-evidence scope status
   ```
6. La restauración es válida solo si `restore` informa de que el inventario
   restaurado coincide con el que viaja dentro del propio snapshot y
   `evidence verify --all` no da errores. Si `restore` dice "NO verificado",
   no la des por buena. Registra la prueba (fecha, snapshot, máquina) como
   evidencia.

## Si algo falla

| Síntoma | Qué significa | Qué hacer |
|---------|---------------|-----------|
| `backup status` muestra una ejecución `started` | Se interrumpió | Repite `backup run`; la interrumpida nunca cuenta como copia |
| `backup run` termina en `failed` | El destino no estaba disponible o las credenciales fallaron | Revisa red y credenciales; la evidencia no se da por copiada |
| El inventario restaurado no coincide | Copia incompleta o alterada | No uses esa restauración; prueba otro snapshot y revisa el destino |
| `workspace import --all` aborta porque la carpeta difiere del inventario | Se modificó, borró o añadió algo tras `restore` | Nada se escribió; restaura de nuevo en una carpeta vacía e importa sin tocarla |
| `scope status` sale `NOT_APPROVED` tras importar | La importación cambió el alcance | El responsable del sistema vuelve a aprobarlo |
| `backup status` muestra la ejecución restaurada como "reconstruido al restaurar" | Es esperado: el registro se rehízo a partir del inventario del snapshot | Nada; queda constancia de que procede de una restauración |
