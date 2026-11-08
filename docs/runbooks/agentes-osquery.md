# Agentes osquery: alta, operación y recuperación

Servidor de agentes (ADR 0020, [modelo de amenaza](../security/servidor-osquery.md)).
El despliegue del servicio y el enrutado de Caddy están en el
[runbook de la caja](caja.md#41-servidor-de-agentes-perfil-agents).

Los comandos `ens-evidence agent ...` se ejecutan dentro de la caja, con el
workspace montado:

```bash
docker compose -p ens-box exec app ens-evidence agent hosts
```

> Nombres de subcomandos según el plan (`serve`, `hosts`, `admit`, `revoke`,
> `query run|show|list`). `src/ens_evidence/agent/cli.py` no existía al escribir
> este runbook: confirma los flags con `ens-evidence agent --help`.

## 1. Preparar el servidor

1. Genera los secretos (distintos, de al menos 32 bytes):

   ```bash
   cp deploy/agent.env.example deploy/agent.env
   chmod 600 deploy/agent.env
   openssl rand -base64 32   # pega en ENS_AGENT_ENROLL_KEY
   openssl rand -base64 32   # pega en ENS_AGENT_NODE_KEY_SECRET
   ```

   Guarda una copia fuera de la caja (gestor de contraseñas).
2. Con `deploy/agent.env` presente, `box.sh` activa el perfil `agents`:

   ```bash
   deploy/box.sh restart agent
   deploy/box.sh restart caddy
   ```
3. Publica el hostname de agentes (p. ej. `agentes.example.org`) en el túnel,
   sin Access (caja.md, 4.1).
4. Comprueba desde dentro de la red `edge` (no sale a Internet):

   ```bash
   docker compose -p ens-box exec caddy wget -q -O - \
     --header 'Content-Type: application/json' --post-data '{}' \
     http://127.0.0.1:8080/agent/enroll
   # esperado: {"node_invalid": true}
   docker compose -p ens-box exec caddy wget -S -O /dev/null http://127.0.0.1:8080/agent/enroll
   # esperado: error 403 (solo se admite POST)
   ```

Si el servicio sale con código 2, un secreto falta, es corto o ambos son
iguales. El mensaje nombra la variable, nunca el valor.

## 2. Dar de alta un equipo

El equipo debe estar inscrito en `connectors/endpoint.yaml` (uuid y hostname).

1. Emite el token de alta (caduca en 24 h por defecto):

   ```bash
   docker compose -p ens-box exec app ens-evidence agent admit portatil-01 --hours 24
   ```

   Imprime el token una sola vez. Entrégalo al equipo por un canal seguro.
2. En el equipo, guarda el token en un fichero legible solo por root
   (`/etc/osquery/enroll.token`, modo 600) y configura osquery
   (`osquery.flags`):

   ```
   --tls_hostname=agentes.example.org
   --host_identifier=uuid
   --enroll_secret_path=/etc/osquery/enroll.token
   --enroll_tls_endpoint=/agent/enroll
   --config_plugin=tls
   --config_tls_endpoint=/agent/config
   --logger_plugin=tls
   --logger_tls_endpoint=/agent/log
   --disable_distributed=false
   --distributed_plugin=tls
   --distributed_tls_read_endpoint=/agent/distributed/read
   --distributed_tls_write_endpoint=/agent/distributed/write
   ```

   **Prohibidos:** `--logger_tls_compress` (Caddy responde 415) y
   `--tls_secret_always` (el servidor no lo contempla).
3. Reinicia `osqueryd` y comprueba:

   ```bash
   docker compose -p ens-box exec app ens-evidence agent hosts
   ```

   El equipo aparece con su último contacto. El primer lote de resultados
   llega al cabo del intervalo (6 h) o al reiniciar osquery.

Un token caducado o de otra generación da `node_invalid`: vuelve a ejecutar
`agent admit`. Tras el alta, el token ya no hace falta.

## 3. Consultas en vivo

Solo consultas del paquete, por id. Nunca SQL arbitrario.

```bash
docker compose -p ens-box exec app ens-evidence agent query list
docker compose -p ens-box exec app ens-evidence agent query run <id-consulta> --host portatil-01
docker compose -p ens-box exec app ens-evidence agent query show <run_id>
```

El equipo la recoge en su siguiente sondeo distribuido y el resultado queda
como evidencia, visible con `query show`.

## 4. Revocar un equipo

Equipo perdido, comprometido o dado de baja:

```bash
docker compose -p ens-box exec app ens-evidence agent revoke portatil-01 --reason "equipo perdido"
```

Sube la generación: el node_key anterior deja de valer en la siguiente
petición (el registro no se cachea). Para volver a admitirlo, ejecuta
`agent admit`.

## 5. Rotar secretos

| Secreto | Efecto | Procedimiento |
|---|---|---|
| `ENS_AGENT_ENROLL_KEY` | Invalida los tokens de alta pendientes; los equipos ya dados de alta siguen | Cambia el valor, `deploy/box.sh restart agent`, reemite con `agent admit` los pendientes |
| `ENS_AGENT_NODE_KEY_SECRET` | Invalida **todos** los node_key | Cambia el valor, `restart agent`, y readmite todos los equipos (`agent admit` por cada uno) |

Genera cada valor con `openssl rand -base64 32`; siguen siendo distintos entre sí.

## 6. Volver atrás

`deploy/box.sh rollback` a una versión sin servicio `agent` detiene el
contenedor sin borrarlo (caja.md, 4.1). Los POST a `/agent/*` pasan a 403: osquery
interpreta el fallo, guarda en búfer y reintenta; no se pierden los resultados
pendientes del equipo hasta el límite de su búfer. Al volver a la versión con
`agent`, los equipos retoman solos. Para desactivar el servidor sin volver
atrás: mueve `deploy/agent.env` y `docker compose -p ens-box stop agent`.

## 7. Recuperación

- **Se pierde `deploy/agent.env`:** genera secretos nuevos (sección 1), reinicia
  `agent` y readmite todos los equipos. Los node_key antiguos no valen.
- **Restaurar la copia del workspace:** el registro `agents/registry.yaml`
  vuelve a un estado anterior y las generaciones **bajan**: un equipo
  revocado después de la copia volvería a ser válido. Tras restaurar,
  revoca o readmite todos los equipos antes de reabrir el hostname de
  agentes (`agent revoke` y después `agent admit`).
- **Lock ocupado (503 sostenido):** hay un ciclo o una copia largos
  (`deploy/box.sh status`). osquery reintenta solo.

## Qué no hace

No declara conformidad: los resultados son evidencias del equipo y las
revisa una persona.
