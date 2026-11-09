# Kit de copias: Backrest y restic en el VPS de la caja

Cómo activar, operar y recuperar el perfil `copias` (spec
[kit-backups](../specs/kit-backups.md), [ADR 0019](../decisions/0019-caja-y-herramientas-en-un-vps.md),
[ADR 0016](../decisions/0016-caja-compose.md)). Se apoya en el
[runbook de la caja](caja.md); la copia propia de la caja (restic hacia R2,
sección 6 de `caja.md`) es distinta y sigue funcionando igual.

Instalar Backrest no cumple mp.info.6: la evidencia sale de las copias que se
hacen, de su integridad y de una prueba de restauración revisada por una
persona (sección 3).

## 1. Qué se despliega

- Servicio `backrest` (perfil `copias`): Backrest v1.14.1, imagen
  `ghcr.io/garethgeorge/backrest:v1.14.1-scratch` fijada por digest en
  `compose.yaml`. Lleva el restic de la imagen (`BACKREST_RESTIC_COMMAND=/bin/restic`).
- Corre con `user: 10001:10001`, `read_only`, `cap_drop: [ALL]`,
  `no-new-privileges`, sin puertos publicados. La imagen es `scratch`: no tiene
  shell ni curl, así que no admite healthcheck; `box.sh` solo comprueba que el
  contenedor corre.
- UID 10001, no otro: el volumen `data` de la caja es `0700` de 10001 y con
  otro UID no se puede leer, ni con `group_add` (comprobado con `ls` desde
  `10002` en `tests/box/test_integration.py`). No es una escalada: sin
  capacidades, y `data` entra en solo lectura. Los volúmenes de Backrest
  (`backrest-config` y `backrest-data`) son suyos y los prepara `box.sh`.
- Volúmenes: `backrest-config` guarda `config.json` con la contraseña del
  repositorio y las claves S3 de escritura, y ningún otro servicio lo monta.
  `backrest-data` guarda su base de operaciones y logs. `data` de la caja entra en
  `/source/caja:ro`; las rutas del host que se elijan entran en `/source/<nombre>:ro`
  por `deploy/kit/copias/compose.yaml` (generado, fuera de git).
- Redes: `copias-net` (interna; solo la alcanza Caddy) y `copias` (con salida).
  Compose no filtra la salida por destino: `backrest` puede salir a cualquier
  sitio. Si quieres limitarla al destino S3, hazlo en el cortafuegos del VPS
  (no verificado aquí).
- Caddy publica Backrest en `ENS_COPIAS_HOST`. Backrest autentica con
  `Authorization` (Basic o el Bearer que guarda el navegador), no con cookies
  (`internal/auth/middleware.go` en v1.14.1): Caddy deja pasar `Authorization` y
  quita `Cookie` y `Cf-Access-*`.
- Caddy no valida el JWT de Access en este host. Si la aplicación de Access
  falta o su política está mal, lo único que protege es el usuario de Backrest
  (`admin`, que crea `box.sh` al activar; sección 2, paso 5).

## 2. Alta

Necesitas la caja instalada (`deploy/box.sh install`) con una release que incluya
el perfil `copias`, y el paquete que genera el asistente
(`/herramientas/copias`): un `.tar.gz` con `config.json`, `compose.yaml`,
`app.env`, `backups.yaml` y `MANIFEST`. El paquete contiene las claves de
escritura: trátalo como un secreto.

1. **Destino.** Un bucket S3 con bloqueo de objetos, de un proveedor distinto del
   de la copia propia de la caja si puedes (ADR 0008). Dos credenciales: la de
   escritura (solo Backrest) y una de **solo lectura** (listar y leer; sin
   `PutObject` ni `DeleteObject`) para la app.
2. **Hostname y Access.** En Zero Trust, un hostname del túnel hacia
   `http://caddy:8080` (como en la sección 4 de `caja.md`) y una aplicación de
   Access con MFA para él. Añade `ENS_COPIAS_HOST=copias.example.org` a
   `deploy/ui.env` (modo 600; el fichero también activa el perfil `ui`,
   ver [interfaz web](interfaz-web.md)).
3. **Activar:**

   ```bash
   deploy/box.sh kit enable copias /ruta/al/paquete.tar.gz
   ```

   `box.sh` rechaza el paquete si trae algo que no sea de los cinco ficheros, o si
   un sha256 del `MANIFEST` no coincide, antes de tocar nada. Luego:
   `config.json` entra por stdin en el volumen `backrest-config` (modo 600, dueño
   10001, ya con el usuario `admin`); `app.env` se funde en `deploy/.env`; `backups.yaml` se funde en
   `connectors/backups.yaml` del workspace; `compose.yaml` se instala (tras
   validar que solo toca los volúmenes de `backrest`, de host, en `:ro` bajo
   `/source/`) en `deploy/kit/copias/`. Recrea `backrest`, `caddy` y `app`. Nada
   de eso imprime valores, salvo la contraseña de `admin` (ver el paso 5).
