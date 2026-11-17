# Contrato de plantillas de la UI

Plantillas Jinja2 en `src/ens_evidence/ui/templates/`. Entorno obligatorio: `autoescape` activo para `.html`. El test `tests/ui/test_templates_render.py` renderiza cada plantilla con `StrictUndefined`: **falta una variable, falla**. Los ejemplos del test son el contexto de referencia.

Reglas de la salida (CSP `default-src 'self'`): sin `<script>` ni `<style>` en línea, sin atributos `style=` ni manejadores `on*`/`hx-on`. Estáticos en `/static/` (`ui.css`, `vendor/htmx-2.0.4.min.js`, `fonts/`). Las plantillas no llevan token CSRF: la defensa es `Origin` + `Sec-Fetch-Site` en los POST (plan, C6).

Los textos de usuario se pasan **sin escapar**: la plantilla escapa. No pases `Markup`.

## Contexto común (todas las páginas completas)

| Variable | Tipo | Notas |
|---|---|---|
| `product_name` | `str` | Marcador: el nombre del producto está pendiente (`base.yml`). |
| `identity_email` | `str \| None` | Email del JWT de Access. |
| `nav_active` | `"estado" \| "asistente" \| "matriz" \| "evidencias" \| "expediente" \| "incidentes" \| "preauditoria" \| "herramientas" \| "copias" \| "monitorizacion" \| "inventario" \| "conectores" \| "agentes"` | Marca la sección actual. |
| `page_title` | `str` | Título de la pestaña. |

Enlaces fijos del menú (las rutas deben existir): `/`, `/asistente`, `/matriz`, `/evidencias`, `/expediente`, `/incidentes`, `/preauditoria`, `/herramientas`. Rutas con parámetro: `/matriz/{medida}`, `/evidencias/nueva`, `/expediente/{id}`, `/expediente/{id}/dossier.md`, `/incidentes/nuevo` y `/incidentes/{id}`.

## Estados (`m.badge`)

Los valores se pasan como la cadena del enum. La plantilla pone icono, texto y código en mono (nunca solo color). Valores conocidos: `CheckStatus`, `Applicability`, `MeasureAssessment`, `Readiness`, `DossierState`, `CollectionStatus`, `ScopeState`, `CategorizationState`, `Classification` (preaudit), `DispositionState`, y `NOT_RUN`/`DONE` (`LayerStatus`). Un valor desconocido se muestra tal cual, en neutro.

## `error.html`

| Variable | Tipo | Notas |
|---|---|---|
| `status` | `int` | Copy propio para 401, 403 y 409; cualquier otro, mensaje genérico. |
| `message` | `str \| None` | Si viene, sustituye el texto por defecto. |

Más el contexto común. En 401 el backend no debe dar enlace de «entrar»: la autenticación es de Access.

## Asistente

Cada paso es una plantilla `wizard/<paso>.html` que extiende `partials/paso_solo.html` si `hx_request` es verdadero (fragmento htmx: el paso más la lista de pasos como swap fuera de banda) o `wizard/shell.html` si no. `hx_request = request.headers.get("HX-Request") == "true"`. Los formularios funcionan sin JavaScript (POST normal a `form_action`) y con htmx (`hx-post`, destino `#paso`, `innerHTML`). El backend responde al POST con el mismo paso renderizado (errores o «Guardado»), o redirige (PRG) si no es htmx.

### Contexto común del asistente

| Variable | Tipo | Notas |
|---|---|---|
| `steps` | `list[{slug:str, label:str, group:"alcance"\|"categorizacion"\|"riesgos"\|"aplicabilidad"\|"documentos", state:"done"\|"current"\|"todo"\|"optional"}]` | En orden, los pasos de abajo. |
| `step` | `str` | `slug` actual. |
| `step_number`, `step_total` | `int` | Progreso, empieza en 1. |
| `step_title` | `str` | Título del paso. |
| `form_action` | `str` | `/asistente/<slug>`. Es también la URL del POST. |
| `prev_step`, `next_step` | `str \| None` | Slugs vecinos. |
| `hx_request` | `bool` | Ver arriba. |
| `errors` | `dict[str, list[str]]` | Errores por **nombre de campo**. |
| `form_errors` | `list[str]` | Errores generales; si no está vacío, se muestra «No se ha guardado». |
| `saved` | `bool` | Muestra «Guardado» si no hay `form_errors`. |
| `values` | `dict[str, str \| list[str]]` | Eco de lo enviado (o de lo guardado) por nombre de campo. |
| `approver_default` | `str` | Prelleno de «Lo aprueba»: el email del JWT. Editable. |

Todo botón de envío lleva `name="accion"`: `guardar` (por defecto), `anadir` (activos), `aprobar`, y los de cada paso de abajo (`preparar`, `decidir`, `anadir-riesgo`, `generar`…).

### Pasos (slug → plantilla → campos del formulario y variables extra)

