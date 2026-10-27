# 0006 — Workspace y backends de almacenamiento

- Estado: aceptada
- Fecha: 2026-10-27
- Decidida por: Alejandro Perez

## Contexto

Los datos de cada organización (inventario, alcance, riesgos, aplicabilidad,
respuestas, evidencias) no pueden vivir en el repo del producto, que es
AGPL y público. Cada organización decide dónde guardarlos.

## Decisión

### Workspace

Conjunto de ficheros de una organización con rutas relativas
(`scope/…`, y en specs futuras `risk/…`, `controls/…`, `org/…`). El producto
nunca guarda datos de una organización en su propio repo.

### Ajustes

Fichero `settings.yaml` en `--settings` / `ENS_SETTINGS`; por defecto
`~/.config/ens-evidence/settings.yaml`, nunca el directorio actual. Las rutas
relativas se resuelven respecto al fichero de ajustes. No hay destino por
defecto: `init` lo exige.

```yaml
workspace:
  backend: local            # local | s3
  local: { path: /ruta/al/workspace }
  s3: { bucket: mi-bucket, prefix: org/, endpoint_url: https://..., region: auto }
```

- Credenciales S3 solo por variables de entorno estándar
  (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`). Se
  rechazan claves de credenciales en el YAML y credenciales embebidas en
  `endpoint_url`.
- `endpoint_url` exige `https` salvo `allow_insecure_http: true`, solo para
  desarrollo.
- Los errores nunca imprimen el valor rechazado.
- Un destino local dentro de un checkout del producto (tras resolver enlaces
  simbólicos) se rechaza. La única excepción son fixtures de tests y
  ejemplos, mediante un parámetro de API no expuesto en la CLI.

### `WorkspaceStore`

- Claves relativas normalizadas: separador `/`, sin `..`, sin rutas
  absolutas, sin claves vacías.
- Operaciones: `read_text`, `write_text`, `exists`, `list(prefix)`.
- Errores tipados: `NotFound`, `AccessDenied`, `StorageUnavailable`.
- `LocalStore`: escritura atómica; contención verificada en lectura,
  escritura y listado, incluidos enlaces simbólicos.
- `S3Store`: extra opcional `ens-evidence[s3]` (boto3). El prefijo termina en
  `/` para aislar `org/` de `org-otra/`; listado paginado; R2 y otros
  proveedores vía `endpoint_url`. Compatibilidad real con R2 sin verificar.

### Edición con S3

Se edita en local y se sincroniza con `workspace pull <dir>` y
`workspace push <dir>`; `push` valida todo antes de escribir. Un único editor
en v0.1. La coherencia la garantiza la huella de aprobación del alcance: una
publicación parcial nunca queda aprobada.

### Puerta de alcance

`scope_status()` devuelve `NOT_APPROVED`, `APPROVED`, `STALE` o `INVALID`.
`require_approved_scope()` falla si no es `APPROVED`; la categorización debe
llamarla y guardar la huella aprobada. Aprueba el responsable del sistema
(`system_owner`). Aprobar el alcance no tiene relación con `Readiness` (0002).

## Consecuencias

- El repo del producto puede publicarse sin datos de ninguna organización.
- Las evidencias (spec `evidence-storage`) reutilizarán `WorkspaceStore`.
- boto3 solo se instala si se usa S3.
- Sin bloqueo entre editores concurrentes en v0.1.
