# Kit de contraseñas: gestor del equipo con Vaultwarden

Cómo activar, dar de alta, operar y recuperar el perfil `contrasenas` (spec
[kit-passwords](../specs/kit-passwords.md), [ADR 0019](../decisions/0019-caja-y-herramientas-en-un-vps.md)).
Se apoya en el [runbook de la caja](caja.md) y en el [kit de copias](kit-copias.md).

Un gestor de contraseñas no es el mecanismo de autenticación de los sistemas
(op.acc.6) y instalarlo no cumple nada. La herramienta mide dos cosas del propio
gestor: si cada usuario activo tiene un segundo factor real y si hay cuentas sin
actividad por encima del umbral de la política. Detectar la inactividad no
suspende la cuenta. La conformidad la decide una persona.

## Qué se despliega

- `vaultwarden` (perfil `contrasenas`): Vaultwarden 1.37.5 (AGPL-3.0), imagen
  fijada por digest en `compose.yaml`. Corre como root del contenedor pero sin
  ninguna capacidad (`cap_drop: [ALL]`, `no-new-privileges`, `read_only`), sin puertos
  publicados y sin Internet: su única red es `contrasenas-net` (interna), con
  Caddy como único vecino. `SIGNUPS_ALLOWED=false` (solo por invitación),
  `INVITATIONS_ALLOWED=true`, `DISABLE_ICON_DOWNLOAD=true` y
  `DOMAIN=https://<ENS_CONTRASENAS_HOST>`.
- `contrasenas-export`: el exportador. Imagen de la app, UID 10001, `network_mode:
  none`, `read_only`, `cap_drop: [ALL]`, sin `env_file` ni el volumen de datos de la
  app. Cada 15 minutos lee la base de Vaultwarden (volumen en solo lectura) y
  publica `usuarios.json` (id interno, activo, invitado, segundo factor, última
  actividad; sin emails, nombres ni cofres) y una instantánea de la base y de
  `rsa_key*`.
- La app solo monta `contrasenas-export` en `/run/kit/contrasenas`, en solo
  lectura. No monta `vaultwarden-data` ni alcanza `contrasenas-net`: no tiene
  credenciales de Vaultwarden y no ve los cofres.
- Volúmenes: `ens-box_vaultwarden-data` (cuentas y cofres cifrados),
  `ens-box_contrasenas-export` y `ens-box_contrasenas-snapshot`.
- Segundo factor: cuentan los métodos reales activados (TOTP, correo, Duo,
  YubiKey, U2F, WebAuthn). No cuentan «recordar dispositivo» ni el código de
  recuperación.
- RAM en reposo, una sola medida con `docker stats --no-stream` unos segundos tras
  arrancar, con 3 usuarios de prueba: Vaultwarden 29,67 MiB y exportador 50,96 MiB.
  No es un promedio ni se ha medido con una base grande.

## Alta

1. En el asistente `/herramientas/contrasenas` indica el host y los días de
   inactividad de la política, y descarga el paquete (`.tar.gz` con
   `passwords.yaml` y `MANIFEST`; sin secretos).
2. Define `ENS_CONTRASENAS_HOST=contrasenas.example.org` en `deploy/ui.env` y crea
   el hostname del túnel y la aplicación de Access con MFA, como en la sección 2
   del [kit de copias](kit-copias.md).
3. Activa el perfil:

   ```bash
   deploy/box.sh kit enable contrasenas /ruta/al/paquete.tar.gz
   ```

   `box.sh` rechaza el paquete si trae ficheros de más o si el sha256 del
   `MANIFEST` no coincide. Arranca Vaultwarden (sin panel de administración) y el
   exportador.
4. Abre el alta (el panel de administración existe solo mientras dure):

   ```bash
   deploy/box.sh kit contrasenas alta
   ```

   Genera un token aleatorio, guarda en `deploy/kit/contrasenas/admin.env` (modo
   600, fuera de Git) solo su hash Argon2id, recrea Vaultwarden con él y muestra
   el token **una sola vez**. Guárdalo donde guardas los secretos de la caja. Si el
   comando falla, no muestra ningún token: ejecuta `cerrar-alta` y repítelo. Con
   un alta ya abierta, `alta` se niega a repetirse; si perdiste el token, cierra
   y vuelve a abrir.
5. Entra en `https://<ENS_CONTRASENAS_HOST>/admin` con el token, invita por correo
   al primer propietario (sin SMTP no se envía nada: la invitación queda
   registrada) y pídele que cree su cuenta en la web.
6. Cuando se haya registrado, **cierra el alta**:

   ```bash
   deploy/box.sh kit contrasenas cerrar-alta
   ```

   Borra `admin.env` y el override, recrea Vaultwarden y comprueba dos cosas: que el
   contenedor ya no tiene `ADMIN_TOKEN` y que `/admin` responde «The admin panel
   is disabled» (el código HTTP es 200 abierto y cerrado: solo el cuerpo lo
   distingue). Si falla, lo dice y no da el alta por cerrada: repítelo.
   `deploy/box.sh status` avisa mientras haya un alta abierta.
7. Pide a cada persona que active un segundo factor en su cuenta (Ajustes >
   Seguridad > Verificación en dos pasos). Sin eso el check falla.

Verificado con Vaultwarden real (2026-11-11, datos ficticios): invitar desde el
panel, registrar por la API de identidad, activar TOTP, cerrar el alta y que el
panel quede desactivado y las cuentas sigan entrando.

## Invitar a más personas

