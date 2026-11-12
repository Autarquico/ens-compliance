---
codename: ens-compliance
title: "UI web: el flujo completo, de los riesgos a la pre-auditoría"
type: task
status: active
priority: P1
size: XL
issue: 63
prs: [64]
related_issues: [63]
depends_on: [web-ui-base]
supersedes: []
related_specs: [web-ui, web-ui-base]
created: 2026-11-08
updated: 2026-11-09
---

# UI web: el flujo completo, de los riesgos a la pre-auditoría

## Contexto / problema

`web-ui-base` (#62) solo cubre el alcance y la categorización; el resto del flujo exige la CLI y editar YAML a mano. La demo del 2026-11-08 (clínica ficticia) lo confirmó: para el usuario objetivo, la web no sirve todavía. El usuario lo pide sin rodeos: «haz el flujo completo, esta app tal como está es inusable».

Tramos 2 y 3 de `web-ui` (#29) juntos. La UI y la CLI escriben por las mismas APIs de producto; donde hoy se edita YAML a mano se añade la API de edición y su comando CLI, como en `web-ui-base`.

## Scope

**Entra:**
- **Riesgos:** adoptar la metodología (nombre, referencia, adaptación; escalas de la plantilla), añadir y quitar riesgos y acciones con formularios que solo ofrecen valores válidos (activos, servicios, escalas, medidas del catálogo), validar y aprobar.
- **Aplicabilidad:** proponer, decidir cada medida pendiente (aplica / no aplica con justificación propia, refuerzo elegido, compensación o ausencia de activo), confirmar y firmar la Declaración.
- **Documentos:** el cuestionario de `docs questions` como formulario por bloques, con la cláusula que motiva cada pregunta; generar; aprobar cada documento con el rol que le toca.
- **Matriz:** por medida, responsable y periodicidad; subir una evidencia manual; enlazar documentos y evidencias a sus cláusulas (la UI propone las cláusulas que cubre cada documento); revisar con valoración y conclusión.
- **Expediente:** generar, autoevaluar, analizar, declarar (BÁSICA) y descargar el expediente en Markdown.
- **Pre-auditoría:** preparar, revisar el resumen de lo que se enviaría y su huella, consentir, y decidir cada hallazgo. El envío lo ejecuta el servicio `app` (el `ui` no tiene salida a Internet): la UI deja una petición con la huella consentida y el scheduler de `app` la ejecuta.
- **Guía:** la página de estado dice en cada momento cuál es el siguiente paso y enlaza a él.
- **Fallos destapados por la demo:** cláusulas `*.intro` que `matrix link` rechaza; recuento de bloqueos distinto entre UI y CLI; «Medida desfavorable» para lo pendiente de revisión; los hallazgos de juicio enterrados bajo los deterministas.

**No entra:**
- OIDC, roles y panel de agentes (tramo 4).
- Edición del catálogo, de los conectores o de los ajustes.

## Criterios de aceptación

### Scenario: el flujo completo por la web
- **Given** un workspace vacío y un usuario autenticado por Access
- **When** recorre la web desde el alcance hasta la declaración, sin tocar la CLI ni ficheros
- **Then** obtiene alcance, categorización, riesgos, Declaración, documentos, matriz revisada y expediente declarado

### Scenario: mismos artefactos que la CLI
- **Given** el mismo guion y el mismo reloj
- **When** se completa por la web y por la CLI en dos workspaces
- **Then** los ficheros del workspace y sus huellas coinciden

### Scenario: siempre hay un siguiente paso
- **Given** cualquier estado intermedio del flujo
- **When** se abre la página de estado
- **Then** muestra el siguiente paso pendiente y un enlace que lleva a él

### Scenario: la web no da por buena una medida sin pruebas
- **Given** una medida con alguna cláusula sin fuente
- **When** se intenta revisar como conforme desde la web
- **Then** se rechaza, se dice qué cláusulas faltan y no se escribe nada

### Scenario: la pre-auditoría no sale sin consentimiento
- **Given** una pre-auditoría preparada
- **When** se pide ejecutarla sin consentir la huella mostrada, o con otra huella
- **Then** no se crea la petición y no sale nada del equipo

### Scenario: subida de evidencias acotada
- **Given** un usuario autenticado
- **When** sube un fichero mayor que el límite, o con un nombre con rutas
- **Then** se rechaza y no se escribe nada

## Notas técnicas / riesgos

- Review de seguridad obligatoria: subida de ficheros, petición de envío a un proveedor, escrituras nuevas.
- La lista blanca de escritura de `ui/store.py` crece solo lo necesario (evidencias manuales, peticiones de pre-auditoría).
- El texto nunca dice «certifica», «garantiza el cumplimiento» ni «cumple el ENS».

## Tasks (no autoritativo — el board manda)

- [ ] APIs de edición: riesgos, aplicabilidad, respuestas
- [ ] Asistente: riesgos y aplicabilidad
- [ ] Asistente: documentos
- [ ] Matriz y evidencias
- [ ] Expediente y pre-auditoría
- [ ] Fallos de la demo
- [ ] Tests de escenarios y hostiles

## Referencias

- docs/specs/web-ui.md, docs/specs/web-ui-base.md
- .claude/loop/web-ui/plan.md
