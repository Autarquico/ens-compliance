---
codename: ens-compliance
title: UI de documentos — estado, generación y aprobación
type: task
status: active
priority: P1
size: M
issue: 98
prs: []
related_issues: [98]
depends_on: [web-ui-flujo, org-docs-templates, ui-inventario]
supersedes: []
related_specs: [web-ui-flujo, org-docs-templates, ui-riesgos, ui-conectores]
created: 2026-11-19
updated: 2026-11-19
---

# UI de documentos — estado, generación y aprobación

## Contexto / problema

`ens-evidence docs` tiene seis órdenes (`questions`, `init`, `answer`, `build`, `approve`,
`status`; `cli.py:1823-1972`). En la web, la documentación organizativa vive solo dentro del
asistente: siete bloques de cuestionario, un paso «Generar» y un paso «Aprobar»
(`ui/wizard/docs.py:770-780`). Eso sirve para recorrer el flujo una vez, pero no para
operarlo después: no hay ninguna pantalla en la navegación que responda «¿qué documentos
tengo, cuál está aprobado y por quién, cuál se ha quedado obsoleto y por qué, y qué me falta
para volver a generar?». Tampoco se puede leer un documento sin pasar por el paso «Aprobar»
(`?ver=`), ni ver su historial de aprobaciones.

Es el quinto tramo de la UI de operación (inventario, copias, conectores, recogida). Sigue
el mismo patrón que `/inventario` frente al paso «activos»: una página de operación que
reutiliza las APIs de producto y la lógica del asistente, sin duplicar reglas.

Restricciones que no cambian:
- El rol que aprueba cada documento lo fija el producto (`orgdocs/approval.py:44-52,114-119`);
  la UI lo comprueba contra los roles asignados (ADR 0022).
- Generar exige alcance aprobado y respuestas completas (`orgdocs/build.py:75-84`); la UI no
  relaja nada, explica.
- Aprobar un documento no cambia el estado de ninguna medida (`cli.py:1945-1946`). Cubrir
  una cláusula no acredita que la medida esté implantada (`orgdocs/meta.py:31`).
- El nombre de quien aprueba (`by`) es **declarado**, no autenticado (ADR 0022, «Campo
  `by`»): la UI no lo prerrellena. Quién operaba la pantalla lo prueba la auditoría.

## Scope

**Entra:**
- **Página `/documentos`** con una fila por documento de las plantillas (`document_names()`,
  `orgdocs/keys.py:20`). Cada fila muestra:
  - **Estado**, con las etiquetas que ya usa el asistente (`ui/wizard/docs.py:84-89`):
    sin generar · generado, pendiente de aprobar · aprobado · aprobación caducada. Un
    documento generado que ya no coincide con sus entradas lleva sus motivos
    (`DocumentStatus.reasons`, `orgdocs/approval.py:99-108`), en lenguaje llano
    (`_plain`, `ui/wizard/docs.py:260-272`).
  - **Fuente del documento generado**: plantilla y versión, sha256 del documento, fecha de
    consolidación del catálogo y del articulado (`DocumentMeta`, `orgdocs/meta.py:30-45`).
  - **Última aprobación**: quién (nombre declarado), con qué rol y cuándo
    (`DocumentApproval`, `orgdocs/approval.py:57-79`). Sin aprobación, la fila lo dice; nunca
    se muestra como favorable.
  - **Quién debe aprobarlo**: el rol de la ficha (`required_role`) y el nombre declarado para
    ese puesto en las respuestas (`ROLE_QUESTION`, `orgdocs/approval.py:49-53`). Si la
    organización aún no ha declarado quién aprueba los procedimientos, se dice y se enlaza
    al bloque «Responsables» del asistente.
  - **Qué falta para generarlo**: preguntas pendientes o inválidas que afectan a ese
    documento, agrupadas por bloque y enlazadas al bloque del asistente
    (`_issue_groups`, `ui/wizard/docs.py:513-534`).
- **Ficha `/documentos/{documento}`**: el texto generado (Markdown renderizado como texto
  plano escapado, nunca HTML del fichero), su meta (fuente, hashes, cláusulas y medidas que
  cubre con la advertencia de `meta.py:31`), y el **historial de aprobaciones** legibles
  (`approval_history`, `orgdocs/approval.py:190-192`) más el aviso de registros ilegibles
  (`DocumentStatus.unreadable`). Un documento sin generar da 404 en la ficha y se explica en
  el listado.
