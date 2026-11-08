# Contrato de plantillas de la UI

Plantillas Jinja2 en `src/ens_evidence/ui/templates/`. Entorno obligatorio: `autoescape` activo para `.html`. El test `tests/ui/test_templates_render.py` renderiza cada plantilla con `StrictUndefined`: **falta una variable, falla**. Los ejemplos del test son el contexto de referencia.

Reglas de la salida (CSP `default-src 'self'`): sin `<script>` ni `<style>` en línea, sin atributos `style=` ni manejadores `on*`/`hx-on`. Estáticos en `/static/` (`ui.css`, `vendor/htmx-2.0.4.min.js`, `fonts/`). Las plantillas no llevan token CSRF: la defensa es `Origin` + `Sec-Fetch-Site` en los POST (plan, C6).

Los textos de usuario se pasan **sin escapar**: la plantilla escapa. No pases `Markup`.

## Contexto común (todas las páginas completas)

| Variable | Tipo | Notas |
|---|---|---|
| `product_name` | `str` | Marcador: el nombre del producto está pendiente (`base.yml`). |
| `identity_email` | `str \| None` | Email del JWT de Access. |
| `nav_active` | `"estado" \| "asistente" \| "matriz" \| "evidencias" \| "expediente" \| "preauditoria"` | Marca la sección actual. |
| `page_title` | `str` | Título de la pestaña. |

Enlaces fijos del menú (las rutas deben existir): `/`, `/asistente`, `/matriz`, `/evidencias`, `/expediente`, `/preauditoria`.

## Estados (`m.badge`)

Los valores se pasan como la cadena del enum. La plantilla pone icono, texto y código en mono (nunca solo color). Valores conocidos: `CheckStatus`, `Applicability`, `MeasureAssessment`, `Readiness`, `DossierState`, `CollectionStatus`, `ScopeState`, `CategorizationState`, `Classification` (preaudit), `DispositionState`, y `NOT_RUN`/`DONE` (`LayerStatus`). Un valor desconocido se muestra tal cual, en neutro.

## `error.html`

| Variable | Tipo | Notas |
|---|---|---|
| `status_code` | `int` | Copy propio para 401, 403 y 409; cualquier otro, mensaje genérico. |
| `message` | `str \| None` | Si viene, sustituye el texto por defecto. |

Más el contexto común. En 401 el backend no debe dar enlace de «entrar»: la autenticación es de Access.

## Asistente

Cada paso es una plantilla `wizard/<paso>.html` que extiende `partials/paso_solo.html` si `hx_request` es verdadero (fragmento htmx: el paso más la lista de pasos como swap fuera de banda) o `wizard/shell.html` si no. `hx_request = request.headers.get("HX-Request") == "true"`. Los formularios funcionan sin JavaScript (POST normal a `form_action`) y con htmx (`hx-post`, destino `#paso`, `innerHTML`). El backend responde al POST con el mismo paso renderizado (errores o «Guardado»), o redirige (PRG) si no es htmx.

### Contexto común del asistente

| Variable | Tipo | Notas |
|---|---|---|
| `steps` | `list[{slug:str, label:str, group:"alcance"\|"categorizacion", state:"done"\|"current"\|"todo"}]` | En orden, los 8 pasos de abajo. |
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

Todo botón de envío lleva `name="accion"`: `guardar` (por defecto), `anadir` (activos), `aprobar`.

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

Nombres de campo con error: las claves de `errors` son exactamente los nombres de arriba (incluida la forma `rating~...`).

## Vistas de solo lectura (contexto común + lo siguiente)

### `views/estado.html`
`readiness: {state:str, summary:str}` (`Readiness`) · `scope_state: str` · `categorization_state: str` · `category: str \| None` · `next_step_url: str \| None` y `next_step_label: str` (botón principal; ambos o ninguno) · `blockers: list[{kind:str, measure_id:str\|None, text:str}]` (`BlockerKind`) · `generated_at: str`.

### `views/matriz.html`
`rows: list[{measure_id:str, title:str, applicability:str, assessment:str, check_status:str\|None, evidence_count:int}]` · `generated_at: str`. `measure_id` va en mono (ids oficiales, ej. `op.pl.1`).

### `views/evidencias.html`
`manifests: list[{id:str, source:str, collected_at:str, status:str, items_count:int}]` (`status` = `CollectionStatus`). Solo manifiestos: nunca el cuerpo de las evidencias.

### `views/expediente.html`
`dossier_state: str` (`DossierState`) · `generated_at: str` · `declared: None \| {by:str, at:str}` · `sections: list[{title:str, file:str, state:str, generated_at:str, fingerprint:str\|None}]`.

### `views/preauditoria.html`
`generated_at: str` · `layer_status: dict[str, str]` (nombre visible de capa → `LayerStatus`) · `findings: list[{id:str, measure_id:str, classification:str, disposition:str, text:str, criterion:str\|None, layer_label:str}]`. La vista siempre muestra «Revisión humana obligatoria» y presenta los hallazgos como propuestas.

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
