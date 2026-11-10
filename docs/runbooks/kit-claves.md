# Kit de claves: almacén de claves y secretos con OpenBao

Cómo activar, desbloquear, operar y recuperar el perfil `claves` (spec
[kit-secrets](../specs/kit-secrets.md), [ADR 0019](../decisions/0019-caja-y-herramientas-en-un-vps.md)).
Se apoya en el [runbook de la caja](caja.md) y en el [kit de copias](kit-copias.md).

Instalar OpenBao no cumple op.exp.10: la evidencia sale de lo que el almacén
tiene de verdad (auditoría activa, claves con rotación dentro de la política,
sin root token vivo, almacén desbloqueado) y de que una persona revise el
registro de custodios. La herramienta no declara conformidad.

## Qué se despliega

- Servicio `openbao` (perfil `claves`): OpenBao 2.7.2 (imagen distroless fijada
  por digest en `compose.yaml`), con almacenamiento integrado (raft) y auditoría
  en fichero activada desde el arranque.
- Corre con `user: 10001:10001`, `read_only`, `cap_drop: [ALL]`,
  `no-new-privileges`, sin puertos publicados. La imagen no tiene shell ni curl:
  no hay healthcheck, y `deploy/box.sh status` solo dice si el contenedor corre y
  si está sellado.
- Volúmenes: `ens-box_openbao-data` (las claves y secretos cifrados) y
  `ens-box_openbao-audit` (el registro de auditoría). Ningún otro servicio los
  monta, salvo `backrest` en solo lectura si los incluyes en las copias.
- Redes: `claves-net` (la alcanza Caddy) y `claves-app` (interna; la usa la app
  para leer metadatos).
- RAM: 23,31 MiB en reposo, con el almacén desbloqueado y sin datos (una sola
  medida con `docker stats --no-stream`, justo tras arrancar; no es un promedio y
  crecerá con los datos).
- La app lee con un token de solo metadatos, de 72 h y renovable, creado por el
  rol `ens-app`: no puede leer valores de secretos ni material de claves
  (comprobado: OpenBao responde 403 a `kv/data/x` y a `transit/export/...`). El
  root token se revoca al terminar la inicialización y deja de existir.
- La interfaz web de OpenBao queda publicada en `ENS_CLAVES_HOST` (Access con MFA)
  solo para administrar. Caddy quita `Cookie` y `Cf-Access-*`; la interfaz usa
  la cabecera `X-Vault-Token`, no cookies.

## Aviso: sin las partes, no hay vuelta atrás

El almacén se desbloquea con N partes repartidas entre custodios; hacen falta M
(el umbral) para abrirlo. **Si se pierden partes y quedan menos de M, los
secretos y claves guardados se pierden para siempre.** Nadie puede recuperarlos,
tampoco quien mantiene el kit. Las partes se muestran una sola vez al inicializar.
Reparte las partes a personas distintas, que las guarden en sitios distintos y
fuera del VPS.

## Antes de empezar

- Los custodios (al menos N personas, cada una con su parte por recibir) están
  presentes y en la misma sala. Ninguna parte viaja por correo ni chat.
- Usa una sesión de terminal directa en el VPS (SSH normal o consola). **No uses
  `script`, `tmux` o `screen` con registro, ni grabación de pantalla**: las partes
  se muestran en pantalla y quedarían escritas en el registro.
- Cierra la ventana del terminal al terminar, para que no queden las partes en
  el historial de pantalla.
- Define `ENS_CLAVES_HOST=claves.example.org` en `deploy/ui.env` y crea el hostname
  del túnel y la aplicación de Access con MFA, como en la sección 2 del
  [kit de copias](kit-copias.md).

## Alta

1. En el asistente `/herramientas/claves` fija N y M y descarga el paquete (un
   `.tar.gz` con `openbao.hcl`, `init.json` y `MANIFEST`; no lleva secretos).
2. Activa el perfil:

   ```bash
   deploy/box.sh kit enable claves /ruta/al/paquete.tar.gz
   ```

   `box.sh` rechaza el paquete si trae ficheros de más, si un sha256 del
   `MANIFEST` no coincide o si `openbao.hcl` no es el de la release en uso. OpenBao
   arranca sin inicializar.
3. Inicializa (los custodios, presentes):

   ```bash
   deploy/box.sh kit claves init
   ```

   El proceso pide escribir `ENTIENDO` tras el aviso de irrecuperabilidad.
   Después muestra las partes **una a una**: entrega cada parte a su custodio,
   que la anota; con Intro se borra la pantalla y pasa a la siguiente. Al final
   pide M partes para desbloquear y configura el almacén. El root token no sale del
   contenedor: se revoca y se comprueba que ya no vale.