- **Generar** (`POST /documentos/generar`, editores). Antes de ofrecer el botón, la página
  calcula lo que `build_documents` va a exigir y, si falta algo, muestra el motivo en lugar
  del botón:
  - alcance no aprobado (`scope_status`, mensaje `SCOPE_NOT_APPROVED` de
    `ui/wizard/docs.py:61`), con enlace al paso del asistente;
  - respuestas pendientes o inválidas, por bloque;
  - cuestionario sin empezar.
  Si aun así el POST llega y el producto rechaza, se muestran los motivos y no se escribe
  nada (`ScopeNotApproved`, `AnswersInvalid`). Tras generar, se listan los documentos
  creados, actualizados y sin cambios con su sha256 (`BuildReport`, `orgdocs/build.py:34-45`),
  y el aviso de edición manual archivada si procede.
- **Aprobar** (`POST /documentos/{documento}/aprobar`), un formulario por documento en estado
  generado o caducado:
  - campo «Quién lo aprueba» vacío, validado como nombre declarado (`declared_name`,
    `ui/wizard/base.py:100-106`; `check_declared_name`, `privacy_text.py:220`);
  - el rol lo resuelve el handler con `required_role` y lo exige con
    `permissions.require(role)` antes de escribir, como el paso `documentos-aprobar`
    (`ui/wizard/docs.py:756-757`); a quien no tiene el rol se le muestra qué rol falta y no
    se le ofrece el botón;
  - `approve_document` decide el resto: alcance, documento al día, nombre declarado
    coincidente con el puesto (`orgdocs/approval.py:335-380`); sus motivos se muestran en
    llano y no se escribe nada.
- **El cuestionario sigue en el asistente.** `/documentos` no duplica el formulario de
  respuestas: enlaza a cada bloque (`/asistente/documentos-*`) desde lo que falta. Un solo
  formulario, una sola validación.
- **Escritura:** auditoría de cada escritura y de cada rechazo (`ui/audit.py:25-63`), con el
  rol usado al aprobar; 303 a `/documentos` al terminar. `org/` ya está en la lista blanca
  (`ui/store.py:10-24`): no se amplía.
- Enlace «Documentos» en la navegación principal (`templates/base.html:22`) y desde la fase
  «Documentos» de la página de estado (`ui/views/status.py:98`).

**No entra:**
- Responder preguntas desde `/documentos` (se hace en los bloques del asistente).
- Editar el texto de un documento generado. Una edición manual la detecta y archiva
  `build_documents` (`orgdocs/build.py:95-104`); la UI solo lo avisa.
- Revocar o borrar una aprobación. El registro es de solo añadir
  (`orgdocs/approval.py:165-187`).
- Descargar el documento como fichero. Se lee en pantalla; la exportación va en el expediente.
- Cambiar quién aprueba los procedimientos (bloque «Responsables», exige `management`).

## Criterios de aceptación

### Scenario: estado de los documentos con fuente y aprobación
- **Given** un workspace con la política aprobada, la normativa generada sin aprobar y los procedimientos sin generar
- **When** se abre `/documentos`
- **Then** la política muestra «aprobado» con quién, rol y fecha, y la plantilla, hashes y fechas de consolidación de su meta; la normativa muestra «generado, pendiente de aprobar» con el rol que debe aprobarla; los procedimientos muestran «sin generar» con lo que falta, enlazado al bloque; ninguna fila sin aprobación aparece como favorable

### Scenario: no se puede generar y la página dice por qué
- **Given** un alcance no aprobado y dos preguntas del bloque «Incidentes» sin responder
- **When** un editor abre `/documentos`
- **Then** no hay botón «Generar»; la página dice que el alcance no está aprobado, con enlace al paso del asistente, y lista las dos preguntas pendientes enlazadas al bloque «Incidentes»; un POST directo a `/documentos/generar` devuelve los mismos motivos y no escribe nada

### Scenario: aprobar con el rol que exige el documento
- **Given** la política generada y al día, un usuario con `management` asignado y `firmante_direccion` declarado como «Ana Pérez»
- **When** envía «Quién lo aprueba» = «Ana Pérez» en el formulario de la política
- **Then** se escribe un registro en `org/docs/approvals/politica-seguridad/`, la fila pasa a «aprobado» con nombre, rol `management` y fecha, la auditoría recoge identidad, rol usado y claves, y ninguna medida cambia de estado

### Scenario: aprobación caducada por un cambio en las respuestas
- **Given** la política aprobada y una respuesta que usa modificada después en el asistente
- **When** se abre `/documentos`
- **Then** la política aparece como «aprobación caducada» con el motivo en llano, se ofrece «Generar» y, hasta que se regenere, el formulario de aprobar explica que el documento no está al día