1. `sistema` → `wizard/sistema.html`. Campos: `name`, `description`.
2. `activos` → `wizard/activos.html`. Campo: `included_assets` (multi). Para añadir a mano: el formulario GET `?tipo=<valor>` pide la ficha del tipo; el POST `accion=anadir` envía `asset_type`, `asset_id` y `asset_<campo>` por cada campo de `new_asset.fields`.
   - `assets: list[{id:str, type:str, type_label:str, summary:str\|None, deployment:"deployed"\|"planned", included:bool}]`
   - `asset_types: list[{value:str, label:str}]`
   - `new_asset: None \| {type:str, type_label:str, fields:list[{name:str, label:str, kind:"text"\|"bool"}]}` (los campos son `REQUIRED_FIELDS[type]`).
3. `servicios` → `wizard/servicios.html`. Campos: `included_services` (multi), `service_id`, `service_name`, `service_assets` (multi).
   - `services: list[{id:str, name:str, assets:list[str], included:bool}]`
   - `asset_choices: list[{id:str, label:str\|None}]`
4. `dependencias` → `wizard/dependencias.html`. Campos: `dependency_id`, `asset`, `depends_on`, `purpose`.
   - `dependencies: list[{id:str, asset:str, depends_on:str, purpose:str\|None}]`
   - `asset_choices` como en servicios.
5. `aprobacion-alcance` → `wizard/aprobacion_alcance.html`. Campo: `approved_by`; botón `accion=aprobar`.
   - `scope_state: str` (`ScopeState`)
   - `scope_errors: list[str]` (si no está vacío, el botón «Aprobar alcance» va deshabilitado)
   - `scope_summary: {name:str, assets_count:int, services_count:int}`
   - `approval: None \| {approved_by:str, approved_at:str}` (`approved_at` ya formateado)
6. `informacion` → `wizard/informacion.html`. Campos: `info_id`, `description`.
   - `information: list[{id:str, description:str}]`
7. `valoraciones` → `wizard/valoraciones.html`. Una tarjeta por elemento. Nombres de campo, con `~` como separador (`kind` es `information` o `service`; `D` es el código de dimensión `C|I|T|A|D`):
   - `rating~<kind>~<id>~<D>~level`: `""` (pendiente), `BAJO`, `MEDIO`, `ALTO` o `not_affected`.
   - `rating~<kind>~<id>~<D>~justification`
   - `valued_by~<kind>~<id>`
   - `elements: list[{kind:"information"\|"service", id:str, label:str, role_label:str, ratings:dict[str, {level:str, justification:str}], valuation:None\|{valued_by:str, valued_at:str}}]`. `ratings` se indexa por código de dimensión; ausente = pendiente. `label` es la descripción (información) o el nombre (servicio). `role_label`: «su responsable» o equivalente en claro.
   - Las explicaciones de cada dimensión y los niveles viven en la plantilla (`_macros.html`, `DIMENSIONES` y `NIVELES`); el backend no las pasa.
   - `valued_by` se prellena con `approver_default` si no hay valoración.
8. `aprobacion-categorizacion` → `wizard/aprobacion_categorizacion.html`. Campo: `approved_by`; botón `accion=aprobar`.
   - `categorization_state: str` (`CategorizationState`)
   - `category: "BASICA"\|"MEDIA"\|"ALTA"\|None`
   - `dimension_levels: list[{code:str, level:str}]` (`level` es `BAJO|MEDIO|ALTO|not_affected`)
   - `pending: list[str]` (elementos sin valorar, como `kind:id`) e `issues: list[str]`; si cualquiera no está vacío, el botón va deshabilitado.
   - `approval: None \| {approved_by:str, approved_at:str}`

9. `riesgos-metodologia` → `wizard/riesgos_metodologia.html`. Campos: `name`, `reference`, `adaptation`.
   - `adopted: bool`
   - `likelihood`, `impact`, `risk_levels: list[{id:str, description:str}]` (las escalas de la plantilla, solo lectura)
   - `impact_ids: list[str]`, `matrix_rows: list[{likelihood:str, levels:list[str]}]` (la matriz de riesgo inherente; `levels` va en el orden de `impact_ids`)
10. `riesgos` → `wizard/riesgos_registro.html`. `accion=anadir-riesgo`: `risk_id`, `threat`, `risk_assets` (multi), `risk_services` (multi), `risk_dimensions` (multi), `valuation~<D>`, `likelihood`, `impact`, `safeguards` (multi), `residual`, `treatment`, `owner`, y si hace falta aceptación `accepted_by`, `accepted_role`. `accion=anadir-accion`: `action_id`, `action_risk`, `action_description`, `action_owner`, `action_due`, `action_status`. `accion=quitar-riesgo` y `quitar-accion`: `item_id`.
    - `adopted`, `likelihood`, `impact`, `risk_levels`, `impact_ids`, `matrix_rows` como en la metodología; `minimum_level: str`
    - `risks: list[{id, threat, targets:list[str], dimensions:list[str], likelihood, impact, inherent, residual, treatment:str, owner, safeguards:list[str], accepted_by:str\|None, actions:list[Action]}]`
    - `actions: list[Action]` con `Action = {id, description, owner, due:str\|None, status:str}`
    - `asset_choices`, `service_choices`, `safeguard_choices: list[{id:str, label:str\|None}]` (solo lo incluido en el alcance y el catálogo)
    - `levels: list[str]`, `treatments`, `roles`, `action_statuses: list[{value:str, label:str}]`
