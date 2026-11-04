# 0017 — Pre-auditoría del expediente

- Estado: aceptada
- Fecha: 2026-11-04

## Contexto

La visión (`docs/vision.md`) pide revisar el expediente final en dos capas:

- una determinista, que hace el motor;
- otra de juicio, con un audit pack portable que ejecuta un modelo de
  lenguaje a través de un adaptador agnóstico.

La spec `pre-audit` dejaba abierta una pregunta: qué norma rige la
clasificación de los hallazgos. El expediente (ADR 0015) ya avisaba de que
la pre-auditoría no existía y exigía `--manual-judgment-review` en su lugar.

Enviar el expediente a un proveedor externo saca datos del equipo. Por eso
lo que se envía tiene que ser mínimo, estar redactado y contar con un
consentimiento expreso.

## Decisión

### Fuentes de la clasificación

Las descargó el equipo el 2026-11-04:

- **ITS de Auditoría** (Resolución de 27-3-2018, BOE-A-2018-4573), texto
  consolidado «sin modificaciones». Fuente: `boe.es`. sha256 del PDF:
  `9964f11f6eab58ae5d644be0889c251f022a6eb6cc71aedfe7d7cee2bbb8c7a5`.
  - El §VI.2 define «No Conformidad Menor», «No Conformidad Mayor» y
    «Observación». Usamos esas definiciones.
  - El §VI.3 regula el dictamen final (FAVORABLE…). **No se aplica**: la
    herramienta nunca emite un dictamen.
- **CCN-CERT IC-01/19** «Criterios Generales de Auditoría y Certificación»,
  edición de octubre de 2026, firmada digitalmente por el CCN el
  2026-10-13. Fuente: `ens.ccn.cni.es`. sha256 del PDF:
  `b5a7656e914a663e0707d07eaa5973cf02a78b9ccc9924974431888e45480815`.
  - El §52 obliga a distinguir mayores, menores y observaciones.
  - El §53 da los criterios de mayor y menor con el RD 311/2022.
  - El §54 da las reglas de agrupación.

La ITS sigue citando el RD 3/2010. La IC-01/19 indica que esas referencias
se entienden hechas al RD 311/2022.

Al repositorio solo van los párrafos citados, como fixtures de test, con su
URL, la fecha de descarga y el sha256: `tests/fixtures/ccn/`.

**La herramienta propone y nunca dictamina.** Todo hallazgo lleva
«propuesta; la decide una persona».

### Capa determinista

Trabaja sobre la instantánea del expediente, sin red. Sigue una tabla de
decisión fija y no inventa severidades:

| Hecho | Propuesta | Por qué |
|---|---|---|
| Check `FAIL` en una medida aplicable | `NC_MENOR` | ITS §VI.2: «fallo en la implantación… de uno o más de los requisitos». IC-01/19 §53: «incumplimiento parcial de alguna medida». |
| Evidencia ausente o caducada | `SIN_CLASIFICAR` | No hay evidencia objetiva de incumplimiento, solo falta información. |
| Check `UNKNOWN` | `SIN_CLASIFICAR` | Igual. |
| Hash que no cuadra | `SIN_CLASIFICAR` | Igual. |
| Integridad o estado del expediente roto | `SIN_CLASIFICAR`, con aviso | Igual. |

Una persona clasifica los `SIN_CLASIFICAR`. Los hallazgos deterministas no
generan el bloqueo `OPEN_FINDING`: sus hechos ya bloquean la preparación por
sí mismos.

### Agrupación (IC-01/19 §54)

Es solo una propuesta derivada:

- se conservan los hallazgos originales y se justifica la agrupación;
- varias menores de una medida → una menor;
- menores de varias medidas de un mismo grupo del catálogo (`op.pl`,
  `op.acc`…) → propuesta de mayor;
- nunca se agrupa entre grupos distintos y nunca se agrupan mayores.

La confirma una persona.

### Audit pack versionado

El pack v1 está en `src/ens_evidence/audit_pack/v1/`:

- `criteria.yaml`: cada criterio con su id, su texto, su fuente con cita
  literal, las medidas a las que aplica y las propuestas que admite;
