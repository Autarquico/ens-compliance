---
codename: ens-compliance
title: UI de riesgos — registro, plan de tratamiento y aprobación anual
type: task
status: active
priority: P1
size: M
issue: 99
prs: []
related_issues: [99]
depends_on: [web-ui-flujo, ui-inventario]
supersedes: []
related_specs: [web-ui-flujo, ui-documentos, ui-inventario]
created: 2026-11-19
updated: 2026-11-19
---

# UI de riesgos — registro, plan de tratamiento y aprobación anual

## Contexto / problema

`ens-evidence risk` tiene nueve órdenes (`init`, `adopt-methodology`, `add`, `remove`,
`add-action`, `remove-action`, `validate`, `approve`, `status`; `cli.py:1975-2200`). En la
web, los riesgos viven solo en tres pasos del asistente: metodología, registro y aprobación
(`ui/wizard/risk.py:443-452`). Sirve para la primera pasada; no para operar el análisis
después: no hay ninguna pantalla en la navegación que responda «¿qué riesgos tengo, qué
acciones los tratan y en qué estado están, qué dice la validación, hasta cuándo vale la
aprobación anual y por qué ha dejado de valer?». Quitar un riesgo o una acción se hace hoy
con un botón sin confirmación (`templates/wizard/riesgos_registro.html:38,46`).

Sexto tramo de la UI de operación. Mismo patrón que `/inventario` frente al paso «activos»:
una página de operación que reutiliza las APIs de producto y la lógica del asistente.

Restricciones que no cambian:
- **La herramienta no propone valores de riesgo.** Probabilidad, impacto, residual,
  tratamiento y responsable los decide la organización; el producto solo valida
  (`cli.py:2060`, `risk/edit.py:128-138`). El riesgo inherente se lee de la matriz de la
  propia metodología (`Methodology.inherent`, `risk/models.py:89`), no lo estima la UI.
- Añadir un riesgo se valida contra metodología, inventario y catálogo
  (`risk/edit.py:141-155`, `risk/validate.py:61`); añadir una acción, contra el registro
  (`edit.py:177-191`). La UI no reimplementa nada: ofrece solo valores válidos y deja que
  el producto rechace.
- Quitar un riesgo que alguna acción cita se rechaza (`risk/edit.py:158-175`).
- La aprobación anual la firma `security_officer` (`risk/approval.py:62-65`; ADR 0022);
  `by` es declarado, no autenticado, y no se prerrellena (ADR 0022, «Campo `by`»).
- Validar que el análisis es coherente no dice que los valores sean acertados
  (`cli.py:2162`); la revisión humana sigue siendo obligatoria.

## Scope

**Entra:**
- **Página `/riesgos`** con cuatro bloques:
  - **Aprobación anual**: estado (`RiskState`, `risk/approval.py:29-35`): sin aprobar ·
    aprobada y vigente hasta <fecha> · obsoleta (con los motivos de `_stale_reasons`,
    `approval.py:101-116`, en llano) · caducada el <fecha> · registro inválido. Con
    aprobación: quién (nombre declarado), rol y cuándo (`RiskApproval`,
    `risk/models.py:179-207`). Sin aprobación vigente nunca se presenta como favorable.
  - **Validación**: el resultado de `validate_risk` ahora, con cada error en llano
    (`plain_errors`, `ui/wizard/risk.py:74-80`) y la fuente de cada uno (fichero de `risk/`,
    inventario, catálogo o alcance). Si el alcance no está aprobado se dice y se enlaza al
    paso del asistente. Sin errores: «coherente: N riesgos, M acciones», con la advertencia
    literal de que la herramienta no evalúa si los valores son acertados.
  - **Metodología**: nombre, referencia, adaptación y si está adoptada; escalas y matriz tal
    como están en `risk/methodology.yaml` (solo lectura). Sin adoptar, un formulario para
    editores con los tres campos (`adopt_methodology`, `risk/edit.py:97-111`), que crea las
    plantillas de `risk/` si faltan (`init_risk`, `risk/init.py:25`): no hay botón «init»
    aparte.
  - **Registro y plan de tratamiento**: una fila por riesgo (id, amenaza, activos y
    servicios, dimensiones y valoración, probabilidad, impacto, inherente, salvaguardas,
    residual, tratamiento, responsable, aceptación si la hay) con sus acciones debajo (id,
    descripción, responsable, fecha límite, estado). Las acciones que citan un riesgo que
    ya no existe se listan aparte, marcadas, con el error de validación que las acompaña.
- **Añadir un riesgo** (`POST /riesgos/anadir`, editores): el formulario del paso «Riesgos»
  (`_planned_risk`, `ui/wizard/risk.py:252-301`), con sus listas cerradas: activos y
  servicios incluidos en el alcance, dimensiones, niveles, valores de las escalas de la
  metodología adoptada, tratamientos y medidas del catálogo. Si queda riesgo residual por
  encima del mínimo o el tratamiento es aceptar, exige quién lo acepta y con qué rol
  (`_acceptance`, `risk.py:311-322`). Sin metodología adoptada, el formulario no se ofrece
  y se explica.
