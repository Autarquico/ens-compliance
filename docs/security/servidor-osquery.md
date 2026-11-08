# Modelo de amenaza — servidor osquery

Alcance: servicio `agent` (ADR 0020), expuesto a Internet detrás de Caddy y,
en su caso, Cloudflare Tunnel. Procedimientos: [runbook de agentes](../runbooks/agentes-osquery.md).

## Activos

- Evidencias y manifiestos del workspace (el servidor puede escribir en ellos).
- `agents/registry.yaml` (generaciones y revocaciones).
- `ENS_AGENT_ENROLL_KEY` y `ENS_AGENT_NODE_KEY_SECRET` (`deploy/agent.env`).
- Credenciales de los conectores y de la copia (`deploy/.env`): **no** deben
  llegar al servicio `agent`.
- Datos de los equipos: inventario, software y configuración de seguridad
  (privacidad).

## Posiciones del atacante

1. Internet sin credenciales.
2. Equipo inscrito comprometido (con su node_key).
3. Token de alta filtrado (con ventana vigente).
4. Ejecución de código en el proceso `agent`.

## Amenazas y controles

| Amenaza | Control | Queda sin cubrir |
|---------|---------|------------------|
| Alta de un equipo no inscrito | Token por equipo, con caducidad, emitido por `agent admit`; uuid y hostname deben estar en `connectors/endpoint.yaml` | Quien tenga el token vigente de un equipo puede darlo de alta hasta que caduque |
| Suplantar a un equipo | node_key HMAC con generación; `hostIdentifier` debe coincidir con el uuid del node_key | Equipo comprometido con su propio node_key |
| Oráculo sobre qué falló | Siempre `200 {"node_invalid": true}`, mismo cuerpo, sin escrituras | Diferencias de latencia no medidas: en `enroll`, un uuid no inscrito o revocado sale antes de calcular el HMAC; el uuid no es enumerable, así que se acepta |
| Falsear resultados | Solo snapshots de consultas del paquete, plataforma y columnas en lista blanca, límites de `parse_rows`, `unixTime` acotado | Un equipo comprometido miente sobre sus propios datos (las evidencias del endpoint son declarativas) |
| Inyección de consultas | Config y consultas en vivo solo desde el paquete, por id | — |
| DoS por cuerpos | Caddy: 64 KB / 10 MB, `read_header 10s`, `read_body 60s`, `idle 1m` y `request_buffers 10MB` (el cuerpo se completa en Caddy antes de ocupar una plaza de uvicorn: un cliente lento no la retiene); 415 con `Content-Encoding`; uvicorn con límite de concurrencia | DoS volumétrico: WAF de Cloudflare (recomendado, no verificado) |
| Equipo admitido que llena el disco con lotes distintos en `/agent/log` | Un lote de resultados programados por equipo por intervalo de schedule (6 h menos 5 min de desfase); el resto responde 200 y se descarta (`outcome=throttled`). Cota en memoria: reiniciar el servicio la reabre una vez por equipo | Las consultas en vivo no se acotan por tiempo, pero solo se aceptan las entregadas y una vez |
| DoS por lock | Try-lock y 503; un ciclo largo no bloquea peticiones | Ciclos o copias largas retrasan resultados; osquery reintenta |
| Compromiso del proceso `agent` | Red interna sin salida, sin `deploy/.env`, rootfs de solo lectura, sin capacidades | Puede leer y escribir el workspace montado |
| Secretos en logs | Logs sin cuerpos, cabeceras ni claves; `asset` y hash corto del host solo en peticiones autenticadas (nunca el uuid); uvicorn no vuelca trazas; Caddy filtra cabeceras y query string | — |
| Cabeceras de Access o cookies reenviadas al servidor | Caddy elimina `Cookie` y `Cf-Access-*` hacia `agent` | Sintaxis validada con `caddy validate` y `caddy adapt` (2.11.7); el borrado por prefijo `Cf-Access-*` no se ha probado con una petición real |
| Revocación tardía | Registro releído en cada petición | — |
| Restaurar una copia antigua | Runbook: revocar o readmitir tras restaurar | Depende del operador |
| HTTP plano Caddy → `agent` | Red Docker interna sin otros servicios | Un contenedor comprometido en esa red lo ve |
| Rotura de secretos al rotar | Procedimiento en el runbook | Rotar el secreto de node_key obliga a readmitir todo |

## Riesgos residuales

- El token de alta viaja al equipo como fichero; protegerlo (modo 600) y usar
  ventanas cortas.
- Privacidad: se recogen datos de portátiles personales o de la empresa;
  informar a las personas usuarias (documentación organizativa).
- Volumen de manifiestos y almacenamiento crece con la flota (un lote por equipo y 6 h como máximo).
- Verificación real pendiente: Cloudflare no se ha probado en esta entrega y los
  timeouts de Caddy no se han medido con clientes lentos reales.
- Starlette no ofrece CSRF ni MFA: lo resuelve `web-ui` (Cloudflare Access).

## Controles organizativos (verificación manual)

- [ ] `deploy/agent.env` con modo 600, fuera de Git y copia de los valores fuera de la caja.
- [ ] El hostname de agentes no comparte política de Access con la UI y tiene rate-limit en el WAF.
- [ ] Tras restaurar una copia, revocados o readmitidos todos los equipos.
- [ ] Revisión de `agent hosts` periódica: equipos sin contacto o no esperados.
