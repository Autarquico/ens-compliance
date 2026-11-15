# Validación y cierre de v0.1

Procedimiento humano para cerrar v0.1: validas la herramienta con las pruebas de
la guía (fase 14, tabla de validación), autoauditas Autarqui y la caja, y
recorres los estados del expediente. Lo ejecutas tú, con sistemas reales.

Qué es y qué no es:

- **Valida la herramienta; no certifica nada.** La herramienta nunca declara
  conformidad por sí sola ni garantiza el cumplimiento del ENS. Aunque todo
  salga bien, la conformidad la declara una persona (BÁSICA) o la decide una
  auditoría de certificación (MEDIA y ALTA).
- Las pruebas automáticas del repositorio cubren cada fila con datos ficticios
  (ver la [matriz de pruebas](../validation/matriz-de-pruebas.md)). Este
  runbook cubre la parte que solo puede hacer una persona sobre sistemas
  reales.
- **Si no llegas a `READY` (paso 5), v0.1 no se cierra.** No se rebaja la regla.

Contratos: [ADR 0015](../decisions/0015-expediente-de-autoevaluacion.md),
[ADR 0017](../decisions/0017-pre-auditoria.md). Procedimientos que usas aquí:
[caja](caja.md), [restauración](restauracion-de-evidencias.md),
[expediente](expediente-de-autoevaluacion.md), [pre-auditoría](pre-auditoria.md).

## 0. Dónde vive el workspace real

El workspace de Autarqui y el de la caja contienen datos reales (inventario,
evidencias, expedientes). **Viven fuera del repositorio** y nunca se
commitean. Ejemplos ficticios:

| Workspace | Ruta de ejemplo | Ajustes |
|---|---|---|
| Autarqui (tu equipo) | `~/ens-workspaces/organizacion-ejemplo/` | `~/.config/ens-evidence/settings.yaml` |
| Caja desplegada | volumen `ens-box_data` (`/data` en el contenedor) | `/data/settings.yaml` |

En los comandos de este runbook, `ens-evidence` es el de tu equipo; para la
caja usa el alias de [caja, sección 5](caja.md):
`ens() { docker compose -p ens-box exec -T app ens-evidence "$@"; }`.

Las notas de cada prueba (texto libre, capturas) las guardas también fuera del
repositorio, p. ej. `~/ens-workspaces/notas/`. No pongas en ellas tokens,
claves ni datos de personas que no hagan falta.

## 1. Cómo registras cada resultado

Cada prueba deja rastro en el workspace, no en el repositorio:

- **Lo que registra la herramienta sola.** `collect` guarda el intento como
  evidencia, también cuando falla (conector en `ERROR`); `check` registra la
  ejecución; `backup run`, `backup restore` y `backup check` registran la suya.
- **Lo que registras tú**, con una nota breve en un fichero (qué hiciste, a qué
  hora, qué viste, cómo lo revertiste):

  ```bash
  ens-evidence evidence import ~/ens-workspaces/notas/validacion-01-token-revocado.md \
    --source manual --asset <id-del-activo> --by "Lucía Navarro Gil" \
    --collected-at 2026-11-10T10:30:00+01:00
  ```

  `--asset` es un activo del inventario (`inventory validate` lo comprueba).
  `--by` es un nombre declarado: la herramienta no lo autentica. El hash prueba
  que la nota no cambió, no que sea auténtica.
- **Solo para la restauración**: `backup restore-test record` (prueba 2.5).

Una nota manual **no sustituye** al resultado de la herramienta: cítalo en la
nota (comando, código de salida, id de la evidencia que generó).

## 2. Pruebas de la tabla de validación

Antes de empezar: alcance aprobado (`scope status`), `evidence verify --all`
sin errores y una copia reciente (`backup run`, `backup status`).

Las acciones destructivas o sobre sistemas reales (revocar un token, quitar
una protección, forzar una copia fallida) **las decides y las haces tú**, solo
en recursos de prueba y con la vuelta atrás a mano. Ningún comando de este
runbook las hace por ti.

### 2.1 Revocar API test: `ERROR`, nunca `PASS`

1. Prepara: crea un token de prueba nuevo (GitHub y Cloudflare, de solo lectura,
   [GitHub](../connectors/github.md), [Cloudflare](../connectors/cloudflare.md)).
   No uses el token de producción.
