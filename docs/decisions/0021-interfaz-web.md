# 0021 — Interfaz web

- Estado: aceptada
- Fecha: 2026-11-08

## Contexto

`web-ui` (#29) añade una interfaz web sobre la base HTTP de `osquery-server`
(Starlette, ADR 0020). La UI lee todas las evidencias y escribe alcance y
categorización, así que es la superficie más sensible del producto. El usuario
decidió (2026-11-08) autenticar con Cloudflare Access: sin contraseñas, TOTP ni
sesiones propias.

## Decisión

### Un servicio `ui` aparte

`ui` es un tercer servicio de la misma imagen (junto a `app` y `agent`), en una
red interna sin salida a Internet y **sin `deploy/.env`**: no recibe los
secretos de los conectores ni los de los agentes. Caddy lo publica por
hostname propio, conserva hacia `ui` las cabeceras `Cf-Access-*` y las quita
hacia `agent`. Lleva healthcheck (`/healthz`).

### Autenticación: solo el JWT de Cloudflare Access

- Cada petición (salvo `/healthz`) debe traer `Cf-Access-Jwt-Assertion`. La app
  lo verifica: RS256 con `cryptography`, `kid` buscado en el JWKS, `iss`
  (`https://<team>.cloudflareaccess.com`), `aud` (ENS_UI_ACCESS_AUD), `exp` y
  `nbf` con una tolerancia de 60 s. Se rechazan `alg` distinto de RS256 (`none`,
  `HS*`). Cualquier fallo: 401 y no se escribe nada.
- La MFA la **exige la política de Access** del hostname (MFA propia o un IdP
  con MFA); la app no puede comprobarla. El runbook explica cómo verificarlo.
- Sin JWKS válido en disco, la UI no deja entrar a nadie (falla cerrado).
- Configuración, solo por entorno: ENS_UI_ORIGIN (https), ENS_UI_ACCESS_AUD,
  ENS_UI_ACCESS_TEAM_DOMAIN, ENS_UI_ACCESS_JWKS_FILE. Ningún valor se imprime.

### Certificados (JWKS) sin salida a Internet

`ui` no puede pedir los certificados de Access. Un trabajo del scheduler en
`app` (que sí tiene salida) descarga
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, valida que sea un
JWKS con al menos una clave RSA y lo escribe de forma atómica en state, de
donde `ui` lo lee (se relee si cambia su mtime). Si falla, se conserva el
fichero anterior y se registra el error. **Este trabajo puede ir en un
followup**: mientras tanto el runbook describe el aprovisionamiento manual del
fichero.

### Sin SQLite en este tramo

Los borradores del asistente se guardan en el YAML del workspace, como en la
CLI, y la auditoría va al log. Esto actualiza el ADR 0005: la UI ya no
necesita SQLite en v0.2; se reabre si un tramo posterior necesita estado que
no sea exportable como fichero.

### Sin cookies ni sesiones: CSRF

La identidad llega en cada petición, así que no hay CSRF de sesión. Aun así, un
POST exige `Origin` igual a ENS_UI_ORIGIN y `Sec-Fetch-Site: same-origin`; si
falta alguno o difiere, 403.

### Cabeceras y plantillas

CSP `default-src 'self'` (sin inline, sin `unsafe-*`), `frame-ancestors 'none'`,
`Referrer-Policy: no-referrer`, `Cache-Control: no-store`,
`X-Content-Type-Options: nosniff`. Jinja2 con autoescape. htmx y fuentes
empaquetados, sin CDN. Las vistas de evidencias muestran manifiestos y
resúmenes, nunca cuerpos en bruto.

### Lista blanca de escritura

La UI solo escribe bajo `scope/`, `controls/`, `risk/`, `org/` y
`assessments/` (constante en `ui/store.py`). Cualquier otra clave
(`connectors/`, `reports/`, ajustes) lanza un error y no escribe. Se rechaza
también el workspace S3 (la UI solo trabaja sobre carpeta local).

### Escribe como la CLI, sin el lock

La UI no toma el `CycleLock`: escribe por las mismas APIs de producto que la
CLI, con escritura atómica (`os.replace`). La copia independiente detecta los
cambios durante la transferencia y aborta; el siguiente ciclo la reintenta.

### Campo `by`

Se prerrellena con el email del JWT y es editable. Motivo: el email prueba
quién estaba autenticado (queda en el log de auditoría), pero quien responde
puede ser otra persona (un asesor que rellena en nombre del responsable); el
registro ENS debe reflejar a quien asume la decisión, no al operador de la
pantalla. La revisión humana sigue siendo obligatoria.

Cada escritura que se guarda emite una línea en el logger
`ens_evidence.ui.audit`: `identity` (email del JWT), `step`, `action`, claves
escritas y `at` (reloj inyectado). Nunca cuerpos de formulario ni valores.

## Consecuencias

- Una superficie web nueva con review de seguridad obligatoria.
- La disponibilidad de la UI depende de Cloudflare Access y de que el JWKS
  esté fresco.
- La equivalencia UI/CLI se prueba comparando ficheros del workspace con el
  mismo reloj.
