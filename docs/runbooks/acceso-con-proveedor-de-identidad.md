# Acceso con el proveedor de identidad de la pyme

Cómo hacer que la web de la caja y las herramientas del kit solo admitan el
inicio de sesión de Google Workspace o de Microsoft Entra ID (Microsoft 365), a
través de Cloudflare Access. La caja no lleva proveedor de identidad propio
([ADR 0019](../decisions/0019-caja-y-herramientas-en-un-vps.md)). Se apoya en el
[runbook de la caja](caja.md) (sección 4) y en el de la
[interfaz web](interfaz-web.md) (sección 3).

Fuente: documentación de la API de Cloudflare, 2026-11-15; no probado contra una
cuenta real. **No verificado:** los pasos exactos de la consola de Cloudflare
(los nombres de los menús pueden haber cambiado), si para cada proveedor
conviene OIDC o SAML, y si Access puede exigir que el proveedor haya pedido el
segundo factor.

Instalar no es cumplir. Esta guía no marca ninguna medida por sí sola: la
comprobación técnica ayuda a una persona a decidir, y la conformidad la decide
el responsable del sistema.

## 1. Antes de empezar

Prepara esto antes de tocar nada. Si falta alguna cosa, no sigas.

- El acceso al panel de Cloudflare **no depende del mismo proveedor** que vas a
  configurar: usuario propio de Cloudflare con segundo factor, o una cuenta
  de emergencia guardada en el gestor de contraseñas. Si no, una caída del
  proveedor te deja fuera también de Cloudflare.
- Una clave SSH para entrar al VPS, probada, y la CLI `ens-evidence` funcionando.
- Un segundo administrador de la cuenta de Cloudflare.
- Permiso de lectura de Access en el token del conector
  (`docs/connectors/cloudflare.md`): sin él, la comprobación da desconocido.

## 2. Dar de alta el proveedor en Access

En Cloudflare Zero Trust: Settings, Authentication, Login methods, Add new.
Elige el tipo según el que use la pyme.

- **Google Workspace** (tipo `google-apps` en la API).
- **Microsoft Entra ID** (tipo `azureAD` en la API).

Pasos generales, sin verificar contra una cuenta real:

1. En el proveedor, registra una aplicación para Cloudflare y apunta la URL de
   retorno que Cloudflare muestra en el formulario (el dominio de equipo
   `<equipo>.cloudflareaccess.com`).
2. Copia el identificador y el secreto de cliente a Cloudflare. El secreto va
   solo en ese formulario: nunca en Git, en un chat ni en un fichero de la caja.
3. Si el formulario lo ofrece, limita el inicio de sesión al dominio de la pyme.
4. Pulsa «Test» en Cloudflare e inicia sesión con una cuenta de prueba.

**No verificado:** si Cloudflare recomienda OIDC o SAML para cada proveedor; usa
lo que su formulario ofrezca por defecto y anótalo en el registro de cambios.

## 3. Exigirlo en cada aplicación de la caja y del kit

Por cada aplicación de Access que publique un host de la caja o del kit (la
interfaz, el panel, las herramientas):

1. Abre la aplicación, pestaña de métodos de inicio de sesión.
2. Marca **solo** el proveedor de la pyme. Una lista explícita
   (`allowed_idps` en la API) es obligatoria: con la lista vacía, Access admite
   todos los proveedores de la cuenta.
3. **Quita el código de un solo uso por correo** (`onetimepin`) y cualquier
   proveedor personal (Google, Facebook, LinkedIn). Quítalos también de la
   cuenta si ninguna aplicación los necesita.
4. Revisa las políticas: ninguna `Bypass`, ninguna `Allow` que deje entrar a
   cualquiera sin pasar por el proveedor.
5. Excepción: los hostnames de agentes osquery (`public_routes`) no llevan Access;
   se quedan como están (caja.md, sección 4.1).

## 4. Doble factor

Access no demuestra que el proveedor haya pedido el segundo factor. Se fuerza en
el proveedor, que es donde se evidencia:

- **Google Workspace:** verificación en dos pasos obligatoria para todos los
  usuarios de la organización.
- **Microsoft Entra ID:** acceso condicional que exija MFA a las cuentas
  autorizadas, sin excepciones.

Después, recoge la evidencia del propio proveedor (`collector-workspace`,
`collector-m365`).

## 5. Comprobarlo

```bash
ens-evidence collect cloudflare
ens-evidence check --all
```

El check `cloudflare.access_idp_restricted` mira cada aplicación de Access que
publica un host de los túneles de la cuenta (menos las `public_routes`
declaradas) y qué métodos de inicio de sesión admite.

