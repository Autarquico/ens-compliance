---
codename: ens-compliance
title: UI — «Recoger ahora» por petición en cola
type: task
status: shipped
priority: P1
size: M
issue: 95
prs: [97]
related_issues: [95]
depends_on: [ui-conectores]
supersedes: []
related_specs: [ui-conectores]
created: 2026-11-17
updated: 2026-11-18
---

# UI — «Recoger ahora» por petición en cola

## Contexto / problema

La web no puede recoger: no tiene credenciales ni salida a Internet (ADR 0021). El scheduler
de `app` sí puede, y ya atiende peticiones que escribe la UI para la pre-auditoría:
- la UI escribe `evidence/preaudits/requests/…`;
- el scheduler la reclama, la ejecuta bajo el lock del ciclo y escribe el resultado;
- referencia: `preaudit/requests.py`, `scheduling/scheduler.py:233-241,420-437`.

Este tramo usa el mismo mecanismo para recoger un conector y evaluar sus checks bajo demanda.

## Scope

**Entra:**
- **Botón «Recoger ahora»** en cada conector configurado de `/conectores`, para editores. Al
  pulsarlo:
  - se escribe una petición con creación exclusiva;
  - hay como mucho una petición viva por conector;
  - caduca a las 24 h.
- **El scheduler** la atiende en cuanto queda libre (normalmente en menos de un minuto) bajo el
  lock del ciclo, con un solo intento:
  - ejecuta `collect` de ese conector y después `check`;
  - escribe el resultado: estado, hora e identificador de la ejecución, sin mensajes en bruto.
- **La página muestra la petición** como pendiente, en curso o hecha (con su resultado), y
  como caducada si nadie la atendió.
- Si el ciclo diario está en marcha, la petición espera al lock como hace la pre-auditoría.

**No entra:**
- Recoger todos los conectores a la vez.
- Ejecutar nada en el servicio `ui`.
- Cancelar una petición.

## Criterios de aceptación

### Scenario: petición atendida
- **Given** GitHub configurado y el scheduler en marcha
- **When** un editor pulsa «Recoger ahora»
- **Then** la página muestra la petición pendiente; en cuanto queda libre, el scheduler recoge GitHub, evalúa sus checks y marca la petición hecha con su resultado; la página muestra la nueva recogida

### Scenario: una sola petición viva
- **Given** una petición pendiente para GitHub
- **When** otro editor pulsa «Recoger ahora»
- **Then** no se crea otra y la página lo dice

### Scenario: scheduler parado
- **Given** una petición sin atender durante más de 24 h
- **When** se abre `/conectores`
- **Then** la petición aparece como caducada, con la indicación de revisar el scheduler, y se puede pedir otra

### Scenario: solo lectura
- **Given** un usuario sin permiso de edición
- **When** hace POST a la petición
- **Then** recibe 403 auditado y no se escribe nada

## Notas técnicas / riesgos

- `evidence/collect-requests/` debe entrar en los prefijos que escribe la UI.
- Hay que medir qué pasa si la recogida falla a medias. Lo importado queda como en
  `collect`: un fallo es una evidencia en `ERROR`.

## Referencias

- `docs/specs/ui-conectores.md`, `docs/specs/ui-preauditoria*.md` si existe,
  `src/ens_evidence/preaudit/requests.py`