11. `aprobacion-riesgos` → `wizard/riesgos_aprobacion.html`. Campo: `approved_by`; `accion=aprobar`.
    - `risk_state: str` (`RiskState`), `stale_reasons: list[str]`, `issues: list[str]`, `issues_more: int` (los que no caben), `risks_count`, `actions_count: int`
    - `approval: None \| {approved_by:str, approved_at:str, valid_until:str}`
12. `aplicabilidad` → `wizard/aplicabilidad_medidas.html`. `accion=preparar` (sin campos) y `accion=decidir`: `measure_id`, `decision` (`applies`\|`not_applicable`\|`absent`), `justification`, `reinforcement~<n>` (uno por grupo de alternativas), `absent_asset_type`, `comp_measures`, `comp_replaces` (multi), `comp_justification`.
    - `blocked: str \| None` (por qué no se puede decidir todavía), `needs_preparing: bool`, `category: str \| None`
    - `pending`, `required`, `own: list[Measure]` (por decidir; exigidas por el anexo II ya decididas; decididas por la persona) con `Measure = {id, name, state:"undecided"\|"reinforcement"\|"unregistered"\|"review"\|"decided", reason:str\|None, required:bool, required_by:str\|None, required_reinforcements:list[{id,title}], groups:list[{options:list[{id,title}], chosen:str}], choice:""\|"applies"\|"not_applicable"\|"absent", justification:str, absent_types:list[str], absent_type:str, obligations:list[{id,title}], compensation:{measures:str, replaces:list[str], justification:str}}`
13. `aprobacion-aplicabilidad` → `wizard/aplicabilidad_aprobacion.html`. Campo: `approved_by`; `accion=aprobar`.
    - `declaration_state: str` (`DeclarationState`), `stale_reasons`, `issues: list[str]`, `issues_more: int`
    - `exclusions: list[{id, name, asset_type, justification}]` (exigidas sin aplicar por ausencia de activo; se listan antes de firmar)
    - `declaration: None \| {approved_by:str, approved_at:str, selected_count:int}`
14. `documentos-<bloque>` (siete: `organizacion`, `responsables`, `normativa`, `procedimientos`, `informacion`, `incidentes`, `autorizaciones`) → `wizard/documentos_bloque.html`. Por pregunta: `q~<id>` (marca de que se envió), `a~<id>` (texto o lista), `r~<id>~<campo>` (registros, una columna por fila), `na~<id>` + `reason~<id>` (no aplica), `del~<id>` (filas a quitar).
    - `block_intro: str`, `people: list[str]` (personas declaradas, para el desplegable)
    - `questions: list[Question]` con `Question = {id, name, text, help, type:"text"\|"list"\|"records", choices:list[str], multiline, people_list, optional:bool, clause:{reference:str, text:str}, condition_note:str\|None, allow_na:bool, na_label:str, na:bool, reason:str, value:str, rows:list[Row]}`. `Row` en listas: `{n:int, value:str, filled:bool, removed:bool}`; en registros: `{n, filled, removed, cells:list[{id,label,name,value,long:bool}]}`.
15. `documentos-generar` → `wizard/documentos_generar.html`. `accion=generar` (sin campos).
    - `scope_ok`, `answers_started`, `can_generate: bool`
    - `issue_groups: list[{slug:str, label:str, items:list[str]}]` (lo que falta, con enlace al bloque `/asistente/<slug>`), `warnings: list[str]`
    - `documents: list[Document]` con `Document = {name, title, state:str, state_label:str, tone:"ok"\|"warn"\|"neutral", reasons:list[str]}`
16. `documentos-aprobar` → `wizard/documentos_aprobar.html`. `accion=aprobar`: `documento` y `approved_by`; el rol sale del servidor, nunca del formulario. `?ver=<documento>` lee el texto.
    - `documents: list[Document + {role_label:str, signatory:str\|None, by:str, can_approve:bool, approval:None\|{approved_by,approved_at}, error_key:str}]`; el error de cada documento va en `errors[error_key]` (`approved_by~<documento>`).
    - `viewing: None \| {title:str, text:str}` (el texto va en `<pre>`, escapado)

Nombres de campo con error: las claves de `errors` son exactamente los nombres de arriba (incluida la forma `rating~...`).

## Vistas (contexto común + lo siguiente)

Las vistas con formulario (`matriz_medida`, `evidencias_nueva`, `expediente`, `preauditoria`, `incidente_nuevo`, `incidente`) reciben además `form_errors: list[str]`, `errors: dict[str, list[str]]`, `values: dict[str, str]` y `saved` (`str \| bool`): como en el asistente, pero los POST redirigen (PRG) y los errores devuelven 422 con la misma página.