| Resultado | Código | Qué significa y qué hacer |
|-----------|--------|---------------------------|
| PASS | | La aplicación solo admite un proveedor de directorio, el nombrado en el motivo. **No prueba** que sea el de la pyme ni que exija doble factor: una persona comprueba en la consola que ese proveedor es el de su organización. |
| FAIL | `otp_or_consumer_idp` | Admite código por correo o un proveedor personal. Quítalo (sección 3). |
| FAIL | `bypass` | Hay una política `Bypass`. Sustitúyela por una política `Allow` ligada al proveedor de la pyme (sección 3). |
| Desconocido | `permission_denied` | El token no puede leer los proveedores. Añade el permiso de Access. |
| Desconocido | `no_identity_providers` | La cuenta no tiene proveedores de identidad: sección 2. |
| Desconocido | `idp_review` | Una persona revisa la aplicación. Se emite si: admite SAML, OIDC u otro proveedor, o más de un directorio; no tiene ninguna política `Allow` o sus `Allow` no admiten ningún proveedor; una política `Deny` menciona un método de código o personal; hay una política con decisión, selector o grupo de Access desconocido, sin recoger o en ciclo; o `allowed_idps` es ilegible o cita un proveedor que no consta en la cuenta. |
| Desconocido | `non_identity` | Hay acceso por token de servicio. Una persona confirma que es solo para máquinas. |
| Desconocido | `scope_incomplete` | Faltan datos de túneles, aplicaciones o políticas. Corrige el permiso o el destino ilegible y recoge de nuevo. |
| Desconocido | `stale_evidence` | La recogida es anterior a este check. Recoge de nuevo. |

Sin aplicaciones en el alcance no hay resultados: nunca se declara PASS sin
evidencia. El vínculo a una medida (op.acc.6) lo declara una persona con
`ens-evidence matrix link`; ver la tabla de cláusulas en
`docs/connectors/cloudflare.md`.

## 6. Si el proveedor cae: acceso de emergencia

La web **sigue cerrada**. Por orden:

1. **SSH con clave al VPS y la CLI.** Casi todo lo urgente se hace así:
   `deploy/box.sh status`, evidencias, restaurar. No abre nada a Internet.
2. **Solo si la web es imprescindible**, y con la aprobación del responsable del
   sistema, añade temporalmente el código por correo **solo en la aplicación de
   la web**, con una política que admita una o dos direcciones con nombre.
   Nunca «todos», nunca un dominio entero, nunca `Bypass`. Para ello, vuelve a
   activar el método «One-time PIN» en la cuenta (sección 3) y quítalo de la
   cuenta al terminar.
   **Vigencia máxima:** duración de sesión la más baja que permita la consola y
   un plazo fijo de 24 h fijado en la política (caducidad). Si hace falta más,
   el responsable del sistema lo renueva de forma explícita y la renovación se
   anota en el registro del incidente (`ens-evidence incident`). Al vencer el
   plazo se retira, aunque el proveedor no haya vuelto; luego se renueva si
   procede.
3. Regístralo como incidente, antes o durante:

   ```bash
   ens-evidence incident new --title "Acceso de emergencia sin proveedor de identidad" \
     --detected-at <fecha-hora-ISO> --by "<Nombre Apellido>" \
     --severity medio --asset <activo> --personal-data unknown
   ```

4. Al volver el proveedor, o al vencer el plazo, **retira** el código por correo
   (en la aplicación y en la cuenta) y las direcciones temporales, y ejecuta `ens-evidence collect cloudflare` y
   `ens-evidence check --all`. Mientras el código esté puesto, el check da FAIL:
   es lo esperado y avisa de que falta retirarlo.
5. El responsable del sistema revisa lo ocurrido y cierra el incidente
   (`ens-evidence incident close`, ver
   [registro de incidentes](registro-de-incidentes.md)).

## 7. Rollback

- **Quitar el proveedor de una aplicación** (volver al estado anterior): en la
  aplicación, vuelve a marcar el método anterior y guarda. Hazlo solo si el
  proveedor nuevo impide entrar y has seguido la sección 6.
- **Borrar el proveedor de la cuenta:** solo si ninguna aplicación lo usa; si no,
  Access deja a esas aplicaciones sin método de entrada.
- Tras cualquier cambio: `ens-evidence collect cloudflare` y
  `ens-evidence check --all`, y anota fecha y motivo en el registro de cambios.
- Recuperación: el panel de Cloudflare con la cuenta de emergencia (sección 1)
  o SSH con clave. La caja no pierde datos por cambiar Access: solo cambia
  quién puede llegar a ella.