- **Añadir una acción** (`POST /riesgos/acciones/anadir`, editores): id, riesgo que trata
  (solo los del registro), descripción, responsable, fecha límite, estado
  (`_add_action`, `risk.py:334-367`).
- **Quitar un riesgo** (`POST /riesgos/{riesgo}/quitar`) y **quitar una acción**
  (`POST /riesgos/acciones/{accion}/quitar`), editores, **siempre confirmado**: hay que
  escribir el identificador para confirmar, como la baja de un activo
  (`ui/views/inventory.py:990-996`). Un riesgo con acciones que lo citan no ofrece el
  formulario de quitar y dice qué acciones hay que quitar antes; si el POST llega igual,
  el producto lo rechaza y no se escribe nada.
- **Aprobar el análisis** (`POST /riesgos/aprobar`, rol `security_officer`): campo «Quién
  lo aprueba» vacío, validado como nombre declarado (`declared_name`,
  `ui/wizard/base.py:100-106`). Antes de ofrecer el botón, la página muestra lo que
  `approve_risk` va a exigir y que ahora falta: alcance aprobado, categorización vigente,
  validación sin errores, `risk/methodology.md` documentado (no la plantilla)
  (`risk/approval.py:68-83`). A quien no tiene el rol se le dice qué rol falta y no se le
  ofrece el botón.
- **Escritura**: auditoría de cada escritura y de cada rechazo, con el rol usado al aprobar;
  303 a `/riesgos` al terminar. `risk/` ya está en la lista blanca (`ui/store.py:10-24`):
  no se amplía. Los errores del producto se muestran en llano (`plain_rejection`,
  `ui/wizard/risk.py:83-90`), sin nombres de fichero de cabecera ni órdenes de CLI.
- Enlace «Riesgos» en la navegación principal (`templates/base.html:22`) y desde la fase
  «Riesgos» de la página de estado (`ui/views/status.py:94`).

**No entra:**
- Editar un riesgo o una acción ya registrados. El producto no tiene API de edición
  (`risk/edit.py` solo añade y quita); se quita y se vuelve a añadir. Añadir `edit` al
  producto y su orden CLI es un tramo aparte.
- Editar las escalas o la matriz de la metodología desde la web (se editan en
  `risk/methodology.yaml`; la API de producto solo fija nombre, referencia y adaptación).
- Editar `risk/methodology.md`. La web dice si sigue siendo la plantilla y remite al fichero.
- Proponer, sugerir o calcular valores de riesgo distintos de la lectura de la matriz.
- Revocar o borrar la aprobación anual.

## Criterios de aceptación

### Scenario: registro, plan y vigencia a la vista
- **Given** una metodología adoptada, dos riesgos con una acción cada uno y una aprobación de hace diez meses sin cambios desde entonces
- **When** se abre `/riesgos`
- **Then** la aprobación aparece vigente con quién, rol, fecha y «vigente hasta» dentro de dos meses; la validación dice «coherente: 2 riesgos, 2 acciones» con la advertencia de que no se evalúan los valores; cada riesgo muestra sus campos y su acción con estado

### Scenario: aprobación obsoleta por un cambio en el registro
- **Given** una aprobación vigente y un riesgo añadido después
- **When** se abre `/riesgos`
- **Then** la aprobación aparece como obsoleta con el motivo «risk/ cambió desde que se aprobó» en llano, no como vigente, y se ofrece volver a aprobar a quien tiene `security_officer`

### Scenario: añadir un riesgo con la validación del producto
- **Given** una metodología adoptada y un editor
- **When** envía un riesgo que mitiga sin ninguna medida, o que cita un activo que no está en el inventario
- **Then** el formulario se vuelve a mostrar con el motivo del producto en llano, sin escribir nada; con una medida del catálogo y un activo del inventario, el riesgo queda en `risk/register.yaml` con el inherente leído de la matriz y la escritura auditada

### Scenario: quitar un riesgo con acciones
- **Given** un riesgo `R-01` con una acción `A-01` que lo cita
- **When** un editor intenta quitar `R-01`
- **Then** la fila no ofrece quitarlo y dice que `A-01` lo cita; un POST directo a `/riesgos/R-01/quitar` con el id confirmado se rechaza con ese motivo y no se escribe nada; tras quitar `A-01` (confirmando su id), quitar `R-01` funciona y ambas bajas quedan auditadas

### Scenario: aprobar sin lo que el producto exige
- **Given** un usuario con `security_officer`, alcance aprobado y categorización no vigente
- **When** abre `/riesgos`
- **Then** el bloque de aprobación dice que la categorización no está vigente, con enlace al paso del asistente, y no ofrece el botón; un POST directo a `/riesgos/aprobar` devuelve el mismo motivo y no escribe nada

### Scenario: solo lectura
- **Given** un usuario sin permiso de edición
- **When** abre `/riesgos`
- **Then** ve aprobación, validación, metodología, registro y plan sin ningún formulario; un POST a cualquier ruta de `/riesgos` recibe 403 auditado y no se escribe nada

