# Acceso con el proveedor de identidad de la pyme

Cómo hacer que la web de la caja y las herramientas del kit solo admitan el
inicio de sesión de Google Workspace o de Microsoft Entra ID (Microsoft 365), a
través de Cloudflare Access. La caja no lleva proveedor de identidad propio
([ADR 0019](../decisions/0019-caja-y-herramientas-en-un-vps.md)). Se apoya en el
[runbook de la caja](caja.md) (sección 4) y en el de la
[interfaz web](interfaz-web.md) (sección 3).

Fuente: documentación oficial de Cloudflare (API y guías de cada proveedor,
enlazadas en cada sección), verificada el 2026-11-15; no probado contra una
cuenta real. **No verificado:** los nombres de los menús de la consola que la
documentación no da (pueden haber cambiado), y si Access puede exigir que el
proveedor haya pedido el segundo factor.

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

Sigue solo el procedimiento del proveedor que use la pyme. Los pasos, campos y
menús de esta sección están **verificados en la documentación oficial de
Cloudflare el 2026-11-15; no probados contra una cuenta real**. Los menús de
Google y de Microsoft son los que cita esa documentación y pueden haber
cambiado.

En ambos casos necesitas el nombre de equipo de Cloudflare (`<equipo>`), que
está en Zero Trust, Settings, Team name and domain. La URL de retorno es
siempre `https://<equipo>.cloudflareaccess.com/cdn-cgi/access/callback`.

Los secretos de cliente van **solo** en el formulario de Cloudflare: nunca en
Git, en un chat, en el registro de cambios ni en un fichero de la caja.

### 2.1 Google Workspace (tipo `google-apps` en la API)

Fuente: [Google Workspace como proveedor de Access](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/google-workspace/).

En la consola de Google Cloud (no es la consola de administración de
Workspace):

1. Crea un proyecto o usa uno existente.
2. En APIs & Services, Enable APIs and Services, activa **Admin SDK API**
   (Access la usa para leer los grupos).
3. En APIs & Services, Credentials, **Configure Consent Screen**: nombre de la
   aplicación, correo de soporte y audiencia **Internal**. Internal limita el
   inicio de sesión a usuarios de la organización de Workspace y deja fuera las
   cuentas Gmail personales.
4. **Create OAuth Client**, tipo **Web application**:
   - Authorized JavaScript origins: `https://<equipo>.cloudflareaccess.com`.
   - Authorized redirect URIs:
     `https://<equipo>.cloudflareaccess.com/cdn-cgi/access/callback`.
5. Guarda y copia el **Client ID** y el **Client secret**.

En la consola de administración de Google Workspace:

6. Security, Access and data control, API controls, Settings: en Internal apps
   activa **Trust internal apps**. Viene desactivado y la integración lo
   necesita.

En Cloudflare Zero Trust, Integrations, Identity providers, **Add new identity
provider**, Google Workspace:

7. **App ID**: el Client ID. **Client Secret**: el secreto.
   **Google Workspace domain**: el dominio de la pyme (`apps_domain` en la API).
8. Opcional: activa **PKCE**.
9. Guarda y abre el enlace que genera Cloudflare: un administrador de Workspace
   inicia sesión y autoriza a Access a leer los grupos.
10. Pulsa **Test**: deben aparecer tu identidad y tus grupos. Si pruebas antes
    del paso 9, sale «Failed to fetch user/group information».

Según la misma página: los cambios de grupos pueden tardar hasta 24 h en verse,
y la integración no funciona si la propia cuenta de Google Workspace está
protegida con Access.

### 2.2 Microsoft Entra ID (tipo `azureAD` en la API)

Fuente: [Microsoft Entra ID como proveedor de Access](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/entra-id/).

En el centro de administración de Microsoft Entra:

1. Applications, Enterprise applications, New application, **Create your own
   application**. Ponle nombre y elige «Register an application to integrate
   with Microsoft Entra ID (App you're developing)», no una de la galería.
2. En Redirect URI, plataforma **Web**:
   `https://<equipo>.cloudflareaccess.com/cdn-cgi/access/callback`.
   Pulsa Register. La documentación no indica qué tipo de cuentas admitidas
   elegir: **no verificado**.
3. En App registrations, All applications, abre la aplicación y copia el
   **Application (client) ID** y el **Directory (tenant) ID**.
4. En Client credentials, crea un secreto con **New client secret**:
   ponle nombre y caducidad, y copia el **Value** en ese momento (después no se
   vuelve a ver). Anota la fecha de caducidad en el registro de cambios: cuando
   caduca, nadie puede entrar por Access.
5. API permissions, Microsoft Graph, permisos **delegados**: `email`,
   `offline_access`, `openid`, `profile`, `User.Read`, `Directory.Read.All` y
   `GroupMember.Read.All`. Add permissions y luego **Grant admin consent**. La
   documentación dice que es el conjunto probado; uno más estrecho podría
   bastar, sin verificar.

En Cloudflare Zero Trust, Integrations, Identity providers, **Add new identity
provider**, Azure AD:

6. **Application (client) ID**, **Client secret** y **Directory (tenant) ID**
   (`client_id`, `client_secret` y `directory_id` en la API).
7. Opcional: **PKCE**; **Support Groups** si las políticas van a usar grupos
   de Entra; **Email claim** (por ejemplo `preferred_username`) si los UPN no
   coinciden con las direcciones de correo.
8. Guarda y pulsa **Test** con una cuenta de prueba.

### 2.3 Segundo factor en el inicio de sesión

Ninguna de las dos páginas dice si Access lee o exige la reclamación de
autenticación multifactor (`amr`) del proveedor: **no verificado**. La página
de Entra ID menciona una opción «Entra ID Policy Sync» que remite a una guía de
acceso condicional que esta guía no ha revisado. Por eso el doble factor se
fuerza y se evidencia en el proveedor (sección 4).

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
| Desconocido | `idp_review` | Una persona revisa la aplicación. Se emite si: admite SAML, OIDC u otro proveedor, o más de un directorio; no tiene ninguna política `Allow` o sus `Allow` no admiten ningún proveedor; una política `Deny` menciona un método de código o personal; hay una política con decisión, selector o grupo de Access desconocido, sin recoger o en ciclo; `allowed_idps` es ilegible o cita un proveedor que no consta en la cuenta; o `allowed_idps` falta o es `null` en la respuesta de Cloudflare («la respuesta no indica qué proveedores admite la aplicación»). |
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
   sistema, abre temporalmente el código por correo **solo en la aplicación de
   la web**, para una o dos direcciones con nombre.
   Nunca «todos», nunca un dominio entero, nunca `Bypass`.

   Antes de abrirlo, comprueba que el buzón que recibirá el código funciona
   durante la caída. Si ese buzón depende del mismo proveedor (un correo de
   Workspace o de Microsoft 365), el código no llegará: este paso no sirve y
   te quedas con el SSH.

   Pasos:

   1. Nombra a una persona responsable del acceso de emergencia y fija el
      **plazo de retirada: 24 h** desde la apertura, con fecha y hora.
   2. Regístralo como incidente **antes** de abrir nada. El título va sin datos
      personales; el responsable, las personas admitidas y la fecha y hora de
      retirada van en el análisis inicial (`--summary`):

      ```bash
      ens-evidence incident new --title "Acceso de emergencia sin proveedor de identidad" \
        --detected-at <fecha-hora-ISO> --by "<Nombre Apellido>" \
        --severity medio --asset <activo> --personal-data unknown \
        --summary "Responsable: <Nombre Apellido>. Admitidos: <personas>. Retirada antes de <fecha-hora-ISO>."
      ```

   3. Activa el método «One-time PIN» en la cuenta y añádelo a los métodos de
      inicio de sesión de la aplicación de la web (`allowed_idps`), solo en
      esa aplicación.
   4. Crea en esa aplicación una política `Allow` de emergencia con
      **Include** = las direcciones de correo concretas (selector de correo,
      una por persona con nombre) y **Require** = método de inicio de sesión
      «One-time PIN» (`login_method` en la API). El código por correo nunca va
      como rama `Include` suelta: una `Include` de «One-time PIN» dejaría
      entrar a cualquier dirección que reciba el código.
   5. Si la consola lo permite, pon a esa política una duración de sesión
      corta. La duración de sesión **no caduca la política**: solo obliga a
      volver a evaluar la política al vencer la sesión
      ([gestión de sesiones](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/)).
      Cloudflare no documenta una fecha de caducidad para las políticas: el
      plazo de 24 h lo cumple la persona responsable retirándola a mano.

   Si hace falta más de 24 h, el responsable del sistema lo renueva de forma
   explícita y la renovación con su nuevo plazo se anota en el incidente
   (`ens-evidence incident`). Al vencer el plazo se retira, aunque el proveedor
   no haya vuelto.

   **Autenticación temporal de Cloudflare:** Cloudflare tiene una función de
   [autenticación temporal](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/temporary-auth/)
   en las políticas `Allow`: quien pide acceso da un motivo y una persona
   aprobadora lo concede hasta un máximo de 24 h. Las aprobadoras tienen que
   estar autenticadas por Access; si el proveedor está caído, puede que tampoco
   puedan entrar. Su documentación no aclara cómo se identifica quien pide
   acceso sin proveedor. No sustituye a los pasos anteriores y esta guía no la
   ha probado.
3. Al volver el proveedor, o al vencer el plazo,
   **retira** el código por correo:

   1. Borra la política `Allow` de emergencia.
   2. Quita «One-time PIN» de los métodos de la aplicación (`allowed_idps`).
   3. Quita el método «One-time PIN» de la cuenta si ninguna otra aplicación
      lo necesita (sección 3).
   4. En la aplicación, «Revoke existing tokens» cierra las sesiones abiertas
      con el código
      ([gestión de sesiones](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/)).
   5. Comprueba la retirada: `ens-evidence collect cloudflare` y
      `ens-evidence check --all`. Mientras el código esté admitido, el check
      `cloudflare.access_idp_restricted` da FAIL (`otp_or_consumer_idp`): es
      lo esperado y avisa de que falta retirarlo. Anota en el incidente la hora
      de la retirada y el resultado del check.
4. El responsable del sistema revisa lo ocurrido y cierra el incidente
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