2. Carga el token de prueba en `ENS_GITHUB_TOKEN` (desde el gestor de contraseñas) y comprueba que funciona con `ens-evidence collect github`.
3. Revócalo tú en el proveedor (GitHub: *Settings > Developer settings*;
   Cloudflare: *My Profile > API Tokens*).
4. Ejecuta de nuevo:

   ```bash
   ens-evidence collect github       # idem: collect cloudflare, con ENS_CLOUDFLARE_API_TOKEN
   ens-evidence check --all
   ```

5. Qué miras: `collect` sale con 1 (error global, token inválido o revocado),
   la evidencia del intento queda guardada y ningún check que dependa de ese
   conector da `PASS` (el sistema queda `NOT READY`).
6. Registra: nota manual (sección 1) con el id de la evidencia en `ERROR`.
7. Vuelta atrás: el token revocado no se recupera. Borra el token de prueba del
   entorno (`unset`) y restablece el de producción en tu gestor de
   contraseñas. Recoge de nuevo (`collect --all`) para que el estado vigente
   no sea el del fallo.

### 2.2 Desconectar agente: cobertura perdida

La caja ya trae el agente osquery ([agentes osquery](agentes-osquery.md)),
pero en v0.1 sus resultados no alimentan los checks HOST: estos solo leen el
paquete que importas ([endpoint](../connectors/endpoint.md)). Por eso la
cobertura perdida se prueba de dos formas: por caducidad del paquete (pasos 1
a 5, la que bloquea el expediente) y, si tienes agentes, por el aviso de
agente sin reportar (paso 6).

1. Prepara: un equipo de prueba inscrito, con un paquete importado.
2. Haz que caduque: espera a `max_evidence_age_days` o evalúa en una fecha
   posterior, sin tocar nada:

   ```bash
   ens-evidence endpoint check --at 2026-12-31T10:00:00+01:00
   ens-evidence assessment status --at 2026-12-31T10:00:00+01:00
   ```

3. Qué miras: `endpoint check` sale con 4 e indica evidencia caducada;
   `assessment status` (con la misma fecha) muestra el bloqueo
   `EXPIRED_EVIDENCE` y HOST-001 no da `PASS`.
4. Registra: nota manual con la fecha usada en `--at` y la salida.
5. Vuelta atrás: ninguna; `--at` no escribe. Si dejaste caducar un paquete real,
   vuelve a ejecutar el script del equipo e impórtalo.
6. Si el equipo de prueba tiene agente: para `osqueryd` en él
   (`sudo systemctl stop osqueryd` en Linux,
   `sudo launchctl bootout system/io.osquery.agent` en macOS,
   `Stop-Service osqueryd` como Administrador en Windows) y espera más
   de 3 h. Qué miras: en `agent hosts`
   ([agentes osquery, sección 2](agentes-osquery.md)) el último contacto deja
   de avanzar, y el [panel de monitorización](monitorizacion.md) marca
   «Agente sin reportar». Registra: nota manual con la salida de
   `agent hosts` y la hora. Vuelta atrás: arranca de nuevo `osqueryd`
   (`sudo systemctl start osqueryd`; `sudo launchctl bootstrap system
   /Library/LaunchDaemons/io.osquery.agent.plist` en macOS, no verificado en
   equipo real; `Start-Service osqueryd` en Windows) y comprueba que el último
   contacto vuelve a avanzar.

### 2.3 Quitar protección test: desviación

1. Prepara: un repositorio de **prueba** (nunca el de producción) con
   protección de la rama por defecto; `collect github` con `PASS` en
   `github.default_branch_protected`. Anota los ajustes de la protección antes de tocarla.
2. Quítala tú en GitHub (*Settings > Branches*).
3. Ejecuta:

   ```bash
   ens-evidence collect github
   ens-evidence check --all
   ens-evidence report daily
   ens-evidence assessment status
   ```

4. Qué miras: `check --all` muestra `github.default_branch_protected` en `FAIL`
   (o en un estado que no es `PASS`); el informe diario recoge el cambio
   respecto de la ejecución anterior; `assessment status` queda `NOT READY` por
   ese check.
5. Registra: nota manual con el id de ambas evidencias.
6. Vuelta atrás: restaura la protección con los ajustes que anotaste y recoge
   de nuevo.

### 2.4 Backup fallido: hallazgo

1. Prepara: copia sana (`backup run` termina bien).
2. Provócalo tú de forma reversible, solo en tu sesión: exporta una
   `ENS_BACKUP_AWS_SECRET_ACCESS_KEY` incorrecta (el valor real sigue en el
   gestor de contraseñas).