### `views/estado.html`
`readiness: {state:str, summary:str}` (`Readiness`) · `scope_state: str` · `categorization_state: str` · `category: str \| None` · `phases: list[{label:str, state:"done"\|"next"\|"todo"}]` (el recorrido de alcance a pre-auditoría) · `next_step_url: str \| None` y `next_step_label: str` (botón principal; la etiqueta va siempre) · `blockers: list[{measure_id:str\|None, texts:list[str]}]` (agrupados por medida, como `assessment status`) · `generated_at: str`.

### `views/matriz.html`
`rows: list[{measure_id:str, title:str, applicability:str, assessment:str, evidence_count:int}]` · `wizard_pending: bool` (alcance o categorización sin cerrar) · `generated_at: str`. `measure_id` va en mono (ids oficiales, ej. `op.pl.1`) y enlaza a `/matriz/<medida>`.

### `views/matriz_medida.html`
Formularios: `POST /matriz/<medida>/responsable` (`owner_name`, `owner_role`, `every`), `/enlazar` (`kind`=`document`\|`evidence`, `target`, `covers` multi), `/desenlazar` (`kind`, `target`), `/revisar` (`by`, `role`, `assessment`, `conclusion`).
`measure_id`, `title: str` · `applicability`, `assessment: str` · `selected: bool` (false: no aplica, se revisa sin valoración) · `owner: None \| {name:str, role:str}` · `every_days: int \| None`, `max_days: int` · `obligations: list[{id, title, compensating:list[str], clauses:list[{unit, label, text, sources:list[str]}]}]` · `links: list[{kind, target, label, covers:list[str], counts:bool}]` · `document_offers: list[{name, covers:list[str]}]` (con las cláusulas que su ficha dice cubrir) · `evidence_offers: list[{id, asset_id, collected_at:str, expired:bool}]` · `units: list[str]` · `blockers: list[str]` · `last_review: None \| {by, role, at, valid_until, conclusion}` · `owner_roles`, `review_roles`, `assessments: list[{value, label}]` · `by_default: str` (el email, editable) · `uncovered: list[str]` (cláusulas sin cubrir de una revisión rechazada) · `generated_at: str` · `kit_offer: None \| {name, tool_name, cta, url, covers, not_covers: str, covers_clauses, partial_clauses, not_covered_clauses: list[{id:str, text:str \| None}], ram: None \| {mib, measured_on, image, conditions}}` (solo si la medida aplica y el conector de su herramienta no tiene ejecuciones; `name` es el id de la herramienta, «copias», «claves» o «contrasenas»; la ficha enseña qué cubre y qué no en prosa, las cláusulas en tres listas (cubiertas, en parte, no cubiertas), cada una con su id y el texto oficial que la ficha ya carga del catálogo (`text` es `None` si la cláusula no está en la medida: se ve solo el id) y la RAM, y enlaza a `url` con el texto `cta`).

### `views/evidencias.html`
`manifests: list[{id:str, source:str, collected_at:str, status:str}]` · `uploaded: str` (id de la evidencia recién subida; cadena vacía si no hay). Solo manifiestos: nunca el cuerpo de las evidencias.

### `views/evidencias_nueva.html`
Formulario `multipart/form-data` a `POST /evidencias/nueva`: `asset_id`, `by`, `collected_on` (AAAA-MM-DD, opcional) y `file`. El nombre del fichero no se lee. `assets: list[str]` (solo activos del alcance) · `max_megabytes: int` (el tope de la subida) · `by_default: str`.

### `views/expediente.html`
`dossier_id: str \| None` · `category: str \| None` · `dossier_state: str` (`DossierState`, `Sin generar` o `ROTO`) · `generated_at: str` · `declared: None \| {by:str, at:str}` · `sections: list[{title, file, state, state_label, generated_at, fingerprint:str\|None}]` · `transitions: list[{state, actor, role, at}]` · `stale: bool` y `drift_lines: list[str]` · `integrity`, `blocked`, `incomplete: list[str]` · `readiness_notice: str \| None` · `action: "self-assess"\|"analyze"\|"declare"\|None` y `next_text: str` · `can_download: bool` (`GET /expediente/<id>/dossier.md`) · `self_assess_roles: dict[str, str]`. Formularios: `POST /expediente/generar` (sin campos) y `POST /expediente/<id>/<accion>` (`by` más `role` / `conclusion`, `elevated_to`, `elevated`, `manual_judgment_review`, `corrective_measures` / `evidence`, `acknowledge_incomplete`).

### `views/incidentes.html`
`rows: list[{id, simulated:bool, broken:bool, readable:bool, status:str, closed:bool, severity:str, detected_at:str, title:str}]` (el último estado de cada incidente, más reciente primero; `broken`: la verificación encontró problemas) · `generated_at: str`. `id` en mono y enlaza a `/incidentes/<id>`.