4. Al terminar, `box.sh` muestra **una sola vez** la contraseña del usuario
   `admin` del almacén: guárdala en el gestor de contraseñas. El token de la app
   se escribe en `deploy/.env` (modo 600) sin mostrarse. Si la contraseña se pierde, otro
   administrador con el quórum puede generar un root (sección «Generar un root»).
5. Registra a los custodios en `/herramientas/claves/custodios`: nombres y desde
   cuándo, nunca las partes. Es revisión humana: la web no puede saber si
   alguien guarda de verdad su parte.
6. Cierra la ventana del terminal. Comprueba con `deploy/box.sh status` y en la
   tarjeta de Claves de `/herramientas` en la siguiente vuelta del scheduler.

## Desbloqueo

Tras reiniciar el VPS, actualizar o recrear el contenedor, OpenBao arranca
**sellado**: no sirve nada hasta que M custodios aporten su parte. La tarjeta
sale como «Sellado» y `box.sh status` lo dice.

1. Reúne a M custodios, con sesión de terminal directa (igual que en «Antes de
   empezar»).
2. Ejecuta:

   ```bash
   deploy/box.sh kit claves unseal
   ```

3. Cada custodio teclea su parte cuando se pida (no se ve al escribirla). Al
   acabar dice «Almacén desbloqueado». Si tras M intentos sigue sellado, el proceso descarta lo
   tecleado y falla: repite el comando.
4. Cierra la ventana.

**Nunca teclees partes en la interfaz web de OpenBao**: la conexión termina en
Cloudflare y las partes pasarían por ahí. La web de OpenBao es solo para
administrar una vez desbloqueado.

## Actualizar y volver atrás

`deploy/box.sh update vX.Y.Z` y `deploy/box.sh rollback` recrean OpenBao solo si
cambia su configuración en la release (se compara el hash de Compose). Cuando
lo recrearían, el almacén queda sellado y los comandos piden confirmación:
escribe `SELLAR` o pasa `--seal-ok`. Sin terminal y sin `--seal-ok`, se paran
antes de tocar nada. Si no cambia, no lo recrean y no piden nada. Ten a M
custodios disponibles antes de confirmar y desbloquea después (sección
«Desbloqueo»).

## Rotación

Se hace con el usuario `admin` en la interfaz web de OpenBao (`ENS_CLAVES_HOST`):

- **Claves de transit.** Abre la clave y pulsa Rotate: se crea una versión nueva y
  las anteriores siguen sirviendo para descifrar. O fija una rotación automática
  en la configuración de la clave (`auto_rotate_period`). Así el conector ve la
  fecha de rotación de la política.
- **Secretos kv.** Escribe un valor nuevo en el mismo secreto: kv v2 crea una
  versión nueva y conserva las anteriores.
- **Contraseña de `admin`.** Cámbiala en el gestor y en OpenBao (la política de
  `admin` lo permite en `auth/userpass/users/admin/password`).

El periodo aceptable lo fija la organización en `connectors/keystore.yaml`
(`max_key_age_days`); no hay un valor por defecto.

## Pérdida de un custodio

Si una persona se va o pierde su parte, la parte vieja sigue valiendo hasta que se
cambien todas: no basta con quitarla del registro.

1. Con M custodios presentes y el almacén desbloqueado, genera partes nuevas con
   el quórum (`bao operator rekey`, que reparte N partes nuevas y deja
   inservibles las anteriores). Entrega las nuevas con las mismas precauciones
   que en el alta.
2. Actualiza el registro en `/herramientas/claves/custodios`.
3. Destruye las partes viejas.

No verificado: el `rekey` con el binario `bao` dentro de la imagen distroless y
su comportamiento con raft no se han probado en este kit. Pruébalo primero en una
caja de prueba antes de depender de él. Si has perdido partes y quedan menos de M, no hay
`rekey` posible (ver el aviso de irrecuperabilidad).

## Token de la app caducado

Si la tarjeta dice que el token no vale (caducó o se revocó), crea otro:

1. Entra en la interfaz web con `admin` y crea un token con el rol `ens-app`
   (`auth/token/create/ens-app`; la política de `admin` lo permite).
2. Pega el valor en `deploy/.env`, como `ENS_KEYSTORE_TOKEN=<token>` (modo 600).
3. `deploy/box.sh restart app`.

El kit no tiene comando para esto (`kit claves token` no existe). No lo escribas
en ningún fichero que se suba a Git.