3. Ejecuta:

   ```bash
   ens-evidence backup run
   ens-evidence backup status
   ens-evidence collect restic
   ```

4. Qué miras: `backup run` termina en `failed`, `backup status` sale con 1 y
   los checks BACKUP-* no dan `PASS`: la evidencia no se da por copiada.
5. Registra: nota manual con el id de la ejecución fallida.
6. Vuelta atrás: `unset` de la variable, restaura la correcta y repite
   `backup run` hasta tener una copia buena.

### 2.5 Restore: evidencia

Es la restauración de [restauración de evidencias](restauracion-de-evidencias.md),
siempre en una carpeta vacía y aislada, nunca sobre el workspace en uso.

```bash
ens-evidence backup restore --snapshot latest --target ~/restauraciones/prueba-01
```

Qué miras: informa de que el inventario restaurado coincide con el del
snapshot. Si dice «NO verificado», no la des por buena. Regístrala con un
fichero `prueba.yaml` (plantilla y reglas en [Restic](../connectors/restic.md),
sección «Restore test»; `performed_at` a menos de 7 días de la restauración):

```bash
ens-evidence backup restore-test record prueba.yaml
```

`reviewer` y `role` son declarados. Después, `ens-evidence collect restic`
evalúa BACKUP-005 con ese registro.

### 2.6 Alterar evidencia: el hash falla

**Solo sobre una copia restaurada, nunca sobre el workspace vivo.** Alterar una
evidencia del workspace real rompe su cadena y no hay deshacer.

1. Restaura en una carpeta vacía (2.5) y crea un workspace desechable con unos
   ajustes aparte (`copia-settings.yaml`, con `workspace.local.path` apuntando a
   una carpeta nueva y vacía, como en el paso 2 de la
   [restauración completa](restauracion-de-evidencias.md)):

   ```bash
   ens-evidence --settings copia-settings.yaml workspace import --all ~/restauraciones/prueba-01
   ens-evidence --settings copia-settings.yaml evidence verify --all     # base: sin errores
   ```

2. Cambia a mano un byte de un artefacto de la **copia** (el fichero que
   indica el manifiesto de una evidencia, bajo la carpeta del workspace
   desechable).
3. Ejecuta de nuevo `evidence verify --all` con `--settings copia-settings.yaml`.
4. Qué miras: error de hash en esa evidencia y salida distinta de 0. El
   workspace real, comprobado con `ens-evidence evidence verify --all`, sigue sin errores.
5. Registra: nota manual con la evidencia alterada y la salida.
6. Vuelta atrás: borra la carpeta desechable y la de restauración.

### 2.7 LLM caído: los checks siguen

1. Ejecuta la pre-auditoría sin consentir la capa de juicio (sección 5, punto 3): es
   exactamente el caso de LLM no disponible.
2. Qué miras: la capa determinista se completa y el estado dice «capa de
   juicio no ejecutada; revisión humana pendiente». `check --all` y
   `assessment status` funcionan igual sin red hacia el proveedor.
3. Opcional, con consentimiento: con una `ANTHROPIC_API_KEY` incorrecta en tu
   sesión, `preaudit run --consent … --by …` termina la capa de juicio en
   `ERROR` y sale con 1; la determinista sigue ahí. Después `unset`.
4. Registra: nota manual con el número de ejecución de `preaudit`.

### 2.8 Storage caído: la recogida no es OK

Solo si tu workspace es S3/R2 (en local no hay storage remoto que tirar).
Con unos ajustes aparte (`--settings caido-settings.yaml`) que apunten a un
bucket inexistente o con credenciales de prueba incorrectas:

```bash
ens-evidence --settings caido-settings.yaml collect --all
```

Qué miras: la recogida no termina con 0 y no se presenta como correcta. No
verificado contra un R2 real en el repositorio: si el resultado es otro,
anótalo y abre una incidencia. No toques el workspace real ni sus credenciales.

### 2.9 Reejecución: idempotencia

1. Ejecuta dos veces seguidas `ens-evidence check --all` sin que haya cambiado
   nada.
2. Qué miras: mismos estados; una ejecución nueva que da lo mismo no cambia el
   estado del expediente (`assessment dossier status <id>` no señala cambios).
3. Con `ens-evidence evidence import <paquete> --source endpoint --asset <id> --by …`
   del mismo paquete de endpoint dos veces: la segunda se rechaza y señala la evidencia existente.

### 2.10 Recuperación de `ens-evidence`

