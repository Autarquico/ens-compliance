# 0020 — Servidor HTTP de la app y servicio de agentes

- Estado: aceptada
- Fecha: 2026-11-08

## Contexto

La spec `osquery-server` (#28) añade a la app el protocolo remoto de osquery
(enroll, config, log, distributed read y write) detrás de Caddy en `/agent/*`.
Hasta ahora la app no tiene HTTP: el contenedor solo ejecuta el scheduler y
Caddy responde 403 a todo (ADR 0016). El endpoint queda expuesto a Internet,
así que es la superficie de ataque más grande de la caja.

El ADR 0004 y `docs/vision.md` prevén «un contenedor» con UI, API, servidor
osquery y scheduler. Este ADR se desvía de ello (ver «Dos servicios»).

## Decisión

### Framework

- Starlette + uvicorn (h11, sin `[standard]`), como extra opcional `server`
  del paquete. Sin FastAPI. La validación Pydantic de los cuerpos se hace a
  mano en el borde.
- uvicorn sin cabecera `server`, sin access log, con `limit_concurrency` y
  cabeceras h11 acotadas. Los logs no llevan secretos ni cuerpos, y uvicorn no
  vuelca trazas (filtro en `log_config`).

### Dos servicios, una imagen

- `app` (scheduler) no cambia.
- `agent` ejecuta `ens-evidence agent serve` con la misma imagen, en el perfil
  `agents` de Compose, que se activa si existe `deploy/agent.env`.
- `agent` está en la red interna `agent` (sin salida a Internet) y recibe solo
  `deploy/agent.env`, nunca `deploy/.env`. Caddy se une a esa red.
- Motivo: aislamiento. El proceso expuesto a Internet no debe poder leer las
  credenciales de los conectores ni de la copia, ni salir a la red si lo
  comprometen. Un solo proceso obligaría además a compartir señales y ciclo de
  vida con el scheduler.
- Comparten el volumen `data`. La exclusión mutua es el lock del workspace.
- Con workspace en S3, `agent serve` se niega a arrancar: su workspace es local.
- **Desvío registrado** de `docs/vision.md` («Un contenedor») y del ADR 0004
  («una sola unidad»). El coste es un servicio más que operar; se asume.

### Credenciales

- Secretos solo en `deploy/agent.env` (modo 600): `ENS_AGENT_ENROLL_KEY` y
  `ENS_AGENT_NODE_KEY_SECRET`. Cada uno de al menos 32 bytes y distintos entre
  sí; si no, el servicio sale con código 2 sin mostrar el valor.
- **Alta por equipo.** No hay secreto de alta compartido. `agent admit <activo>`
  abre la ventana de alta (24 h por defecto) y emite un token
  `HMAC(clave_alta, uuid ‖ generación ‖ caducidad)` con la caducidad en claro.
  Solo vale para ese equipo y esa generación. Es la «ventana de alta».
- **node_key** sin estado: `HMAC(secreto, uuid ‖ generación)` con prefijo de
  dominio, comparado con `compare_digest`. `host_identifier` es el uuid del
  equipo y debe coincidir con el activo inscrito en `connectors/endpoint.yaml`
  (`host_details` se trata como no fiable).
- **Revocación.** `agent revoke` sube la generación del equipo; el node_key
  anterior deja de valer y el equipo se readmite con `agent admit`.
- El registro (`agents/registry.yaml`) vive en el workspace, así entra en la
  copia. Solo lo escribe la CLI, con el lock. El servidor lo relee en cada
  petición, sin caché.

### Respuestas y escritura

- Cualquier fallo de credencial responde `200 {"node_invalid": true}`, siempre
  igual, sin escribir nada (ni evidencia ni último contacto).
- Los resultados se guardan como evidencia `osquery-agent` con
  `import_collected`, tomando el lock del workspace con try-lock. Con el lock
  ocupado (ciclo o copia) se responde 503 con `Retry-After` y osquery
  reintenta; **no hay spool ni drenador**. Los reintentos se deduplican por sha.
- `log_type=status` se descarta con 200.
- Cota de almacenamiento: un snapshot por consulta del paquete y equipo por
  intervalo de schedule, contado por su hora de generación (6 h menos el 10 %
  de splay de osquery y el desfase de reloj de 5 min), porque osquery entrega
  cada consulta en POST distintos y reentrega tras un corte. Como esa hora la
  pone el equipo, además como mucho 3 entregas atrasadas por consulta y equipo
  cada 6 h de recepción; un snapshot actual siempre cabe. Lo que sobra se descarta con 200 y de un lote mixto se guarda
  lo que cabe. La cota es en memoria. Caddy aplica `read_header` e `idle`
  (sin plazo de cuerpo: sería para todo `:8080` y cortaría subidas lentas de
  la UI y del kit) y completa el cuerpo (`request_buffers`) antes de abrir el
  upstream.
- Solo se aceptan snapshots de consultas del paquete, filtrados por plataforma
  y por la lista blanca de columnas.
- Config servida desde el paquete del repo, SQL byte a byte, en modo snapshot
  cada 6 h (21600 s) y `logger_min_status=3`.
- Consultas en vivo: solo consultas del paquete por id, sin SQL arbitrario.
  `distributed/write` solo acepta ejecuciones entregadas a ese equipo.
- Estado operativo (último contacto, cola) en `$ENS_STATE_DIR/agent/`, fuera
  del workspace y sin lock.

## Consecuencias

- Starlette no trae CSRF ni MFA. Si el mismo servidor sirve la UI, `web-ui`
  tiene que resolverlo. Decisión del usuario: `web-ui` autentica mediante
  Cloudflare Access. El hostname de agentes no puede usar Access (osquery no
  se autentica con él); su defensa es el token de alta y el node_key.
- Restaurar una copia antigua baja las generaciones del registro: node_keys
  revocados volverían a valer. Tras restaurar, revocar o readmitir todos los
  equipos (runbook de agentes).
- Una revocación se aplica en la siguiente petición (el registro se relee
  siempre); no hay retraso por caché.
- El volumen de manifiestos crece con cada lote aceptado (un snapshot por consulta y equipo
  por intervalo como máximo, por la dedupe y el intervalo de 6 h).
- El tráfico Caddy → `agent` es HTTP plano dentro de una red Docker interna.
- Rotar `ENS_AGENT_NODE_KEY_SECRET` invalida todos los node_key: hay que
  readmitir todos los equipos.
- El rollback a una versión sin servicio `agent` deja los POST sin respuesta
  útil (403); osquery guarda en búfer y reintenta (runbook de la caja, 4.1).

## Alternativas descartadas

- **FastAPI:** añade Pydantic-en-rutas y OpenAPI que no se usan; Starlette
  basta para cinco POST.
- **Un solo proceso:** comparte credenciales de conectores con el proceso
  expuesto y mezcla señales con el scheduler.
- **Secreto de alta compartido:** una filtración sirve para inscribir
  cualquier equipo; el token por equipo y con caducidad acota el daño.
- **Spool con drenador:** añade estado, tope y un hilo; con try-lock y 503,
  osquery ya reintenta y la copia no choca con escrituras.