- `system.md`: las instrucciones para el modelo;
- `response.schema.json`: el esquema de la respuesta;
- `manifest.json`: la versión y el sha256 de cada fichero.

El cargador rechaza un pack cuyo manifiesto no cuadra. La huella del pack es
el sha256 del manifiesto, y cada ejecución la registra.

Tiene tres criterios de juicio: coherencia entre política y evidencia,
calidad documental y solidez de las justificaciones de aplicabilidad. Todos
admiten solo `NC_MENOR` y `OBSERVACION`. **En la capa de juicio no hay
`NC_MAYOR`:** según el §54, una mayor solo sale de la agrupación o de una
persona.

### Payload: minimización, redacción y bloqueo posterior

`preaudit prepare` construye el payload solo con lo que piden los criterios:

- las medidas, con sus enlaces a documentos y evidencias;
- las justificaciones de no aplicabilidad y de compensación;
- los textos de los documentos organizativos;
- las evidencias enlazadas a medidas aplicables, con sus metadatos y su
  texto redactado si es texto y no pasa de 64 KiB. Si es binaria o más
  grande, va sin texto.

Cada fichero se comprueba contra el sha256 del expediente. Todo lo que viene
del expediente va marcado como datos no confiables: no son instrucciones.
Las instrucciones vienen solo del pack. El modelo no tiene herramientas.

La redacción:

- secretos → `[REDACTADO:secreto]`;
- correos, teléfonos, DNI/NIE, IBAN y similares → `[REDACTADO:<tipo>]`;
- IP → `[IP]`;
- personas declaradas con un rol → su rol;
- personas que firman un registro sin rol → `[PERSONA]`.

Después se vuelven a pasar todos los detectores sobre el payload final. Si
queda algo detectable, no se envía.

### Consentimiento

`prepare` guarda el payload en `evidence/preaudits/` y muestra su huella
(sha256 del JSON canónico), el destino, el modelo y la versión del pack.

`preaudit run --consent <huella> --by <nombre>` solo envía si coinciden con
lo preparado:

- la huella del payload, recalculada y la guardada;
- el destino;
- el proveedor y el modelo;
- el pack.

Si algo cambia, hay que volver a preparar y consentir. Sin `--consent` no
hay red: solo corre la capa determinista.

Hacia las API, solo HTTPS y sin seguir redirecciones. La clave va solo en la
cabecera hacia ese host.

### Proveedores

El modelo no tiene valor por defecto: lo elige quien configura. Las claves
solo van por el entorno; nunca en los ajustes.

- **API de Anthropic** (`ANTHROPIC_API_KEY`): Messages API con
  `anthropic-version: 2023-06-01` y la clave en `x-api-key`. Existe la
  versión `2026-10-21`, con `Authorization: Bearer` y sin `temperature`; no
  se usa todavía.
- **API de OpenAI** (`OPENAI_API_KEY`): Chat Completions con salida
  estructurada contra el esquema del pack.
- **Plan contratado** (decisión del usuario, 2026-11-04): las CLIs oficiales
  con la sesión de quien ejecuta, sin guardar tokens. El destino es el
  nombre de la CLI y su versión.
  - `claude-code`: `claude -p` sin herramientas, sin MCP y sin guardar la
    sesión.
  - `codex`: `codex exec` en sandbox de solo lectura y sin guardar la sesión.
    Se le desactiva la herramienta de shell, pero queda una ejecución de
    comandos que no se puede quitar y podría leer ficheros del equipo. Por
    eso `run` exige `--accept-shell-risk`, y el consentimiento lo registra.

En todos, el mismo contrato: timeout, validación del esquema y validación
semántica. De las CLIs no se guarda stdout ni stderr, solo la respuesta
validada.

Una respuesta que no cumple el esquema deja la capa de juicio en `ERROR`, sin
hallazgos. Si la cumple, se descarta cada hallazgo que no se apoye en el
payload: criterio inexistente, medida fuera del catálogo o del alcance del
criterio, documento o evidencia no enlazados a esa medida, o cita que no
aparece literalmente. El número de descartes queda visible.

### Disposición humana

Cada hallazgo de juicio tiene un estado, con un enum (ADR 0002):

- `OPEN` → `ACCEPTED`, `RESOLVED` o `DISMISSED`;
- `ACCEPTED` → `RESOLVED` o `DISMISSED`.