Prueba en una máquina limpia (sin la caja ni tu workspace): instala
`ens-evidence` y restic, recupera `settings.yaml` y los secretos de tu gestor
de contraseñas, y sigue la «Restauración completa en una máquina nueva» de
[restauración de evidencias](restauracion-de-evidencias.md). Criterio: `restore`
informa de que el inventario coincide y `evidence verify --all` no da errores.
Regístralo con una nota manual (fecha, snapshot, máquina). Si algo de lo que
necesitaste no estaba fuera de la caja, corrígelo ya.

## 3. Autoauditoría de Autarqui

En el workspace de Autarqui, con el alcance y la categorización aprobados:

```bash
ens-evidence collect --all
ens-evidence check --all
ens-evidence assessment status
```

`assessment status` sale con 0 solo si el sistema está preparado. Resuelve los
bloqueos que indica (`ens-evidence matrix status`, [matriz](matriz-de-cumplimiento.md)).

## 4. La caja: su propio workspace y su expediente

La caja tiene su propio workspace, en el volumen `ens-box_data`, con sus
propios ajustes (`/data/settings.yaml`). Es el workspace donde
[caja, sección 5](caja.md) declara la caja (`srv-caja`) como activo y aprueba
su alcance. Por tanto, **la caja tiene su propio expediente**, distinto del de
Autarqui, y hace falta llevarlo a `READY` también. (La visión pide ese segundo
expediente: «los de Autarqui y el de la propia caja».)

Todos los comandos de esta sección y de la 5 para la caja van con el alias
`ens` (sección 0), no con `ens-evidence`. Prerrequisitos del workspace de la
caja, los mismos que en el de Autarqui (`assessment dossier generate` exige
categorización vigente y Declaración de Aplicabilidad; `assessment status`
exige revisiones de la matriz):

1. Alcance aprobado: `ens scope status` en `APPROVED` ([caja, sección 5](caja.md)).
2. Categorización aprobada: `ens categorization init` / `approve` / `status`.
3. Declaración de Aplicabilidad aprobada: `ens applicability propose` /
   `validate` / `approve`.
4. Documentación organizativa generada y aprobada: `ens docs build` / `approve`
   ([documentación](documentacion-organizativa.md)).
5. Matriz con responsable, periodicidad y revisión por medida:
   `ens matrix init` / `set` / `link` / `review`
   ([matriz](matriz-de-cumplimiento.md)).
6. Para la pre-auditoría (sección 5): una sección `preaudit:` en
   `/data/settings.yaml` con `provider` y `model` ([pre-auditoría, §1](pre-auditoria.md)).
   En la caja solo sirven `anthropic` y `openai`, y la clave
   (`ANTHROPIC_API_KEY` u `OPENAI_API_KEY`) tiene que llegar al contenedor
   `app`: añádela a `deploy/.env` (`ANTHROPIC_API_KEY=` u `OPENAI_API_KEY=`;
   `.env.example` aún no las lista) y ejecuta `deploy/box.sh restart app`
   ([caja, sección 3](caja.md)). Sin clave la pre-auditoría ejecuta solo la capa
   determinista: `READY` sigue siendo alcanzable, pero el expediente queda con
   «revisión humana pendiente» y el análisis (sección 6, punto 2) tiene que
   llevar `--manual-judgment-review`.
7. Lo que la propia caja aporta a su auditoría, si lo usas: acceso con el
   proveedor de identidad ([acceso con proveedor de identidad](acceso-con-proveedor-de-identidad.md)),
   panel de monitorización y agente del VPS ([monitorización](monitorizacion.md),
   [agentes osquery](agentes-osquery.md)) y protección frente a código dañino
   ([antimalware](kit-antimalware.md)). Revisa sus checks en el paso siguiente.

Después, recoge y comprueba:

```bash
ens collect --all
ens check --all
ens assessment status
```

## 5. Pre-auditoría y regla de preparación

Para cada expediente (el de Autarqui con `ens-evidence`; el de la caja, con
el alias `ens`):

1. Genera el expediente y apunta el id:

   ```bash
   ens-evidence assessment dossier generate
   ```

2. Prepara el envío y léelo entero antes de consentir (muestra la huella):

   ```bash
   ens-evidence preaudit prepare <expediente>
   ```

3. Ejecuta. Sin consentimiento corre solo la capa determinista; con él, también
   la de juicio ([pre-auditoría](pre-auditoria.md), §3):

   ```bash
   ens-evidence preaudit run <expediente>
   ens-evidence preaudit run <expediente> --consent <huella> --by "Lucía Navarro Gil"
   ```

