# Conector de Google Workspace

Recoge, en solo lectura, usuarios, administradores, 2SV, eventos de token
OAuth y eventos administrativos de un Google Workspace, y los guarda como
evidencia. Nunca cambia nada en Workspace.

Spec: [collector-workspace](../specs/collector-workspace.md).

## Credencial

1. En Google Cloud, crea una **cuenta de servicio** sin roles de proyecto y
   descarga su clave JSON.
2. En la consola de administración (Seguridad → Acceso y control de datos →
   Controles de API → Delegación en todo el dominio), autoriza el client ID
   de la cuenta de servicio **solo** con estos scopes de lectura:

   ```text
   https://www.googleapis.com/auth/admin.directory.user.readonly
   https://www.googleapis.com/auth/admin.directory.rolemanagement.readonly
   https://www.googleapis.com/auth/admin.reports.audit.readonly
   https://www.googleapis.com/auth/admin.directory.customer.readonly
   ```

   No añadas `admin.directory.user.security`: permite revocar tokens.
3. Elige el administrador al que suplanta la cuenta de servicio
   (`admin_subject`). Los scopes limitan lo que se puede pedir, pero lo que
   Google devuelve depende de los privilegios de ese administrador.

La clave va solo en la variable de entorno; nunca en el YAML ni en Git:

```bash
export ENS_GOOGLE_SERVICE_ACCOUNT_KEY="$(cat cuenta-de-servicio.json)"   # desde el gestor de contraseñas
```

Hace falta el extra opcional: `pip install 'ens-evidence[workspace]'`
(instala `cryptography`, con la que se firma el JWT de la cuenta de
servicio).

### Qué administrador suplantar

Es una decisión de la organización:

- **Rol personalizado de solo lectura** (lectura de usuarios, roles e
  informes): menos privilegio. Google solo muestra a un super admin los
  ajustes de seguridad de otros administradores, así que el estado de 2SV
  de los admins puede no llegar; esos datos quedan `UNKNOWN`.
- **Super admin:** más visibilidad, pero la cuenta de servicio pasa a
  actuar como super admin dentro de los scopes de lectura.

La herramienta nunca escala: lo que el administrador elegido no ve queda
`UNKNOWN`. La matriz exacta de qué campos devuelve cada endpoint a cada tipo
de administrador **no se ha verificado** contra la documentación de Google
ni contra un Workspace real.

## Configuración

En el workspace, `connectors/google-workspace.yaml`:

```yaml
asset_id: google-workspace          # activo identity de scope/assets.yaml
customer: my_customer               # o el id de cliente (C…)
admin_subject: admin@example.com    # administrador suplantado
domains: [example.com]              # dominios esperados
```

## Uso

```bash
ens-evidence collect google-workspace
```

| Código | Significado |
|--------|-------------|
| 0 | Todo recogido |
| 3 | Recogida parcial: alguna sección sin permiso, no soportada o con error |
| 1 | Error global: credencial rechazada, o no se pudo verificar el cliente (cuota, error de Google o red) |

La credencial se comprueba al principio y al final; una revocación entre
medias puede no detectarse al instante.

## Qué se recoge

- **Usuarios:** id, correo principal, suspendido, archivado, admin, admin
  delegado, 2SV inscrita y obligatoria, y si tienen correo o teléfono de
  recuperación (sí/no, nunca el valor).
- **Administradores:** unión de los flags de admin y de las asignaciones de
  rol, incluidas las heredadas por grupo.
- **Eventos de token OAuth (30 días):** apps, scopes y recuento de usuarios
  observados. No es un inventario de apps autorizadas: concesiones
  anteriores no aparecen, y un resultado vacío no prueba que no haya apps.
- **Eventos administrativos (30 días):** solo recuento por tipo de evento.

No se guardan: la clave, el token, teléfonos, correos de recuperación, IPs,
último acceso, parámetros de los eventos ni correos de quien los genera.

## Checks

| Check | `PASS` solo si | `FAIL` si |
|-------|----------------|-----------|
| `workspace.user_2sv_enrolled` | el usuario activo tiene la 2SV inscrita | no la tiene (identifica al usuario) |
| `workspace.admin_2sv_enforced` | el administrador tiene la 2SV obligatoria | no la tiene |
| `workspace.suspended_without_admin_role` | el usuario suspendido no tiene ningún rol de admin, con todas sus asignaciones consultadas | conserva algún rol de admin |

Los dos checks de 2SV solo afirman esos flags de Google: no prueban MFA
resistente a phishing ni el MFA de un proveedor de identidad externo. Los
usuarios archivados no se evalúan en la 2SV inscrita de usuarios activos
(`user_2sv_enrolled`); `admin_2sv_enforced` y `suspended_without_admin_role`
sí los evalúan. Un dato ausente, mal formado o no
visible, un usuario ilegible o una consulta de roles incompleta dan
`UNKNOWN`.

Las limitaciones de la edición contratada aparecen como sin permiso o error;
no se distinguen todavía.
