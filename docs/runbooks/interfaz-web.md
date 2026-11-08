# La interfaz web: desplegar, comprobar el acceso, volver atrás y recuperar

La interfaz (ADR 0021) corre en el servicio `ui` de la caja, con la misma imagen
que `app`. No tiene contraseñas ni sesiones propias: la identidad es el JWT que
firma Cloudflare Access (`Cf-Access-Jwt-Assertion`). **El MFA lo impone la
política de Access, no la aplicación**: si la política no lo exige, nadie lo
exige. Esta guía parte de una caja instalada (`docs/runbooks/caja.md`).

Los nombres de este documento son ficticios: `ui.example.org`,
`agents.example.org`, `example.cloudflareaccess.com`.

## 1. Qué hay en la caja

| Pieza | Papel |
|---|---|
| `ui` (perfil `ui`) | Sirve la interfaz en `ui:8000`. Red `ui-net` (interna, sin salida a Internet). Lee solo `deploy/ui.env`. |
| `caddy` | Enruta por nombre de host: `ENS_UI_HOST` a `ui`, `ENS_AGENT_HOST` a los agentes. Host desconocido o vacío, y cualquier otra ruta: 403. Conserva `Cf-Access-Jwt-Assertion` hacia `ui` y lo quita hacia `agent`. |
| Cloudflare Access | Autentica y exige MFA delante del hostname de la interfaz. |

`ui` se niega a arrancar si el workspace es S3 (la interfaz solo trabaja sobre
carpeta local). Escribe solo en `scope/`, `controls/`, `risk/`, `org/` y
`assessments/`, nunca en `connectors/` ni en los ajustes.

## 2. Desplegar

1. **Aplicación de Access.** En Cloudflare Zero Trust, Access, Applications,
   crea una aplicación autoalojada para `ui.example.org`, con una política que
   permita solo a las personas autorizadas **y exija MFA** (ver sección 3).
   Apunta dos datos de la aplicación: el **Application Audience (AUD) tag**
   y el dominio del team (`example.cloudflareaccess.com`).
2. **Hostname del túnel.** En el túnel de la caja (sección 4 de `caja.md`),
   añade el hostname público `ui.example.org` hacia `http://caddy:8080`. Es
   distinto del hostname de los agentes.
3. **`deploy/ui.env`** (modo 600) desde `deploy/ui.env.example`:

   ```bash
   cp deploy/ui.env.example deploy/ui.env && chmod 600 deploy/ui.env
   ```

   | Variable | Valor |
   |---|---|
   | `ENS_UI_HOST` | `ui.example.org` (sin esquema ni puerto; `box.sh` lo valida y se lo pasa a Caddy) |
   | `ENS_UI_ORIGIN` | `https://ui.example.org` |
   | `ENS_UI_ACCESS_AUD` | el AUD tag de la aplicación |
   | `ENS_UI_ACCESS_TEAM_DOMAIN` | `example.cloudflareaccess.com` |
   | `ENS_UI_ACCESS_JWKS_FILE` | `/data/state/ui/access-jwks.json` |

   El servicio no lee `deploy/.env`: no recibe las credenciales de los conectores.
4. **Hostname de los agentes** (solo si usas el perfil `agents`): añade
   `ENS_AGENT_HOST=agents.example.org` a `deploy/agent.env`. Sin él, Caddy
   deja de enrutar los agentes (host `agent.invalid`).
5. **Certificados de Access** (sección 4). Sin ellos la interfaz arranca pero
   responde 401 a todo.
6. **Arrancar.** Con `deploy/ui.env` presente `box.sh` activa el perfil `ui`.
   Caddy tiene que releer los nombres de host:

   ```bash
   deploy/box.sh restart ui
   deploy/box.sh restart caddy
   deploy/box.sh status          # ui y caddy: healthy
   ```

   Una `update` posterior también arranca `ui` y exige que esté sano.

## 3. Comprobar que Access exige MFA

Hazlo antes de dar la dirección a nadie, y de nuevo tras cualquier cambio de
política o de proveedor de identidad (IdP).

1. **En el panel de Cloudflare.** Zero Trust, Access, Applications,
   `ui.example.org`, pestaña Policies. Debe haber una política `Allow` con
   una de estas dos formas:
   - una regla `Require` de **Authentication method** (MFA), si el IdP envía el
     método de autenticación (`amr`), o
   - un IdP que ya impone MFA a todos sus usuarios (compruébalo en el propio
     IdP: política de acceso condicional o equivalente, sin excepciones para
     las cuentas autorizadas).

   Una política `Allow` por correo o dominio sin una de las dos cosas **no**
   exige MFA. Ninguna política `Bypass` ni `Service Auth` sobre este hostname.
