---
codename: ens-compliance
title: UI de inventario
type: task
status: active
priority: P0
size: M
issue: 90
prs: [91]
related_issues: [90]
depends_on: [web-ui, discovery-inventory, osquery-agents]
supersedes: []
related_specs: [web-ui, ui-copias, ui-conectores]
created: 2026-11-16
updated: 2026-11-17
---

# UI de inventario

## Contexto / problema

Hoy el inventario solo se ve y se gestiona desde la CLI (`inventory validate`,
`inventory reconcile`, `scope add-asset`) o dentro del paso «activos» del asistente, que
sirve para darlo de alta pero no para mantenerlo. No hay ninguna pantalla que responda
«qué activos tengo, qué cubre cada uno y qué dice la última evidencia». Es el primer tramo
de la UI de operación (después: copias y conectores).

## Scope

**Entra:**
- **Listado `/inventario`.** Una fila por activo: identificador, tipo, despliegue
  (desplegado/planificado), si está en el alcance o excluido (con la justificación), y el
  último resultado de checks que lo tienen como `asset_id` (peor estado, fecha y fuente).
  Filtros por tipo, despliegue y estado. Un activo sin resultados dice «sin evidencia»,
  nunca un estado favorable.
- **Ficha `/inventario/{activo}`.** Campos del activo, servicios y dependencias que lo
  referencian, y los últimos resultados de checks por conector, con fuente y timestamp
  (regla 11). Enlace al panel de agentes cuando el activo es un equipo inscrito.
- **Alta, edición y baja** con permiso de edición (ADR 0022), reutilizando la validación del
  paso «activos» del asistente. Cambiar el alcance revoca la aprobación igual que en el
  asistente y la UI lo advierte antes de guardar. La baja pide confirmación explícita, queda
  en la auditoría y se rechaza si un servicio o una dependencia referencia el activo.
- **Reconciliación con los agentes.** Sección informativa que compara el inventario con
  los equipos admitidos en el registro de agentes osquery: equipos que reportan y no están
  en el inventario, y equipos desplegados del inventario que no reportan. Desde una
  discrepancia se puede abrir el alta con el hostname prellenado. No bloquea nada.
- Enlace en la navegación principal.

**No entra:**
- Lanzar recogidas o configurar conectores (tramo `ui-conectores`).
- Copias de seguridad (tramo `ui-copias`).
- Altas o revocaciones de agentes (siguen en la CLI).
- Importar inventarios desde ficheros externos.

## Criterios de aceptación

### Scenario: listado con evidencia
- **Given** un inventario válido con un activo con resultados de checks y otro sin ninguno
- **When** se abre `/inventario`
- **Then** el primero muestra su peor estado con fecha y fuente y el segundo «sin evidencia»; ninguno aparece como conforme

### Scenario: ficha del activo
- **Given** un activo referenciado por un servicio y con resultados de dos conectores
- **When** se abre su ficha
- **Then** se ven sus campos, el servicio, y los resultados por conector con fuente y timestamp

### Scenario: alta que cambia el alcance
- **Given** un alcance aprobado y un usuario con edición
- **When** da de alta un activo incluido en el alcance
- **Then** la UI advierte antes de guardar, el activo queda en el inventario y la aprobación del alcance queda revocada

### Scenario: baja de un activo referenciado
- **Given** un activo que usa un servicio
- **When** se intenta dar de baja
- **Then** se rechaza explicando qué lo referencia y no se escribe nada

### Scenario: sin permiso de edición
- **Given** un usuario sin rol de edición
- **When** abre el inventario o intenta guardar
- **Then** ve el listado y las fichas, no ve acciones de escritura y un POST recibe 403 auditado

### Scenario: equipo que reporta y no está inventariado
- **Given** un equipo admitido en el registro de agentes cuyo hostname no figura en el inventario
- **When** se abre `/inventario`
- **Then** aparece en la reconciliación como no inventariado, con acceso al alta prellenada, sin bloquear nada

### Scenario: inventario inválido
- **Given** un `assets.yaml` que no valida
- **When** se abre `/inventario`
- **Then** se muestran los errores de validación y no se ofrece ninguna escritura que pueda sobrescribirlo

## Notas técnicas / riesgos

- Los resultados salen del historial de checks (`checks/store.py`, `scan_runs`); sin
  recalcular checks en la petición.
- La UI ya relee el workspace en cada petición; no se añade caché.
- Sin verificar: coste de `scan_runs` con historiales largos en cada carga del listado.

## Referencias

- `docs/decisions/0021-*`, `docs/decisions/0022-roles-y-mfa-en-la-interfaz-web.md`
- `docs/specs/discovery-inventory.md`, `docs/specs/osquery-agents.md`