4. Lee los hallazgos con `preaudit show <número>`. Dispón los de juicio con
   `preaudit review <id> --by … --decision accepted|resolved|dismissed`
   (`resolved` exige una evidencia nueva; `dismissed`, un motivo). Los
   deterministas se arreglan donde nacen, no con `review`.
5. Comprueba:

   ```bash
   ens-evidence assessment status
   ```

   Tiene que decir `READY` (salida 0). Si queda un hallazgo abierto es `NOT READY`.
   Si la capa de juicio no se ejecutó, el estado añade «revisión humana
   pendiente»: no bloquea `READY`, pero obliga a `--manual-judgment-review` en
   la sección 6.
   `READY` no es «cumple»: solo dice que la herramienta no encuentra bloqueos.

**Si el expediente de Autarqui o el de la caja no llegan a `READY`, v0.1 no se
cierra.** Corrige, genera un expediente nuevo y repite desde 1. Anota qué
bloqueaba y cómo lo resolviste.

## 6. Estados del expediente

Con el expediente en `READY`, las personas lo avanzan; la herramienta solo
registra ([expediente](expediente-de-autoevaluacion.md)). Haz una copia antes de
cada paso (`backup run`).

1. **SELF_ASSESSED.** Quien administra el sistema o su delegado:

   ```bash
   ens-evidence assessment dossier self-assess <id> --by "Ana Pérez" --role system_administrator
   ```

2. **ANALYZED.** El responsable de la seguridad analiza, concluye y **eleva las
   conclusiones al responsable del sistema** (la herramienta no envía nada:
   `--elevated` confirma que lo has hecho tú):

   ```bash
   ens-evidence assessment dossier analyze <id> --by "Lucía Navarro Gil" \
     --conclusion "…" --elevated-to "Hugo Serrano Ortiz" --elevated \
     --manual-judgment-review
   ```

   El análisis no se repite. `--manual-judgment-review` confirma que una
   persona ha revisado lo que la herramienta no puede valorar sola.

3. **Según la categoría del sistema** (`assessment dossier list` la muestra):

   - **BÁSICA → DECLARED.** La dirección registra la declaración, con su nombre
     y fecha; el sistema tiene que seguir preparado en ese momento:

     ```bash
     ens-evidence assessment dossier declare <id> --by "Marta Ibáñez Soler"
     ```

     Si quieres enlazar el documento firmado, impórtalo antes y pasa
     `--evidence EV-…`. La herramienta no redacta la declaración ni genera el
     distintivo; la firma y el formato los revisa una persona. Luego
     toca darle publicidad (art. 38.2), fuera de la herramienta.
   - **MEDIA (o ALTA) → no hay declaración.** El expediente queda **preparado
     para certificación**: se necesita una auditoría de certificación
     (art. 38.1). No ejecutes `declare`. Autoevaluación y análisis valen como
     revisión interna.

4. Comprueba y copia el resultado:

   ```bash
   ens-evidence assessment dossier status <id>
   ens-evidence assessment dossier verify <id>
   ens-evidence backup run
   ```

Los nombres son declarados: la herramienta no autentica a nadie.

## 7. Criterio de cierre de v0.1

v0.1 se cierra solo si se cumple todo:

- las pruebas de la sección 2 hechas y registradas en el workspace;
- `assessment status` en `READY` en el workspace de Autarqui y en el de la caja;
- expediente en `ANALYZED` y, si es BÁSICA, `DECLARED` con persona y fecha; si
  es MEDIA, «preparado para certificación»;
- el banco de defectos sembrados de la versión actual del audit pack supera el
  umbral de la [visión](../vision.md) («Validación del auditor»): 100 % de los
  defectos deterministas y ≥ 80 % de los de juicio con ≤ 20 % de falsas
  alarmas, por proveedor, medido en la partición reservada
  ([banco de defectos](banco-defectos.md));
- el workspace real sigue fuera del repositorio y con copia.

**Estado del umbral a 2026-11-08: no alcanzado.** Lo atienden las specs
`audit-pack-generalization` y `audit-openai-pair` (ambas `active`). Hasta que
se cumpla, v0.1 no puede cerrarse, aunque todo lo demás de este runbook salga
bien. Que el banco supere el umbral valida la herramienta; no aprueba ningún
expediente real.

Si algo falla: no se cierra. Anota el motivo y qué decide cada persona.