2. **Sin cookie ni sesión.** Desde una máquina fuera de la red de la caja:

   ```bash
   curl -sS -o /dev/null -D - https://ui.example.org/ | grep -iE '^(HTTP|location)'
   ```

   Se espera una redirección (302) a
   `https://example.cloudflareaccess.com/cdn-cgi/access/login/...`, no contenido
   de la interfaz. Con 200 y contenido, quita el hostname del túnel y avisa al
   responsable del sistema.
3. **Con sesión.** Entra desde un navegador, inicia sesión y comprueba que el
   IdP pide el segundo factor. Si no lo pide, la política no lo exige: corrígela
   antes de seguir.
4. **Falsificación.** Una petición con una cabecera `Cf-Access-Jwt-Assertion`
   inventada que llegue a Caddy sin pasar por Access (solo posible desde la red
   `edge`) recibe 401 de la aplicación: el JWT se verifica con la firma,
   `iss`, `aud` y `exp`.

Esta guía no se ha ejecutado contra una cuenta real de Cloudflare: la
comprobación completa queda para `validation-selfassessment`.

## 4. Certificados de Access (JWKS)

`ui` no tiene salida a Internet y no puede descargar las claves con las que
Access firma los JWT. Hasta que exista la tarea del scheduler que las refresca
(ADR 0021; pendiente), se aprovisionan a mano desde un equipo con Internet y
se escriben en el volumen de datos.

```bash
curl -fsS https://example.cloudflareaccess.com/cdn-cgi/access/certs > access-jwks.json
python3 -c "import json; k = json.load(open('access-jwks.json'))['keys']; assert any(x['kty'] == 'RSA' for x in k), 'sin claves RSA'; print(len(k), 'claves')"
docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 -v ens-box_data:/data \
  python:3.12-slim sh -c 'umask 077; mkdir -p /data/state/ui && cat > /data/state/ui/access-jwks.json.tmp \
  && mv /data/state/ui/access-jwks.json.tmp /data/state/ui/access-jwks.json' < access-jwks.json
rm access-jwks.json
```

`ui` relee el fichero en cada petición: no hace falta reiniciar. Sin un
fichero válido nadie entra (401): la interfaz falla cerrada.

Access rota sus claves de forma periódica (las publica con antelación en el
mismo endpoint). Repite el procedimiento al menos una vez al mes y siempre que
`ui` responda 401 a usuarios que sí pasan por Access. Anota la fecha en el
registro de operaciones de la caja.

## 5. Volver atrás

- **Desactivar la interfaz sin tocar nada más.** Mueve `deploy/ui.env` fuera
  de `deploy/` y ejecuta `docker compose -p ens-box stop ui`. Quita el hostname
  del túnel si la desactivación va a durar. Los datos del volumen no se tocan.
- **Volver a una versión anterior.** `deploy/box.sh rollback`. Si esa versión
  no define el servicio `ui`, `box.sh` para el contenedor huérfano sin borrarlo
  ni tocar volúmenes. El Caddyfile vuelve al de esa versión.
- **Revocar el acceso de una persona.** En Access, quita a la persona de la
  política y revoca su sesión (Users, Revoke). La aplicación no guarda sesiones.

## 6. Recuperar

- **La interfaz responde 401 a todos.** Es lo esperado sin JWKS válido, con
  `ENS_UI_ACCESS_AUD` o el dominio del team equivocados, o con el reloj del
  host desviado más de 60 s. Revisa el fichero de la sección 4 y
  `docker compose -p ens-box logs ui` (no imprime valores de configuración).
- **403 de Caddy en el hostname de la interfaz.** `ENS_UI_HOST` de
  `deploy/ui.env` no coincide con el hostname publicado, o Caddy no ha releído
  el valor: `deploy/box.sh restart caddy`.
- **`ui` no arranca.** Falta alguna variable de `deploy/ui.env` o el workspace
  es S3. El error sale con código 2 y no imprime valores.
- **Se perdió el volumen o el workspace.** La interfaz no guarda nada propio:
  restaura el workspace desde la copia (`docs/runbooks/restauracion-de-evidencias.md`),
  vuelve a aprovisionar el JWKS (sección 4) y `deploy/box.sh restart ui`.
- **Escrituras de la interfaz sin copia.** Se hacen sin tomar el lock del ciclo
  y de forma atómica; la siguiente copia detecta el cambio y la reintenta.

Nada de esto se ha verificado con Docker ni con Cloudflare reales, salvo el
enrutado por host de Caddy y la validación del Caddyfile.