- Con el alta abierta, desde `/admin` (Invite User).
- Con el alta cerrada, el panel no existe. Un propietario de organización puede
  invitar desde la web de Vaultwarden, pero **no está verificado** que el
  propietario pueda crear la organización e invitar con `SIGNUPS_ALLOWED=false` sin
  pasar por el panel, ni que la web ofrezca «Crear cuenta» a quien solo tiene una
  invitación sin correo. Pruébalo con una cuenta de prueba antes de depender de
  ello; si no funciona, abre un alta, invita y ciérrala.
- Quien se va: bórralo desde `/admin` con el alta abierta. Un usuario inactivo
  sigue figurando hasta entonces y el check lo mide.

## Higiene del token

- El token en claro no se escribe en disco: solo su hash, en
  `deploy/kit/contrasenas/admin.env` (modo 600, ignorado por Git).
- `box.sh` se niega a usar `admin.env` o el override si son enlaces simbólicos, si
  no son tuyos, si son legibles por otros o si el override no es el que escribió
  `alta`.
- Un alta dura lo que tardes en invitar. No dejes el panel abierto.
- El panel puede borrar usuarios, quitar el segundo factor y cambiar la
  configuración: trátalo como una credencial de administración de la caja.
- Nada de esto va a Git ni a registros; las copias del directorio `deploy/` no
  deben llevarse `admin.env`.

## Copias y restauración

- La instantánea (`db-<fecha>.sqlite3` y `rsa_key.pem`, modo 600, UID 10001) se
  publica en `contrasenas-snapshot` en cada vuelta, solo si `integrity_check` da
  ok; se conserva la última buena. Backrest (también UID 10001) la monta en
  `/source/contrasenas`, en solo lectura. El check «instantánea reciente» falla
  si la última buena es antigua.
- **Pendiente:** el asistente de copias todavía no ofrece `contrasenas` como origen
  (`ALLOWED_VOLUMES` de `kit/backups.py`). Hasta entonces, añade `/source/contrasenas` al
  plan en la interfaz de Backrest (no verificado).
- **No entran** en la instantánea los adjuntos, los sends ni `config.json` de
  Vaultwarden. Los adjuntos y sends son ficheros cifrados de los usuarios en
  `vaultwarden-data`; si se usan, se pierden con la instantánea sola. Decisión: la
  instantánea cubre lo imprescindible para volver a entrar (cuentas, cofres y la
  clave de firma de sesiones); no se monta `vaultwarden-data` en Backrest porque
  contiene la base viva en WAL, que no se copia de forma coherente. No uses
  adjuntos ni sends con este kit hasta cubrirlos.
- Restauración (en un VPS nuevo o sobre un volumen vacío): restaura la copia de
  Backrest de `/source/contrasenas` (procedimiento de la sección 4 de
  [kit-copias](kit-copias.md)), copia `db-<fecha>.sqlite3` como `db.sqlite3` y
  `rsa_key.pem` en el volumen `vaultwarden-data` (con el contenedor parado) y
  arranca el perfil. Prueba hecha (2026-11-11): instantánea copiada a un volumen
  vacío, Vaultwarden nuevo arrancado sobre él y login correcto de una cuenta
  existente. **No verificado:** restaurar desde Backrest en un VPS nuevo, los
  adjuntos y que sin `rsa_key.pem` solo se invaliden las sesiones. Registra tu prueba
  (fecha, quién, resultado) como prueba de restauración
  ([restauración de evidencias](restauracion-de-evidencias.md)).

## Actualizar la imagen

1. Elige la versión nueva de Vaultwarden y verifica su digest
   (`docker buildx imagetools inspect vaultwarden/server:<versión>-alpine`).
2. Cambia la imagen y el digest en `compose.yaml` y la lista `KNOWN_MIGRATIONS` de
   `kit/passwords_export.py` si Vaultwarden añade migraciones: con una migración
   desconocida el exportador deja de publicar `usuarios.json` (a propósito) y la
   tarjeta sale sin datos.
3. Haz una release y `deploy/box.sh update vX.Y.Z`. Haz antes una copia y lee las
   notas de la versión de Vaultwarden.

## Desactivar

`deploy/box.sh kit disable contrasenas` para y quita los dos contenedores, cierra
en disco un alta abierta y renombra `MANIFEST` a `MANIFEST.disabled`. No borra
los volúmenes (`vaultwarden-data`, `contrasenas-export`, `contrasenas-snapshot`)
ni `connectors/passwords.yaml`. Caddy responde 503 en `ENS_CONTRASENAS_HOST`.

## Clientes nativos y Cloudflare Access

La web va detrás de Access con MFA. La extensión de navegador y las apps móviles
de Bitwarden hablan directamente con el servidor y **quizá no puedan pasar
Access** (no llevan la cookie de Cloudflare). No está verificado. Si no pasan,
las opciones son una política de Access de tipo Service Auth para esos clientes
o usar solo la web; decídelo con la persona responsable de seguridad.

## Si la tarjeta dice…

- **Con problemas.** Falta el segundo factor en algún usuario (el detalle da su id
  interno, no su correo), hay cuentas sin actividad por encima del umbral o la
  instantánea es antigua. Esto último: `docker compose -p ens-box logs contrasenas-export`.
- **Sin datos.** No hay `usuarios.json` reciente: el exportador no corre, la
  migración de Vaultwarden no está en la lista conocida o no hay usuarios activos
  registrados. Mira los registros del exportador.
- **Caída.** Comprueba `deploy/box.sh status` y
  `docker compose -p ens-box logs vaultwarden`.