### Scenario: solo lectura
- **Given** un usuario sin permiso de edición
- **When** abre `/documentos` y la ficha de un documento
- **Then** ve estados, fuentes, historial y texto sin botones de generar ni aprobar; un POST a `/documentos/generar` o a `/documentos/{documento}/aprobar` recibe 403 auditado y no se escribe nada

### Scenario: entrada hostil
- **Given** un nombre de documento que no está en las plantillas (`../org/answers`, `<script>`) y un documento generado cuyo texto contiene `<img onerror>`
- **When** se abre `/documentos/<ese nombre>` y la ficha del documento generado, y se envía un POST de aprobar con ese nombre
- **Then** la ficha desconocida da 404 sin escribir nada; el texto del documento se muestra escapado, no se ejecuta; el POST se rechaza con «No reconocemos ese documento» y no se escribe nada

## Notas técnicas / riesgos

- Funciones de producto que se reutilizan (verificado):
  - estado: `all_statuses`, `document_status` (`orgdocs/approval.py:310-333`);
  - rol y firmante: `required_role`, `ROLE_QUESTION`, `FIXED_AUTHORITY`
    (`orgdocs/approval.py:44-53,114-119`);
  - historial: `approval_history`, `scan_approvals` (`orgdocs/approval.py:165-192`);
  - generar: `build_documents`, `AnswersInvalid`, `BuildReport` (`orgdocs/build.py:21-45,75`);
  - aprobar: `approve_document`, `DocumentApprovalRejected`, `UnknownDocument`
    (`orgdocs/approval.py:81-88,335`);
  - respuestas: `validate_answers` (`orgdocs/answers.py:220`), `declared_people`
    (`answers.py:122`);
  - claves: `ANSWERS_KEY`, `document_key`, `meta_key`, `document_names`, `HISTORY_PREFIX`
    (`orgdocs/keys.py:5-28`), `APPROVALS_PREFIX` (`orgdocs/approval.py:42`);
  - alcance: `scope_status`, `ScopeNotApproved` (`ens_evidence.scope`).
- Lógica del asistente que se extrae a un módulo compartido (sin cambiar comportamiento):
  `_document_rows`, `_issue_groups`, `_plain`, `STATE_LABELS`, `STATE_TONES`, `ROLE_LABELS`,
  `_signatory` (`ui/wizard/docs.py:79-95,260-272,513-548,597-599`). Los pasos
  `documentos-generar` y `documentos-aprobar` siguen existiendo y pasan a usar el mismo
  módulo.
- Autorización (`ui/authz.py:107-128`): `("POST", "/documentos/generar"): EDIT` y
  `("POST", "/documentos/{documento}/aprobar"): EDIT` con el rol resuelto en el handler, igual
  que `documentos-aprobar` (`authz.py:69`). El test que exige que la tabla coincida con las
  rutas (`tests/ui/test_authorization.py:97`) cubre las nuevas.
- Lista blanca: las claves escritas (`org/answers.yaml`, `org/docs/*.md`, `*.meta.yaml`,
  `org/docs/history/*`, `org/docs/approvals/*`) están bajo `org/`, ya admitido
  (`ui/store.py:10-24`). Sin cambios.
- Campo `by`: no se prerrellena ni desde el JWT ni desde las respuestas. El producto
  comprueba que el nombre coincide con la persona declarada para el rol
  (`orgdocs/approval.py:122-130,370-371`); la página muestra ese nombre como referencia,
  no lo mete en el campo.
- El meta no registra el instante de generación (`orgdocs/meta.py:30-45`); el único
  timestamp disponible es el de cada aprobación. La fecha de generación no se inventa: la
  fila muestra sha256 y versiones. Añadir ese instante al meta cambiaría el modelo y la
  equivalencia UI/CLI; queda fuera de este tramo.
- El texto del documento se renderiza con autoescape (CSP `default-src 'self'`, ADR 0021);
  no se interpreta el Markdown en este tramo. Lectura acotada del fichero como en `/copias`.
- Sin verificar: coste de `all_statuses` (lee meta, aprobaciones, alcance y respuestas por
  documento) en cada carga; hoy el paso «Generar» ya lo hace sin caché.

## Referencias

- `docs/decisions/0021-interfaz-web.md`, `docs/decisions/0022-roles-y-mfa-en-la-interfaz-web.md`
- `docs/specs/web-ui-flujo.md`, `docs/specs/org-docs-templates.md`,
  `docs/specs/ui-inventario.md`
- `src/ens_evidence/orgdocs/`, `src/ens_evidence/ui/wizard/docs.py`, `src/ens_evidence/cli.py:1823-1972`