`ACCEPTED` lo reconoce y **sigue bloqueando**. Solo `RESOLVED` (con la
evidencia que lo corrige) y `DISMISSED` (con motivo) dejan de bloquear.

Las disposiciones se registran con `preaudit review` en una cadena de solo
adición, como el registro de incidentes (ADR 0013). La persona es declarada
y el motivo pasa por los detectores de privacidad.

El hallazgo tiene un id estable: criterio, medida y referencias. Una
ejecución nueva nunca cierra lo que propuso otra. Una disposición cubre las
apariciones hasta la ejecución que la motivó (`covers_run`). Si una ejecución
posterior vuelve a proponer el mismo hallazgo, se reabre: vuelve a `OPEN`
hasta que una persona lo disponga otra vez.

`run` y `review` escriben con el lock del ciclo tomado. Así una disposición
no se cruza con una ejecución, y una contradicción entre la secuencia y los
relojes solo puede venir de una manipulación. Con un ciclo en curso salen
con 2 sin escribir.

Una cadena ilegible, alterada, con fechas futuras o con un orden que
contradice los relojes bloquea con un error visible
(`preaudit_history_broken`). No se da por cerrado ningún hallazgo.

### Cableado y expediente

- Una única fuente, `PreAuditFindings`, alimenta el bloqueo `OPEN_FINDING`
  en todas las evaluaciones: `assessment status`, los informes diario y
  mensual, la generación del expediente y la evaluación antes de `declare`.
  Bloquean los hallazgos de juicio `NC_MAYOR` y `NC_MENOR` sin
  `RESOLVED` ni `DISMISSED`.
- Una ejecución apunta a la huella del expediente que revisó, y ese
  expediente no cambia. Sus hallazgos afectan a la evaluación actual y a los
  expedientes posteriores. En el revisado se ven en una vista derivada
  (`assessment dossier status`).
- Cada expediente congela las ejecuciones y las disposiciones que existían
  al generarse, como hace con los checks. `verify` reproduce con esas.
- Sin una capa de juicio en `DONE` sobre el expediente vigente, se mantiene
  el aviso «revisión humana pendiente».
- `--manual-judgment-review` **sigue siendo obligatorio** en `analyze` en
  BÁSICA. La pre-auditoría ayuda a la persona; no la sustituye.

## Consecuencias

- Los tests usan respuestas grabadas y CLIs falsas. Prueban el contrato, no
  la eficacia. La eficacia la mide `audit-benchmark`, con el umbral que fije
  el usuario.
- Las personas que no constan en ningún registro ni en las respuestas de
  org-docs pueden seguir apareciendo por su nombre en el texto de un
  documento o de una evidencia. Revisa el payload antes de consentir.
- Las ejecuciones reales esperan a las claves y a la sesión del usuario.
- Un error de orden en la cadena de disposiciones es permanente: la cadena
  es de solo adición. Se recupera restaurando `evidence/preaudits/` desde la
  copia (ADR 0008).
- Cada expediente congela las claves de las ejecuciones y disposiciones que
  existían al generarlo. Si falta una, la historia está rota: bloquea la
  preparación con un error visible en toda evaluación posterior a ese
  expediente y en `assessment dossier verify`. Para borrarla sin que se vea
  hay que alterar también cada expediente que la congeló.
- Hay dos manipulaciones que no se detectan en local; solo las detecta la
  copia externa (ADR 0008):
  - borrar toda la historia de pre-auditoría antes de generar el siguiente
    expediente. Equivale a no haberla ejecutado nunca. La pre-auditoría es
    opcional y no sustituye la revisión manual del juicio, que sigue siendo
    obligatoria;
  - borrar a la vez el último registro de una cadena y su reserva, como en
    las ADR 0013 y 0015. Si ese registro es la ejecución que reabrió un
    hallazgo descartado, el hallazgo deja de bloquear sin rastro.
- Los modos de plan contratado solo funcionan en el equipo de quien ejecuta.
  En la caja de Docker (ADR 0016) no hay CLIs ni sesión.
- Cambiar el pack exige regenerar su manifiesto, y cambia la huella: hay que
  volver a preparar y consentir.