### `views/incidente_nuevo.html`
Formulario `POST /incidentes/nuevo`: `title`, `detected_at` (`datetime-local`, hora UTC), `by` (vacío por defecto: un nombre, nunca un correo), `severity` (solo si hay `levels`), `assets` multi, `source_system`, `alert_ids`, `personal_data`, `summary` y `simulated` (casilla). Contexto de formulario común a esta vista y a `incidente.html`: `asset_choices: list[{id, label:str\|None}]` · `levels: list[str]` (niveles declarados; vacío si no hay) · `personal_data_options`, `action_kinds`, `review_results: list[{value, label}]` · `selected_assets: list[str]` · `simulation_note: str`. Más `form_errors`, `errors` (vacío) y `values`.

### `views/incidente.html`
Formularios: `POST /incidentes/<id>/actualizar` (`by`, `severity`, `personal_data`, `summary`, `assets` multi, `action`, `action_kind`, `action_at`, `evidence`), `/cerrar` (`by`, `conclusion`, `auditable_events_result`, `auditable_events_detail`, `legal_relevance`, `legal_advice`, `preserved_evidence`) y `/historial` (`by`; solo si falta el historial de un cierre).
`incident_id: str` · `readable: bool` (hay registros legibles; si no, solo se muestran `problems` y los reportes) · `simulated: bool` · `title`, `incident_status` (`open`\|`closed`), `status_label`, `severity`, `detected_at`, `personal_data` (`yes`\|`no`\|`unknown`) y `personal_data_label: str` · `closed: bool` · `assets`, `evidence_ids: list[str]` · `source: None \| {system, alert_ids}` · `procedure: None \| {name, approved_at}` · `summary: str \| None` · `actions: list[{at, kind, description}]` · `closure: None \| {at, conclusion, legal_relevance, legal_advice:str\|None, preserved:list[str], events_review, events_detail}` · `records: list[{seq:int, kind, at, evidence_id:str\|None}]` · `problems: list[str]` · `history_id: str \| None` y `history_missing: bool` · `can_write: bool` (abierto y sin problemas: muestra los formularios de reporte y cierre) · `saved: str \| None` · `op_exp_9`, `op_exp_7: str` (enlaces a la matriz) · `generated_at: str`. Tras cerrar, la página dice qué evidencia (`history_id`) enlazar en `/matriz/op.exp.9` y `/matriz/op.exp.7`.

### `views/herramientas.html`
`tools: list[...]`, una tarjeta por herramienta del registro (`ens_evidence/kit/registry.py`) y en su orden. Cada elemento: `id` (ancla `h-<id>`), `name`, `purpose: str` · `state: {key:"ok"\|"sealed"\|"problems"\|"down"\|"nodata", tone, label:str, checked_at:str}` (solo de la última ejecución de checks del conector de la herramienta; `sealed` solo en claves; `checked_at` es `—` sin datos) · `measures: list[str]` (enlazadas a `/matriz/<medida>`), `measures_note: str` · `hints: dict[str, str]` (frase por estado; con un estado presente en `hints` la tarjeta la muestra y enlaza a `help_url` con el texto `help_label`) · `open_url: str \| None` (`https://<host>` de `kit_hosts[id]`; con él la tarjeta ofrece «Abrir» y «Preparar otro paquete»; sin él, un botón con `cta` hacia `wizard_url` y la nota «Aún no está desplegado en esta caja») · `wizard_url`, `cta`, `help_url`, `help_label: str` · `status_url: str \| None` (con él, enlace «Ver el estado de las copias») · `ram: None \| {mib:int, measured_on:str, image:str, conditions:str}` (se pinta «≈N MiB (medido el <fecha>, <condiciones>)»; `None`: «RAM sin medir»).

### `views/herramientas_claves.html`
Formulario `POST /herramientas/claves`: `asset_id` (activo `application` del inventario), `key_shares` (N, 3..10), `key_threshold` (M, 2..N), `max_key_age_days`. Contexto: `application_assets: list[str]` · `enable_command`, `init_command: str` · `values`, `errors`, `form_errors`. El aviso de irrecuperabilidad (N−M) va antes del formulario y del botón. Éxito: el paquete `tar.gz` (`Cache-Control: no-store`, sin secretos) sin escribir en el workspace; el log de auditoría solo anota el hecho. Error: 422 con la misma página. Las partes de desbloqueo nunca se piden en la web.

### `views/herramientas_claves_custodios.html`
`GET`/`POST /herramientas/claves/custodios`: el registro de custodios como revisión humana. Campos: `asset_id` (activo `application` del inventario; se propone el de la última evidencia del conector `keystore`), `key_shares` (N), `key_threshold` (M), hasta 10 filas `custodian_name_<i>` y `custodian_since_<i>` (AAAA-MM-DD; las filas vacías se ignoran), `reviewer`, `role`, `reviewed_on` y `valid_until_on` (AAAA-MM-DD). Todo llega vacío: `reviewer` y los nombres nunca se prellenan con el correo de Access, y un nombre rechazado (correo, DNI, secreto) no se devuelve en el formulario. Contexto: `application_assets: list[str]` · `rows: list[int]` (0..9) · `review_roles: list[{value, label}]` · `current: None \| {custodians:list[{name, since}], key_shares, key_threshold, reviewer, role, reviewed_at, valid_until}` (el registro vigente) · `unreadable_count: int` · `mismatches: list[str]` (avisos si N o M declarados no casan con el último `seal-status` de OpenBao; no bloquean) · `saved: bool` (`?guardado=1`) · `values`, `errors`, `form_errors`. Éxito: guarda `evidence/key-custodians/<id>.yaml` sin sobrescribir y lo registra como evidencia manual del activo elegido (`submitted_by` = el revisor); redirige 303. Errores: 422 con la misma página (409 si el identificador ya existe). La herramienta no guarda las partes de desbloqueo, ni su número ni su huella.