4. **Borra el paquete.** `rm` o `shred`; el de `/herramientas` no se guarda en la app.
5. **Contraseña de Backrest: anótala ahora.** El paquete no trae usuario: antes de
   escribir `config.json`, `box.sh` genera una contraseña aleatoria, calcula su
   bcrypt con la imagen de Caddy de la release (sin red) e inyecta el usuario
   `admin` con la autenticación activada. Backrest nunca arranca publicado sin
   usuario. Al terminar, `box.sh` muestra la contraseña **una sola vez** en la
   terminal; no la escribe en ningún fichero ni en los registros. Guárdala en el
   gestor de contraseñas. Si la pierdes, no hace falta el
   paquete: `deploy/box.sh kit copias reset-password` reescribe solo el usuario
   `admin` del `config.json` que ya hay en el volumen, reinicia `backrest` y
   muestra la contraseña nueva una sola vez (la anterior deja de valer). Falla con
   un error claro si las copias no están activadas.
6. **Comprobar.** `deploy/box.sh status`; en Backrest, lanza el plan una vez.
   En la siguiente vuelta del scheduler la app lee el repositorio con las
   credenciales de lectura y `--no-lock`.

Repetir `kit enable` sobre una caja con Backrest ya configurado exige
`--replace-config`: sustituye `config.json` (la anterior queda como
`config.json.prev` dentro del volumen) y **sustituye el usuario de Backrest**: `box.sh`
genera otra contraseña y la muestra de nuevo (paso 5).

`config.json.prev` (y `config.json.pre-update`, que Backrest deja al migrar la
configuración) llevan claves de escritura. `box.sh` los borra en cuanto
comprueba que Backrest arranca sano con la configuración nueva. Si no arranca,
restaura la anterior desde `config.json.prev` y lo conserva, como conserva
`config.json.pre-update`, para el rollback: bórralos a mano cuando ya no hagan falta.

## 3. Primera prueba de restauración

mp.info.6 no se evidencia sin una prueba hecha por una persona.

1. En Backrest, elige una copia, Restore, y como destino una carpeta dentro de
   `/data` (el único sitio escribible del contenedor), p. ej. `/data/prueba`.
2. Comprueba el resultado desde el host y compáralo con el original:

   ```bash
   docker run --rm -v ens-box_backrest-data:/d:ro alpine:3 sh -c 'cd /d/prueba && find . -type f | head && sha256sum <un-fichero>'
   docker run --rm --user 10001 -v ens-box_data:/d:ro alpine:3 sha256sum /d/<mismo-fichero>
   ```

3. Borra la prueba: `docker run --rm --user 10001 -v ens-box_backrest-data:/d alpine:3 rm -rf /d/prueba`.
4. Regístrala como prueba de restauración (revisión humana) según el
   [runbook de restauración](restauracion-de-evidencias.md). Repítela con la
   periodicidad de la política de copias.

## 4. Restaurar el VPS entero en una máquina nueva

Lo que sale del VPS: las copias del volumen `data` de la caja. `backrest-config`
no se copia a propósito (lleva secretos) y `backrest-data` es caché e historial
de Backrest. Necesitas, guardado fuera del VPS: la URL del repositorio, su
contraseña y unas credenciales de lectura.

1. VPS nuevo con Docker; clona el repositorio y `deploy/box.sh install vX.Y.Z`
   (la misma versión que corría, o la última).
2. Para la app: `docker compose -p ens-box stop app`.
3. Restaura sobre el volumen con el restic de la imagen de Backrest (el
   fichero `restore.env`, modo 600, trae `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID` y
   `AWS_SECRET_ACCESS_KEY`; bórralo al acabar):

   ```bash
   docker run --rm --user 10001:10001 --read-only --cap-drop ALL \
     --tmpfs /cache:uid=10001 -e XDG_CACHE_HOME=/cache --env-file restore.env \
     -v ens-box_data:/restore --entrypoint /bin/restic \
     ghcr.io/garethgeorge/backrest:v1.14.1-scratch@sha256:<el digest de compose.yaml> \
     -r s3:https://<endpoint>/<bucket>/<ruta> restore latest:/source/caja --target /restore
   ```

4. `deploy/box.sh restart app` y `deploy/box.sh status`.
5. Genera un paquete nuevo en el asistente, con claves nuevas, y actívalo
   (sección 2). El anterior queda revocado en el proveedor.
6. Registra la prueba como prueba de restauración (revisión humana). No está
   ejecutada con un VPS real: es el procedimiento previsto.

## 5. Actualizar la imagen de Backrest

La imagen va en `compose.yaml`, que sale de la release: se cambia con una
release nueva, no editando el clon.

1. Obtén el digest: `docker buildx imagetools inspect ghcr.io/garethgeorge/backrest:vX.Y.Z-scratch`
   (el `Digest:` de la cabecera, el del índice multiarquitectura).
2. Cambia la imagen en `compose.yaml` (tag y digest), lee las notas de versión
   de Backrest y pasa `pytest tests/box`.
