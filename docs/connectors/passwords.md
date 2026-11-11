# Conector `passwords`

Lee, en solo lectura, el export de usuarios del gestor de contraseñas del kit
(Vaultwarden) y evalúa los checks PWD-001..003. Spec:
[kit-passwords](../specs/kit-passwords.md). Es evidencia de apoyo para
op.acc.6 que una persona enlaza: no declara conformidad y no sustituye al
mecanismo de autenticación de los sistemas (op.acc.6 lo cubre cada sistema).

## Qué lee y qué no

La app no tiene credenciales de Vaultwarden ni ve su base (contiene los
cofres). Solo lee el JSON que vuelca el exportador del kit
(`ens-evidence kit contrasenas export`) en un volumen que la app monta de solo
lectura: `/run/kit/contrasenas/usuarios.json`.

El fichero solo lleva, por usuario, un id interno, si está activo, si está
invitado, el segundo factor, la fecha de alta y la última actividad. Nunca
email, nombre, claves ni cofres. El modelo (`PasswordsExport`, en
`kit/passwords_export.py`) rechaza cualquier campo extra.

El conector abre el fichero sin seguir symlinks, exige un fichero regular de
hasta 2 MiB y lo valida entero. Un export rechazado no se guarda ni se cita:
la evidencia lleva solo un código fijo.

## Configuración

`connectors/passwords.yaml`, sin secretos:

```yaml
asset_id: vaultwarden                         # activo `application` del inventario
export_path: /run/kit/contrasenas/usuarios.json   # por defecto
inactive_days: 90                             # obligatorio, > 0: lo fija la política
max_export_age_minutes: 60                    # obligatorio, > 0
```

`kit/passwords_setup.py` genera el paquete `contrasenas/passwords.yaml` con
`max_export_age_minutes: 60`: el exportador corre cada 15 minutos, cuatro
vueltas de margen.

## Estados de la recogida

Sin un export válido y reciente la recogida queda en `ERROR` y los tres checks
dan `ERROR`: nunca `PASS`. Códigos en `data.error`:

| Código | Causa |
|--------|-------|
| `export_missing` | el fichero no existe |
| `export_unreadable` | no se puede abrir (permisos, symlink) |
| `export_not_regular` | no es un fichero regular |
| `export_too_large` | supera 2 MiB |
| `export_invalid` | JSON no válido, campos extra, formato desconocido, booleanos no estrictos, ids repetidos o no UUID, fechas sin zona UTC o posteriores al export |
| `export_stale` | `exported_at` más antiguo que `max_export_age_minutes` |
| `export_future` | `exported_at` posterior al reloj del conector |

## Checks

Todos `SELF_REPORTED`, un resultado por recogida. La población evaluable son
los usuarios activos y no invitados; si no hay ninguno, `UNKNOWN`.

| Check | Id | Qué comprueba |
|-------|----|---------------|
| `passwords.second_factor` | PWD-001 | Todos tienen un segundo factor real (TOTP, email, Duo, YubiKey, U2F, WebAuthn; no «recordar dispositivo» ni código de recuperación). FAIL lista solo ids internos. |
| `passwords.no_inactive_accounts` | PWD-002 | Ninguno lleva más de `inactive_days` sin actividad. Sin actividad registrada se mide desde `created_at` y el texto lo dice. Justo en el umbral no es inactivo. Detectar la inactividad no suspende la cuenta. |
| `passwords.snapshot_recent` | PWD-003 | La instantánea para la copia salió bien (`snapshot_ok`) y la última buena no va más de `max_export_age_minutes` por detrás del export. La instantánea se hace en la misma vuelta que el export, así que el mismo margen vale. |

Fechas ilegibles o posteriores al momento de evaluar dan `UNKNOWN`.

## Qué no cubre

- La rotación o la complejidad de las credenciales de los sistemas
  (op.acc.6.4, op.acc.6.r1.2): el gestor ayuda a generarlas y cambiarlas, pero
  los checks no lo miden.
- Que el doble factor del gestor sea el de los sistemas (op.acc.6.r2.1).
- La configuración de Vaultwarden (registro cerrado, panel de administración
  desactivado): la app no ve su entorno.
- Que se suspendan las cuentas inactivas: lo decide una persona.
