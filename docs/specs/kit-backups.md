---
codename: ens-compliance
title: "Kit: copias de seguridad con restic y Backrest"
type: task
status: active
priority: P1
size: L
issue: 66
prs: [73]
related_issues: [66]
depends_on: [box-packaging, collector-restic, web-ui-flujo]
supersedes: []
related_specs: [toolkit-server, installer]
created: 2026-11-09
updated: 2026-11-09
---

# Kit: copias de seguridad con restic y Backrest

## Contexto / problema

Primera herramienta del kit (`toolkit-server`, #65; ADR 0019). Una pyme sin copias de seguridad hoy solo ve que mp.info.6 está sin cubrir. Con esta spec, la app le da un sistema de copias, lo despliega en el VPS de la caja y recoge la evidencia de que funciona.

Es también la primera instancia del contrato del kit: servicio, conector de solo lectura, checks de uso real, runbook y aviso. Se construye en concreto para las copias, y el contrato común se extrae cuando haya dos o tres herramientas.

Piezas:
- **restic** hace las copias. El conector de Restic (`collector-restic`, BACKUP-001…006 y pruebas de restauración) ya existe y está probado sobre la copia propia de la caja.
- **Backrest** (GPL-3.0, verificado en GitHub el 2026-11-09) es la interfaz web para programar copias de restic, ver su historial y restaurar.

## Scope

**Entra:**
- **Perfil `copias` en `compose.yaml`.**
  - Servicio `backrest` con la imagen upstream fijada por digest y los mismos privilegios mínimos que el resto de la caja (ADR 0016): `cap_drop: [ALL]`, `no-new-privileges`, sin puertos publicados y sin socket de Docker.
  - Su propia red, con salida solo hacia el destino S3.
  - Monta en solo lectura lo que se copia: los volúmenes de la caja y del kit, y las rutas del host que elija el usuario.
  - Caddy lo publica en `copias.<dominio>`, detrás de Access, como la web.
- **Asistente de copias en la web** (`/herramientas/copias`). Pide:
  - el destino: S3 compatible con bloqueo de objetos (R2, OVH Object Storage, Scaleway, Backblaze B2…);
  - qué se copia;
  - la periodicidad y la retención, que salen de la política de copias de la documentación organizativa (mp.info.6.1 lo remite a la normativa interna).

  Con eso genera la configuración de Backrest y el comando para activar el perfil. Los secretos (contraseña del repositorio, claves S3 de escritura y usuario inicial de Backrest) se muestran una sola vez y no se guardan en la app.
- **Credenciales separadas.**
  - Backrest guarda las de escritura de sus repositorios.
  - La app solo recibe credenciales S3 de lectura y la contraseña del repositorio, para listar copias sin bloquear (`--no-lock`).
  - Todo es distinto de la copia propia de la caja (ADR 0008).
- **Conector de Restic para varios repositorios.**
  - Además de la copia propia de la caja, lee cada repositorio de copias de la organización con BACKUP-001…006: última copia correcta, errores, integridad, retención y prueba de restauración vigente.
  - Lee los repositorios, no la API de Backrest. Así la evidencia no depende de la interfaz que se vigila.
- **Otros equipos.** La app genera el comando para instalar Backrest (o restic con su programación) en otro servidor de la pyme, con su propio repositorio en el mismo destino, y lo lee igual.
- **Matriz.** mp.info.6 recibe la evidencia por repositorio. Mientras la medida no tenga evidencia, su ficha propone el kit de copias con lo que cubre y lo que no (R1 desde MEDIO: la prueba de restauración la hace una persona).
- **Panel de acceso mínimo** (`/herramientas`): la tarjeta de Copias, con su estado (al día, con problemas o caída, y la última comprobación) y el botón «Abrir». Es la primera tarjeta; el panel completo es `kit-access-panel`.
- **Runbook** `docs/runbooks/kit-copias.md`: alta, primera prueba de restauración, restauración completa del VPS en una máquina nueva, actualización de la imagen de Backrest y rotación de claves.

**No entra:**
- Copias de servicios en la nube como Google Workspace o Microsoft 365 (se evidencian por sus conectores).
- Copias de portátiles de usuario (pregunta abierta).
- Elegir o contratar el destino S3 por la pyme (guía en `setup-guides`).
- El panel de monitorización (`kit-monitoring`).

## Criterios de aceptación

### Scenario: activar las copias desde la web
- **Given** la caja en un VPS y un destino S3 con bloqueo de objetos
- **When** el usuario completa el asistente de copias y ejecuta el comando que le da
- **Then** Backrest arranca en `copias.<dominio>` detrás de Access, hace la primera copia, y en la siguiente vuelta del scheduler la app la ve y la deja como evidencia de mp.info.6

### Scenario: instalar no es cumplir
- **Given** Backrest desplegado y sin ninguna copia correcta
- **When** corren los checks
- **Then** mp.info.6 no tiene evidencia vigente y el check dice qué falta (sin copia correcta, o sin prueba de restauración)

### Scenario: una copia que deja de hacerse se ve
- **Given** un plan de copias cuya última copia correcta es más antigua que la periodicidad de la política
- **When** la app recoge evidencias
- **Then** la tarjeta de Copias sale en rojo con el enlace al runbook, BACKUP-001 da `FAIL` y aparece como hallazgo en la matriz

### Scenario: la app no puede borrar copias
- **Given** las credenciales de lectura que tiene la app
- **When** se intenta borrar o sobrescribir una copia con ellas
- **Then** el destino lo rechaza (prueba en CI con un S3 local que aplica la política; prueba manual registrada en R2)

### Scenario: los secretos no se quedan en la app
- **Given** la configuración generada por el asistente
- **When** el usuario la descarga
- **Then** se muestra una sola vez, y ni la contraseña del repositorio ni las claves de escritura aparecen en el workspace, las evidencias, el expediente ni los logs

### Scenario: restaurar el VPS entero
- **Given** el VPS perdido y las copias en el destino
- **When** se sigue el runbook en una máquina nueva
- **Then** la caja y Backrest vuelven con sus datos, y la prueba queda registrada como prueba de restauración (revisión humana)

## Notas técnicas / riesgos

- **Backrest v1.14.1, verificado el 2026-11-09 en su repositorio (README, `proto/v1/config.proto`, Dockerfiles):**
  - Hay binarios para Linux, macOS y Windows, e imagen Docker `ghcr.io/garethgeorge/backrest` (variantes alpine y scratch) con restic dentro de la imagen. Fuera de Docker descarga restic solo y lo actualiza; se evita fijando `BACKREST_RESTIC_COMMAND` a un binario verificado.
  - Configuración en un `config.json` (`BACKREST_CONFIG`): `repos` (uri, password en claro, env, prunePolicy, checkPolicy), `plans` (paths, schedule cron, retention) y `auth.users` con `passwordBcrypt`. La app puede generar el fichero entero, con el usuario ya creado.
  - La contraseña del repositorio va en claro en `config.json`: el volumen de configuración de Backrest es secreto y no lo lee la app.
  - Tiene modo multihost (emparejamiento de instancias con tokens). Sirve para «otros equipos»; se evalúa en el plan.
  - Las imágenes no fijan `USER`: se ejecuta con un UID explícito, que necesita permiso de lectura sobre lo que copia.
- **Retención frente a bloqueo de objetos.** `forget` y `prune` de Backrest fallan sobre objetos todavía bloqueados. La retención del plan tiene que ser mayor que el periodo de bloqueo, y la app lo valida al generar la configuración.
- **Sin verificar:** restic sobre el bloqueo de objetos de R2 (ya anotado en `docs/vision.md`).
- **Lo que se copia en solo lectura.** Montar volúmenes de otros servicios no da a Backrest permiso de escritura sobre ellos. Se excluyen los tmpfs y el socket de Docker.
- **El destino es un punto único de fallo.** Por eso se recomienda un proveedor distinto del de la copia propia de la caja, o al menos credenciales y administración separadas (ADR 0008).
- **Review de seguridad obligatoria:** credenciales, montajes y exposición tras Access.

## Tasks (no autoritativo — el board manda)

- [ ] Verificar Backrest con la imagen fijada: configuración, autenticación y plataformas
- [ ] Perfil `copias` en compose.yaml y ruta en Caddy tras Access
- [ ] Asistente `/herramientas/copias` y generación de la configuración, con los secretos mostrados una sola vez
- [ ] Conector de Restic con varios repositorios y credenciales de lectura
- [ ] Recomendación en la ficha de mp.info.6 y tarjeta en `/herramientas`
- [ ] Comando para otros equipos
- [ ] Runbook `kit-copias.md` y prueba de restauración completa del VPS
- [ ] Tests: escenarios, hostiles (credenciales de escritura en la app, rutas fuera de lo elegido) y CI con S3 local

## Referencias

- docs/specs/toolkit-server.md, collector-restic.md, box-packaging.md
- docs/decisions/0008-evidencias-y-copia-independiente.md, 0016-caja-compose.md, 0019-caja-y-herramientas-en-un-vps.md
- https://github.com/garethgeorge/backrest
- https://restic.readthedocs.io/
