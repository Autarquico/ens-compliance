# Conector `keystore`

Lee, en solo lectura y solo metadatos, el almacén de claves OpenBao del kit
y evalúa los checks KEY-001..006. Spec: [kit-secrets](../specs/kit-secrets.md).
Cubre op.exp.10.1 solo como evidencia que una persona enlaza: no declara
conformidad.

## Configuración

`connectors/keystore.yaml`, sin secretos:

```yaml
asset_id: openbao               # activo `application` del inventario
address: http://openbao:8200    # por defecto
max_key_age_days: 90            # obligatorio: lo decide la organización
max_token_period_hours: 72      # por defecto 72 (periodo del rol `ens-app`)
```

El token de la app sale solo del entorno: `ENS_KEYSTORE_TOKEN` (deploy/.env,
modo 600). Nunca aparece en la evidencia, en errores ni en logs.

## Qué lee (lista blanca)

El cliente solo envía estas combinaciones; cualquier otra se rechaza antes de
enviar nada:

| Método | Ruta |
|--------|------|
| GET | `sys/seal-status`, `sys/audit`, `sys/mounts`, `sys/policies/acl/ens-app-metadata` |
| LIST / GET | `kv/metadata/` (recursivo por carpetas) y `kv/metadata/<k>` |
| LIST / GET | `transit/keys/` y `transit/keys/<k>` |
| LIST | `auth/token/accessors` |
| POST | `auth/token/lookup-accessor`, `sys/capabilities-self` (consultas) |
| GET | `auth/token/lookup-self` |
| POST | `auth/token/renew-self`: la única escritura, solo si el TTL es menor que la mitad del periodo |

Nunca lee valores de secretos (`kv/data`), la estructura de un secreto
(`kv/subkeys`) ni material de claves (`transit/export`, `transit/backup`).
Del resto se descartan `custom_metadata`, las claves públicas de transit y
`id` y `meta` de los tokens. Los nombres de secretos y claves sí entran en la
evidencia: no pongas datos personales en ellos.

`sys/audit` y `auth/token/accessors` exigen `sudo`; la política de la app lo
concede solo en esas dos rutas. Con un 403 (sin permiso o token no válido) la
sección queda `PERMISSION_DENIED` y el check, `UNKNOWN` con el motivo.

## Secciones

`seal`, `token`, `audit`, `mounts`, `kv`, `transit`, `policies`, `accessors`
y `custodians`, cada una con su estado. Si OpenBao está sellado, sin
inicializar o no responde, solo se lee `seal-status` y las demás secciones
quedan en `ERROR` (sin token tampoco se lee más). `custodians` sale del último
registro vigente del workspace (`kit.custody.latest_register`) y solo lleva
recuento, N, M y fechas, nunca nombres.

## Checks

Todos `SELF_REPORTED`, un resultado por recogida.

| Check | Id | Qué comprueba |
|-------|----|---------------|
| `keystore.unsealed` | KEY-001 | Inicializado y desellado. Sellado: FAIL «hacen falta M de N custodios». |
| `keystore.audit_enabled` | KEY-002 | Al menos un dispositivo de auditoría `file` activo; ninguno, FAIL. |
| `keystore.keys_rotated` | KEY-003 | Última versión de cada clave de transit y entrada de kv dentro de `max_key_age_days`. Cero claves: FAIL «no hay claves gestionadas». Un motor fuera de `kv/` (v2) y `transit/`: UNKNOWN. |
| `keystore.no_root_token` | KEY-004 | Ningún accessor con la política `root`. Listado truncado o ilegible: UNKNOWN. |
| `keystore.app_token_scoped` | KEY-005 | `lookup-self` con políticas exactamente `[ens-app-metadata]`, periodo ≤ `max_token_period_hours`, TTL > 0, `deny` en las rutas de valores y sha256 de la política igual al de `src/ens_evidence/kit/openbao/ens-app-metadata.hcl`. |
| `keystore.custodians_registered` | KEY-006 | Registro de custodios vigente con `len(custodians) == n` y umbral `== t` de `seal-status`; si no, FAIL «no consta quién custodia». |

La ausencia de evidencia nunca es `PASS` (regla 5). `auto_rotate_period` se
informa, pero no sustituye a la fecha real de la última versión.

## Qué no cubre

- Que los algoritmos y tamaños de clave sean los autorizados por el CCN.
- Claves fuera de OpenBao y motores distintos de `kv/` (v2) y `transit/`.
- Que las personas del registro sean las que guardan las partes: el registro
  es una revisión humana.
- Si la política de la app puede ampliarse: el sudo en `sys/audit` y los
  accessors lo ratifica la review de seguridad; si se veta, KEY-002 y KEY-004
  quedan `UNKNOWN`.