### `views/herramientas_contrasenas.html`
Formulario `POST /herramientas/contrasenas`: `asset_id` (activo `application` del inventario), `inactive_days` (entero > 0, el plazo de inactividad de la política de la organización) y `host` (dominio del gestor; se valida pero NO entra en el paquete: se escribe en `deploy/ui.env` como `ENS_CONTRASENAS_HOST`). Contexto: `application_assets: list[str]` · `enable_command`, `host_setting`, `setup_command`, `close_command: str` · `values`, `errors`, `form_errors`. Todo llega vacío. La página dice que la herramienta mira el segundo factor y la inactividad de las cuentas del propio gestor, que detectar la inactividad no suspende la cuenta, y que no mide la rotación ni la complejidad de las credenciales de los sistemas. Éxito: el paquete `tar.gz` (`Cache-Control: no-store`, sin secretos) sin escribir en el workspace; el log de auditoría solo anota el hecho. Error: 422 con la misma página.

### `views/herramientas_copias.html`
Formulario `POST /herramientas/copias`: `variant` (`caja`\|`servidor`), `id`, `asset_id`, `instance` (solo `servidor`), `endpoint`, `bucket`, `prefix`, `lock_days`, `volumes` multi, `host_paths` (una ruta por línea), `cron`, `retention_days`, `max_backup_age_hours`, `max_check_age_days`, `restore_test_validity_days`, `min_check_read_data_percent` y las claves `write_access_key_id`, `write_secret_access_key`, `read_access_key_id`, `read_secret_access_key` (campos `password`, nunca se devuelven al formulario). Éxito: la respuesta es el paquete `tar.gz` (`Content-Disposition: attachment`, `Cache-Control: no-store`), sin escribir en el workspace; el log de auditoría solo anota el hecho. Error: 422 con la misma página.
`values: dict[str, str \| list[str]]` (sin claves; `volumes` es lista) · `errors`, `form_errors` · `backup_assets: list[str]` (activos `backup` del inventario) · `volumes`, `variants: list[{value, label}]` · `default_read_percent: int` · `enable_command: str`.

### `views/copias.html`
`GET /copias` (solo lectura, sin formularios, `nav_active="copias"`). Todo cuelga de `page: CopiasPage`:
`configured: bool` (existe `connectors/backups.yaml`) · `config_error: str \| None` (mensaje fijo, nunca el texto de la excepción: «no es válido» / «no se puede leer»; con él o sin `configured` solo se pinta «No hay copias configuradas» más el bloque de la caja y el historial, sin tono favorable) · `backrest_href: str \| None` (`https://<ENS_COPIAS_HOST>`, nunca sale de la petición; `None`: «Backrest no está desplegado») · `runbook_href: str` (`/ayuda/kit-copias`) · `wizard_href: str` (`/herramientas/copias`) · `history_notes: tuple[str]` · `generated_at: str`.
`repositories: tuple[RepositoryBlock]`, cada uno: `id`, `asset_id: str` · `asset_href: str \| None` y `asset_note: str` (si no está en el inventario) · `frequency: str \| None` (texto libre del inventario) · `thresholds: {max_backup_age_hours, restore_test_validity_days, max_check_age_days: int}` (umbrales de la comprobación, no la política) · `last_snapshot: None \| {at: str, age_hours: int}` (solo si sale del artefacto de evidencia) y `checked_at: str \| None` (fecha del resultado BACKUP-001; nunca se presenta como fecha de copia) · `evidence: EvidenceSummary \| None` (`status`, `label`, `tone`, `favorable`, `coverage`, `partial_reasons`, `sentence`) · `results: tuple[ConnectorResults]` (`connector`, `finished_at`, `complete`, `rows: [{check_id, status, collection_status, reason_code, evidence_ids}]`) y `check_labels: dict[str, str]` · `problem: bool` (algún FAIL/ERROR: cabecera «Con problemas» y acciones Backrest/procedimiento) · `restore_test: None \| {test_id, performed_at, reviewer, role_label, valid_until, approved: bool, expired: bool}` (datos declarados; `expired` = `valid_until <= now` solo produce el aviso desfavorable «Sin prueba vigente… caducó»; nunca se deriva «vigente»: el veredicto es la fila BACKUP-005) · `tests_unattributable: bool` (sin `restore_test` pero con pruebas registradas o ilegibles que no se pueden atribuir a este repositorio, p. ej. sin resultados de checks: se dice «no se pueden atribuir», distinto de «ninguna prueba consta»; nunca favorable) · `guide: {command: str, yaml: str, steps: tuple[str]}` (se pinta en `<pre>` escapado; `steps` pasa por `code_spans`).
`box: {last: None \| {run_id, status, status_label, started_at, finished_at: str\|None, snapshot_id: str\|None}, interrupted: tuple[{run_id, started_at}], unreadable_count: int, note: str}` (nunca el texto de error de una ejecución).
`history: tuple[{test_id, repository_label (code_spans), performed_at, matched: "sí"\|"no"\|"sin verificar", reviewer, role_label, valid_until}]` y `unreadable_tests: int`.
La ausencia de resultados, de snapshot o de prueba se dice con icono y texto y nunca con tono favorable; la copia no usa «certifica», «garantiza el cumplimiento» ni «cumple el ENS».