3. Antes de actualizar, guarda la configuración: Backrest migra `config.json`
   hacia delante y una versión vieja puede no leerla.

   ```bash
   docker run --rm --user 10001 -v ens-box_backrest-config:/c alpine:3 cp -p /c/config.json /c/config.json.pre-update
   ```

4. Publica la release y `deploy/box.sh update vX.Y.Z`. `box.sh` comprueba que
   `backrest` corre.
5. Si no arranca: `deploy/box.sh rollback`; si la versión anterior no lee la
   configuración migrada, vuelve a poner `config.json.pre-update` como `config.json`.

## 6. Rotar claves

- **Credenciales S3 de lectura (app).** Crea las nuevas en el proveedor,
  sustituye `ENS_BACKUPS_<ID>_AWS_*` en `deploy/.env`, `deploy/box.sh restart app`
  y revoca las anteriores.
- **Credenciales S3 de escritura (Backrest).** Crea las nuevas, cámbialas en
  Backrest (Repositorio, editar, variables de entorno), lanza una copia y revoca
  las anteriores. Así no se pierde el usuario de Backrest.
- **Contraseña del repositorio.** Con las claves de escritura, `restic key add`
  y `restic key remove` con el restic de la imagen (mismo `docker run` que en
  4.3, con `restic key list`); después actualiza la contraseña en Backrest y
  `ENS_BACKUPS_<ID>_RESTIC_PASSWORD` en `deploy/.env`. Esta operación no se ha
  ejecutado: pruébala primero en un repositorio de prueba.
- **Todo a la vez** (paquete nuevo): `kit enable copias --replace-config`; `box.sh`
  genera una contraseña nueva para `admin` y la muestra una vez.
- Tras rotar, borra `config.json.prev` y `config.json.pre-update` del volumen si quedaron (`kit enable` ya los borra cuando Backrest arranca sano).

## 7. Desactivar

`deploy/box.sh kit disable copias` para y quita el contenedor, y borra el
override. No borra los volúmenes, las variables de `deploy/.env`,
`connectors/backups.yaml` ni las copias. Caddy responde 503 en
`ENS_COPIAS_HOST` hasta que se vuelva a activar o se quite el hostname del túnel.

## 8. Qué impide borrar copias, y la prueba en R2

Comprobado con un MinIO con bloqueo de objetos (`pytest -m docker
tests/box/test_copias_objectlock.py`):

- Con la credencial de lectura, `restic snapshots --no-lock` funciona; sin
  `--no-lock`, `snapshots` y `forget` fallan (restic escribe un fichero de
  bloqueo); `forget <id>` y `backup` fallan con `Access Denied`, y la copia
  sigue.
- Con la clave de **escritura**, `restic forget` sí «borra» en un bucket
  versionado: deja una marca de borrado y el objeto sigue retenido. Lo que impide
  eliminar esa versión es el bloqueo (la prueba lo intenta y falla). La
  protección frente a la app es, por tanto, la credencial de solo lectura; la
  del bloqueo es contra quien tenga la de escritura. El bucket crece mientras
  duren las retenciones.

Prueba manual en R2 (no ejecutada; restic sobre el bloqueo de R2 está sin
verificar, `docs/vision.md`):

1. Crea el bucket con una regla de retención y dos tokens de API de R2: Object
   Read & Write y Object Read only, ambos limitados al bucket.
2. Con el de escritura: `restic init` y `restic backup <ruta>`.
3. Con el de solo lectura: `restic snapshots --no-lock` debe listar;
   `restic forget <id>` y `restic backup` deben fallar.
4. Con el de escritura: `restic forget --prune` y comprueba qué objetos quedan
   dentro de la retención (`rclone ls --s3-versions` o el panel).
5. Anota el resultado y la fecha como revisión humana.

## 9. Si la tarjeta dice…

La tarjeta de copias de la app resume lo que lee del repositorio. Primeros pasos
según lo que muestre:

- **Caída.** La app no ha podido leer el repositorio. Las copias pueden estar
  haciéndose igualmente: Backrest es independiente de la app. Comprueba, por este
  orden:
  1. Credenciales de lectura: `ENS_BACKUPS_<ID>_*` en `deploy/.env` (caducadas,
     revocadas o con un valor mal copiado; sección 6).
  2. Destino: que el bucket y el endpoint sigan siendo los del paquete y que el
     proveedor no esté caído.
  3. Red: que el VPS salga a internet hacia el destino
     (`docker compose -p ens-box logs app`).
  Tras corregir, `deploy/box.sh restart app` y espera a la siguiente vuelta del
  scheduler.
- **Con problemas.** La app sí lee el repositorio, pero alguna comprobación ha
  fallado. Mira cuál en el detalle de la tarjeta:
  1. Copia antigua: abre Backrest y revisa el último intento del plan (error de
     origen, credenciales de escritura, destino sin espacio o sin acceso); lanza
     el plan una vez.
  2. Integridad: `restic check` ha fallado; no borres nada, y conserva el
     registro para revisarlo con la persona responsable.
  3. Prueba de restauración vencida: repite la sección 3 y regístrala como
     prueba de restauración (revisión humana).