## Generar un root

Si no tienes la contraseña de `admin`, puedes generar un root token temporal con el
quórum de custodios (`sys/generate-root-token`), cambiar la contraseña y revocarlo
al instante. **No verificado**: la política de `admin` lo permite, pero no se ha
probado en este kit. Es una prueba manual: regístrala con fecha y quién la hizo
antes de contar con ella. Mientras el root exista, el check de «sin root token
vivo» fallará, que es lo correcto.

## Si la inicialización se corta

Si `kit claves init` se interrumpe (cierre de terminal, corte de red, error), el
root token se perdió con el proceso. Es un paso **manual**; el kit nunca lo hace
solo.

1. Mira qué llegó a pasar. Si la pantalla mostró la contraseña de `admin`, la
   inicialización terminó: no vacíes nada; sigue por «Token de la app caducado».
2. Si no la mostró, comprueba con los custodios que nadie ha guardado secretos
   (solo `admin` puede, y aún no existe): el almacén no tiene nada que perder.
3. Para OpenBao: `docker stop $(docker ps -q --filter label=com.docker.compose.service=openbao)`.
4. Vacía el volumen a mano, tras mirar qué hay:

   ```bash
   docker run --rm --user 10001 -v ens-box_openbao-data:/d alpine:3 ls -A /d
   docker run --rm --user 10001 -v ens-box_openbao-data:/d alpine:3 sh -c 'rm -rf /d/* /d/.[!.]*'
   ```

   El volumen `openbao-audit` se conserva. Las partes entregadas en el intento
   fallido ya no valen: destrúyelas.
5. `deploy/box.sh restart` no arranca OpenBao; arráncalo con
   `docker start <contenedor>` (el mismo del paso 3) y repite `deploy/box.sh kit claves init`.

## Copias y restauración

Las copias del almacén (los volúmenes `claves` y `claves-auditoria`) se activan
en el kit de copias (sección «Incluir el almacén de claves» de
[kit-copias](kit-copias.md)). Lo copiado sigue cifrado por OpenBao: sirve solo
con M partes; sin ellas, la copia no abre nada.

Restauración en un VPS nuevo (**prueba manual, no verificada**: no se ha probado
a restaurar una copia en bruto del almacén):

1. VPS nuevo con la caja instalada y el kit de copias activo; restaura el volumen
   `claves` de la copia (procedimiento de la sección 4 de kit-copias, con
   `restore latest:/source/claves`, un volumen `ens-box_openbao-data` vacío y `--user 10001`).
2. Genera el paquete de claves y `deploy/box.sh kit enable claves` en el VPS
   nuevo; el volumen ya tiene datos, así que no se ejecuta `init`.
3. Reúne a M custodios y `deploy/box.sh kit claves unseal`.
4. Comprueba con `admin` que los secretos y claves están. Crea un token de la app
   (sección «Token de la app caducado»).
5. Registra la prueba (fecha, quién, resultado) como prueba de restauración
   (revisión humana, [restauración de evidencias](restauracion-de-evidencias.md)).

## Desactivar

`deploy/box.sh kit disable claves` para y quita el contenedor, y renombra
`MANIFEST` a `MANIFEST.disabled` (así no se reactiva solo). No borra los
volúmenes `openbao-data` y `openbao-audit`, ni el token de `deploy/.env`, ni
`connectors/keystore.yaml`. Caddy responde 503 en `ENS_CLAVES_HOST`. Al reactivar
con `kit enable claves`, OpenBao arranca sellado.

## Si la tarjeta dice…

- **Sellado.** OpenBao corre pero nadie lo ha desbloqueado. Sigue «Desbloqueo». Mientras
  tanto op.exp.10 no tiene evidencia vigente.
- **Con problemas.** La app lee el almacén, pero alguna comprobación falla. Mira cuál
  en el detalle: auditoría no activa, claves sin rotar dentro de la política,
  un root token vivo (revócalo con la persona administradora) o el token de la app a punto de
  caducar (sección «Token de la app caducado»).
- **Caída.** La app no llega a OpenBao. Comprueba por este orden: que el contenedor
  corre (`deploy/box.sh status`, `docker compose -p ens-box logs openbao`), que
  `ENS_KEYSTORE_TOKEN` en `deploy/.env` es válido y que `connectors/keystore.yaml`
  apunta a la dirección correcta. Tras corregir, `deploy/box.sh restart app`.
- **Sin datos.** Aún no hay una comprobación. Espera a la siguiente vuelta del
  scheduler; si el kit está activo y sigue igual, mira los registros de la app.