### `views/conectores.html`
`GET /conectores` y la respuesta 422 de `POST /conectores/{conector}/recoger` (`nav_active="conectores"`). `page: ConectoresPage` · `saved: str | None` (`?hecho=`) · `saved_text: str | None` (texto fijo) · `requested_text: str | None` (`?pedida=<conector>` del catálogo: aviso fijo de petición creada) · `form_errors: list[str]` (arriba, `role="alert"`; textos fijos de `RequestRejected`) · `permissions`. Lo único que escribe la página es el formulario «Recoger ahora» de cada bloque: deja una petición de recogida, no recoge nada.
`ConectoresPage`: `blocks: tuple[ConnectorBlock]` · `manifest_notes`, `inventory_notes: tuple[str]` · `generated_at: str` · `registry_unreadable: bool` (aviso fijo: se muestra el catálogo sin el registro de conectores). Tras el lead, la plantilla pinta un resumen de una fila por conector (nombre con ancla `#h-conector-N`, configuración, última recogida, fallo) sin cifras agregadas.
`ConnectorBlock`: `name`, `label`, `href` · `editable`, `can_configure: bool` (el botón «Configurar» solo sale con `can_configure`) · `config: ConfigState` · `credential: CredentialInfo | None` (`None`: conector sin ficha, «Sin ficha en esta versión») · `doc_href: str | None` (`/ayuda/conector-<name>`), `kit_href`, `status_href: str | None` · `last: LastCollection | None` (`None`: «Sin recogidas todavía», neutro) · `evidence: ConnectorEvidence | None` (`EvidenceSummary` más `stale: bool` y `last_outcome: "failed"\|"partial"\|"ok"\|"unreadable"\|None`; con `stale` la plantilla avisa antes del badge de que los resultados son anteriores a la última recogida), `results: tuple[ConnectorResults]`, `history_notes: tuple[str]` · `repositories: tuple[RepositoryState]` (solo `backups`: bloque de solo lectura con enlaces a `/copias` y `/herramientas/copias`) · `request: RequestView | None` (la última petición de recogida del conector; `None`: ninguna) · `can_collect: bool` (`can_edit` y configuración `configurado` y ninguna petición viva; solo entonces se pinta el botón «Recoger ahora») · `collect_action: str` (`/conectores/<name>/recoger`).
`ConfigState`: `status` (`sin-configurar` \| `configurado` \| `invalida` \| `ilegible`), `label`, `problem: str | None` (mensaje fijo) · `asset_id`, `asset_href`, `asset_note: str | None` · `shown: tuple[(etiqueta, valor)]` (solo campos del catálogo).
`CredentialInfo`: `kind` (`env` \| `env_prefix` \| `settings` \| `none`), `names: tuple[str]`, `where: str`. El párrafo depende de `kind` y añade «La web no puede saber si la credencial está puesta: míralo en la última recogida.» (salvo en `none`).
`LastCollection`: `evidence_id`, `collected_at: str` · `status: str | None` (`CollectionStatus`; `None` si no se pudo verificar), `status_label` · `unreadable`, `partial: bool` (con `partial` sale el distintivo «Recogida parcial») · `failure: Failure | None` (`category`, `label` fija, `detail: str | None` fija) · `sections: tuple[SectionRow(name, status)]`, `sections_unreadable: bool`.
`RepositoryState`: `id`, `status: str | None`, `status_label`, `failure: Failure | None`.
`RequestView` (`views/conectores_peticion.py`): `state` (`PENDING` \| `RUNNING` \| `DONE` \| `EXPIRED`), `label` y `text` fijos, `tone` (`warn` pendiente y en curso, `neutral` caducada, `bad` «No se pudo hacer: <causa>» y «Terminada con error de recogida», `warn` «Terminada; comprobaciones no registradas», `neutral` «Terminada»; nunca `ok`: lo favorable no lo dice una petición) · `requested_at: str | None` · `alive: bool` · `result: ResultView | None` (`collection_label`, `phase_label`, `checks_label: str` fijos; `evidence_id: str | None` solo con formato canónico; `evidence_invalid: bool`: había un identificador no canónico, se dice «identificador no válido» y no se muestra). Pendiente: «Se recogerá en cuanto el servicio esté libre; normalmente en menos de un minuto.» Caducada: remite al runbook y el botón vuelve a salir. La tarjeta es la parcial `partials/conector_peticion.html` (también en la ficha).
Todo estado lleva icono, texto y código. Nada de `data.error` ni de texto de excepciones llega a la plantilla; la copia no usa «certifica», «garantiza el cumplimiento» ni «cumple el ENS».