### Scenario: entrada hostil
- **Given** un riesgo cuya amenaza contiene `<script>` y un POST de quitar con id `../scope/system`
- **When** se abre `/riesgos` y se envía ese POST
- **Then** la amenaza se muestra escapada; el POST da 404 o se rechaza como id desconocido, sin escribir nada; un valor con forma de token en cualquier campo de añadir se rechaza por el producto (`_reject_secrets`) sin repetir el valor en pantalla ni en la auditoría

## Notas técnicas / riesgos

- Funciones de producto que se reutilizan (verificado):
  - estado y aprobación: `risk_status`, `approve_risk`, `RiskState`, `RiskStatus`,
    `RiskApprovalRejected` (`risk/approval.py:29-46,62,118`);
  - validación: `validate_risk`, `check_register_entries` (`risk/validate.py:34,61`);
  - edición: `adopt_methodology`, `add_risk`, `remove_risk`, `add_action`, `remove_action`,
    `RiskEditRejected` (`risk/edit.py:37,97,128,158,177,194`);
  - plantillas: `init_risk` (`risk/init.py:25`, llamado desde `adopt_methodology`,
    `edit.py:106`);
  - modelos y claves: `Methodology`, `Risk`, `TreatmentAction`, `Treatment`, `ActionStatus`,
    `RiskApproval`, `APPROVAL_KEY` (`risk/models.py:24,47,93,108,151,158,179`);
    `METHODOLOGY_KEY`, `METHODOLOGY_MD_KEY`, `REGISTER_KEY`, `TREATMENT_PLAN_KEY`,
    `RISK_FILES` (`scope/sync.py:21-25`);
  - contexto: `scope_status`, `ScopeNotApproved`, `categorization_status`
    (`ens_evidence.scope`, `ens_evidence.categorization`), `load_inventory`
    (`scope/inventory.py:52`), `load_catalog` (`ens_evidence.catalog`).
- Lógica del asistente que se extrae a un módulo compartido (sin cambiar comportamiento):
  `_risk_rows`, `_action_row`, `_planned_risk`, `_needs_acceptance`, `_acceptance`,
  `_add_action`, `plain_errors`, `plain_rejection`, etiquetas de tratamiento y estado
  (`ui/wizard/risk.py:60-90,159-204,252-367`). Los pasos `riesgos-metodologia`, `riesgos` y
  `aprobacion-riesgos` siguen existiendo y pasan a usar el mismo módulo; la confirmación
  escrita al quitar se aplica también al paso «Riesgos».
- Autorización (`ui/authz.py:107-128`):
  `("POST", "/riesgos/metodologia"): EDIT`, `("POST", "/riesgos/anadir"): EDIT`,
  `("POST", "/riesgos/acciones/anadir"): EDIT`,
  `("POST", "/riesgos/{riesgo}/{accion}"): ByParam("accion", {"quitar": EDIT})`,
  `("POST", "/riesgos/acciones/{accion_id}/{accion}"): ByParam("accion", {"quitar": EDIT})`,
  `("POST", "/riesgos/aprobar"): SO`. Mismo rol fijo que `aprobacion-riesgos`
  (`authz.py:58`) y que el producto (`risk/approval.py:87`). Los tests de la tabla
  (`tests/ui/test_authorization.py:97-125`) cubren las rutas nuevas.
- Lista blanca: todo lo escrito (`risk/methodology.yaml`, `risk/methodology.md`,
  `risk/register.yaml`, `risk/treatment-plan.yaml`, `risk/approval.yaml`) está bajo
  `risk/`, ya admitido (`ui/store.py:10-24`). Sin cambios.
- Campo `by`: no se prerrellena. El producto no comprueba que coincida con nadie declarado
  (`approve_risk` lo guarda tal cual, `approval.py:85-87`); lo que prueba quién operaba es la
  auditoría con `identity` y el rol validado (ADR 0022).
- La CLI pide `--inherent` y lo valida contra la matriz (`cli.py:2038`,
  `validate.py:61`); la UI lo lee de la matriz con `Methodology.inherent` como ya hace el
  asistente (`risk.py:298`). Es una lectura de la metodología de la organización, no una
  propuesta de la herramienta.
- `validate_risk` lanza `ScopeNotApproved` antes de mirar `risk/` (`validate.py:36`); la
  página lo captura y muestra solo ese motivo, como el paso de aprobación
  (`risk.py:401-405`).
- Sin verificar: tamaño del registro a partir del cual la página con todos los riesgos y
  acciones deja de ser cómoda; en este tramo no hay filtros ni paginación.

## Referencias

- `docs/decisions/0021-interfaz-web.md`, `docs/decisions/0022-roles-y-mfa-en-la-interfaz-web.md`
- `docs/specs/web-ui-flujo.md`, `docs/specs/ui-inventario.md`, `docs/specs/ui-documentos.md`
- `src/ens_evidence/risk/`, `src/ens_evidence/ui/wizard/risk.py`, `src/ens_evidence/cli.py:1975-2200`
