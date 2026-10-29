# 0008 — Evidencias y copia independiente

- Estado: aceptada
- Fecha: 2026-10-27

## Contexto

Las evidencias son datos de la organización (ADR 0006) y tienen que poder
verificarse y sobrevivir a la pérdida de la caja. La guía advierte que un
hash verifica integridad pero no protege contra borrado o modificación del
almacenamiento (`docs/ens-compliance-guide.md:703-704`).

## Decisión

### Almacenamiento de evidencias
- En el workspace, bajo `evidence/`: `raw/`, `normalized/`, `manifests/`,
  `reviews/`, `reports/`, `assessments/`, `backups/`.
- `WorkspaceStore` gana `read_bytes`, `write_bytes` y `create_bytes`
  (creación exclusiva: falla si la clave existe; local con fichero temporal
  + `os.link`, S3 con `If-None-Match: *`). Si el backend no soporta
  escritura condicional, `create_bytes` falla; nunca degrada a comprobar y
  escribir.
- Artefactos direccionados por contenido (`evidence/raw/<sha256>`).
  Orden: artefacto, después manifiesto, ambos con creación exclusiva. Un
  artefacto sin manifiesto es un huérfano, nunca una evidencia.
- IDs `EV-YYYYMMDD-<8 hex aleatorios>`.
- Manifiesto ampliado: `provenance` (`collector` o `manual`, quién la aporta
  y cuándo) y `collected_at` (momento al que se refiere), con
  `valid_until > collected_at`. Las revisiones se añaden en
  `evidence/reviews/<id>/`; el manifiesto no se modifica nunca.

### Integridad
`evidence verify` comprueba manifiesto, artefacto y hash, y con `--all`
lista huérfanos y referencias rotas. El hash prueba que el artefacto
coincide con su manifiesto, no su autenticidad: la alteración conjunta de
ambos solo la detecta la copia externa inmutable.

### Copia independiente
- restic, invocado como subproceso con un entorno construido explícitamente
  (solo `ENS_BACKUP_*` mapeadas a lo que espera restic). Secretos de la
  copia distintos de los del workspace; si coinciden, se rechaza.
- Origen: exportación completa del workspace (todas las claves, por
  bytes), abortada si el listado cambia o falla una descarga.
- Registro por ejecución en `evidence/backups/<run_id>.yaml`:
  `started` → `completed` (snapshot e inventario con sha256) o `failed`.
  Una ejecución que queda en `started` es una ejecución interrumpida. El
  registro de la ejecución en curso no entra en su propio snapshot.
- **Snapshot autoverificable:** antes de copiar, el inventario de la
  exportación se escribe dentro de ella en `.ens-backup/<run_id>.inventory.json`
  (excluido de su propio inventario). Así una caja nueva, con el workspace
  vacío, puede verificar lo que restaura. `.ens-backup/` nunca se importa al
  workspace.
- Restauración aceptada solo si el inventario restaurado coincide con el del
  propio snapshot (y con el registro del workspace si existe) y
  `evidence verify --all` pasa. Sin inventario, la restauración es "no
  verificada" y falla.
- Al importar una restauración, antes de escribir nada, las claves y sha256
  de la carpeta se comparan con el inventario del snapshot; cualquier clave
  modificada, eliminada o añadida aborta la importación sin escrituras. Solo
  entonces el registro de esa ejecución se reconstruye como `completed` y lo
  declara (`reconstructed_from_snapshot: true`). Una carpeta sin marcador de
  snapshot se importa como "no verificada" y sin reconstruir registros.

### Comprobación y pruebas de restauración
- `backup check` (integridad, con lock) y `backup restore` dejan un registro
  por intento con el id del repositorio (`restic cat config`), el snapshot
  completo, el alcance y los tiempos; `backup run` también guarda el id.
- `backup restore-test record` registra la prueba de restauración (formato de
  la guía más `restore_id`, `isolated_target` y la revisión) atada a una
  restauración verificada del mismo repositorio. Escritura única.
- El collector `restic` solo ejecuta `cat config` y `snapshots` con
  `--no-lock --no-cache` y lee esos registros; un registro de otro
  repositorio no cuenta.

### Inmutabilidad del destino
Es una propiedad del proveedor, no del código. Se trata como hipótesis
hasta probarla con `backup verify-immutability`, que usa objetos canario
desechables por prefijo protegido. Cada prefijo da `protected`,
`unprotected` (el canario desapareció o cambió) o `inconclusive` (no se pudo
leer: un error de lectura nunca prueba protección); el conjunto solo es
`protected` si lo son todos. La prueba es manual contra el bucket real y su
resultado se registra como evidencia. Receta propuesta para R2 (bucket
lock en `data/`, `index/`, `snapshots/`, `keys/`; no en `locks/`): sin
verificar.

## Consecuencias

- Una evidencia no se puede corregir: se aporta otra.
- La independencia administrativa del destino (otra cuenta, otro
  proveedor) es un control organizativo que se registra, no se deduce.
- Modelo de amenaza: `docs/security/copia-de-evidencias.md`.