### `views/conectores_form.html`
`GET /conectores/{conector}` y la respuesta 422 de `POST /conectores/{conector}` (`nav_active="conectores"`). Contexto: `block: ConnectorBlock` (cabecera y estado) · `form: ConnectorForm | None` (`None` en `backups` y en conectores sin ficha: solo lectura) · `values: dict[str, str]` (sin los campos marcados como secreto) · `errors: dict[str, list[str]]` por nombre de campo · `form_errors: list[str]` · `permissions`. La sección «Estado» incluye la tarjeta de la petición de recogida (`block.request`, `block.can_collect`).
`ConnectorForm`: `name`, `label`, `action`, `cancel_href` · `fields: tuple[FormField(name, label, kind: text|int|choice|lines|asset, required, hint, choices: tuple[Choice(value,label)])]` (el select de activo usa `choices`) · `asset_choices`, `asset_type_label` · `preserved: tuple[str]` (claves que se conservan) · `needs_confirmation: bool` (casilla `confirmar_sobrescritura`) · `writes_blocked: bool`, `blocked_notice: str | None` (formulario inactivo con el aviso) · `credential: CredentialInfo` · `doc_href`.
Solo se pinta el formulario con `block.can_configure`; el lector no ve formulario ni botones.

### `views/preauditoria.html`
Formularios: `POST /preauditoria/preparar` (`dossier`, `by`), `/enviar` (`dossier`, `reviewed`, `confirm_sha256`, `by`, `accept_shell_risk` si el destino lo pide) y `/hallazgos/<id>` (`dossier`, `decision`, `evidence`, `reason`, `by`). La UI solo deja peticiones; las ejecuta el servicio `app`.
`dossiers: list[str]` y `dossier_id: str \| None` · `requests: list[{kind_label, state, state_label, tone, at, by:str\|None, message:str\|None}]` · `has_live_request`, `can_consent: bool` · `prepared: None \| {key, provider, model, destination, recipient:str\|None, shell:bool, pack_version, prepared_at, sha256, intact:bool, measures, documents, evidence:int}` · `redaction_limit`, `shell_risk: str` · `generated_at: str` · `layers: dict[str, str]` (nombre visible de capa → `LayerStatus`) · `judgment: list[Finding]` (primero) · `deterministic: list[{measure_id, findings:list[Finding]}]` · `error_finding: str \| None` (hallazgo cuyo formulario falló). `Finding = {id, measure_id, classification, disposition, text, criterion, source, classification_source:str\|None, layer_label, decidable:bool, closed:bool, decisions:list[(value,label)], evidence_options:list[{id,label}]}`. La vista siempre muestra «Revisión humana obligatoria» y presenta los hallazgos como propuestas.

Las fechas llegan ya formateadas como `str`.

## Ficheros estáticos y su procedencia

| Fichero | Origen | sha256 |
|---|---|---|
| `static/vendor/htmx-2.0.4.min.js` | htmx.org 2.0.4, `dist/htmx.min.js` del tarball npm (sha1 del tarball `74fce66b177eb59c6d251ecf1052a2478743bec9`, coincide con el registro) | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `static/fonts/geist-latin-wght-normal.woff2` | `@fontsource-variable/geist` 5.3.0 | `19f9c92546aa300c312235e3125af1b81394d8db9a4bc4a425cd5b641d2d54e1` |
| `static/fonts/geist-mono-latin-wght-normal.woff2` | `@fontsource-variable/geist-mono` 5.3.0 | `684ad5b531f81d43c1e8c7038262d5db7cdc1f68006e04d6c7769efa8d33c8cc` |
| `static/fonts/instrument-serif-latin-400-normal.woff2` | `@fontsource/instrument-serif` 5.3.0 | `5eb09b5ac0e28b67c2f041c8ba6d244604ca0c0980d65912ab2d47fed84ddc31` |
| `static/fonts/instrument-serif-latin-400-italic.woff2` | `@fontsource/instrument-serif` 5.3.0 | `5a51946dfffa82972bc98745359c46761515641fda557c25116459a9f83da4a7` |

Fuentes con licencia SIL OFL 1.1 (`static/fonts/OFL-instrument-serif.txt`; Geist, también OFL, de Vercel). Solo subconjunto latino: cubre el español. Iconos: seis trazados de Tabler (MIT) en línea en `_macros.html`, con `stroke-width` 1.5. Configuración de htmx: `<meta name="htmx-config">` en `base.html` con `allowEval:false`, `selfRequestsOnly:true`, `allowScriptTags:false`.

El servidor debe servir `/static/` con el tipo MIME correcto (`font/woff2`, `text/javascript`, `text/css`) y `X-Content-Type-Options: nosniff`.
