---
codename: ens-compliance
title: UI de copias de seguridad
type: task
status: active
priority: P0
size: M
issue: 92
prs: []
related_issues: [92]
depends_on: [kit-backups, collector-restic, ui-inventario]
supersedes: []
related_specs: [ui-inventario, ui-conectores, kit-backups]
created: 2026-11-17
updated: 2026-11-17
---

# UI de copias de seguridad

## Contexto / problema

Las copias ya funcionan, pero su estado solo se ve por la CLI (`backup status`,
`backup restore-test record-kit`) o dentro de Backrest. La web tiene el asistente que las
prepara (`/herramientas/copias`) y una tarjeta de estado, pero ninguna pantalla responde
«¿se están haciendo las copias, cuándo fue la última buena, cuándo se probó restaurar y qué
falta?». Es el segundo tramo de la UI de operación, después del inventario.

Quién hace qué no cambia (`kit-backups`):
- **Backrest** ejecuta las copias de la organización y las restauraciones, en
  `copias.<dominio>`, tras Access.
- **La app** lee los repositorios con credenciales de solo lectura y evalúa
  BACKUP-001…006.
- **La copia propia de la caja** (`backup run`) la lanza el scheduler.

El contenedor de la web no tiene credenciales de restic ni acceso a lo restaurado. Por eso
esta pantalla muestra y guía, pero no ejecuta copias ni restauraciones.

## Scope

**Entra:**
- **Página `/copias`** con un bloque por cada repositorio de `connectors/backups.yaml`:
  - su activo del inventario, con enlace a su ficha;
  - la última copia correcta y su antigüedad frente a la periodicidad de la política, si la
    política la declara;
  - el último resultado de BACKUP-001…006 para ese repositorio, con fuente y timestamp
    (regla 11), sacado del historial de checks sin recalcular;
  - la prueba de restauración vigente: fecha, revisor declarado y cuándo caduca, o «sin
    prueba vigente»;
  - el enlace «Abrir Backrest» para hacer una copia ahora o restaurar.
- **Copia propia de la caja.** Un bloque con lo que da `backup status`:
  - la última ejecución, su estado y su snapshot;
  - las ejecuciones interrumpidas y los registros ilegibles.
- **Historial.** La lista de pruebas de restauración registradas (de `evidence/restore-tests`),
  con:
  - el repositorio y la fecha;
  - si lo restaurado coincidió con el original;
  - el revisor declarado.
- **Guía de la prueba de restauración.** En cada repositorio, los pasos y el comando
  `backup restore-test record-kit` con el `--repository` ya puesto, y el YAML de declaración
  de ejemplo sin rellenar el revisor. Solo muestra el comando; no lo ejecuta.
- **Sin copias configuradas.** Si no hay `connectors/backups.yaml`, la página lo dice y
  lleva al asistente `/herramientas/copias`. Nunca da un estado favorable.
- Enlace en la navegación principal, y desde la tarjeta de Copias de `/herramientas`.

**No entra:**
- Lanzar copias o restauraciones desde esta web (lo hace Backrest; ejecutarlas aquí
  exigiría dar credenciales de restic al contenedor de la web).
- Registrar la prueba de restauración desde la web (la comparación de ficheros la hace la
  CLI donde está lo restaurado).
- Editar la política de copias (es documentación organizativa) o la configuración de
  Backrest (es el asistente existente).
- Configurar conectores (tramo `ui-conectores`).

## Criterios de aceptación

### Scenario: repositorio al día
- **Given** un repositorio con una copia correcta dentro de la periodicidad y una prueba de restauración vigente
- **When** se abre `/copias`
- **Then** se ven la última copia con su fecha, los resultados BACKUP-001…006 con fuente y timestamp, y la prueba con su caducidad; nada dice que la medida se cumpla

### Scenario: copia que dejó de hacerse
- **Given** un repositorio cuya última copia correcta es más antigua que la periodicidad
- **When** se abre `/copias`
- **Then** el bloque lo marca como problema, muestra BACKUP-001 en `FAIL` y ofrece «Abrir Backrest» y el runbook

### Scenario: sin prueba de restauración
- **Given** un repositorio con copias correctas y sin prueba de restauración vigente
- **When** se abre `/copias`
- **Then** dice «sin prueba vigente», muestra la guía con el comando para ese repositorio y no presenta el repositorio como resuelto

### Scenario: copia propia de la caja interrumpida
- **Given** una ejecución de `backup run` sin registro final
- **When** se abre `/copias`
- **Then** el bloque de la caja la muestra como interrumpida, igual que `backup status`

### Scenario: sin copias configuradas
- **Given** un workspace sin `connectors/backups.yaml`
- **When** se abre `/copias`
- **Then** la página explica que no hay copias configuradas y enlaza al asistente; ningún estado es favorable

### Scenario: solo lectura
- **Given** cualquier usuario autenticado, con o sin permiso de edición
- **When** abre `/copias`
- **Then** ve la misma información; la página no tiene ninguna acción que escriba ni ejecute nada

## Notas técnicas / riesgos

- **Fuentes de datos:**
  - los resultados salen de `checks/store.py`, igual que el inventario, con la misma regla de
    cobertura parcial (K6 de `ui-inventario`);
  - la copia propia sale de `backup.backup_status`;
  - las pruebas salen de `evidence/restore-tests`.
- **Fecha de la última copia** (verificado en el plan):
  - El conector guarda, por snapshot, solo su id, su fecha y si lo escribió el workspace. Ese
    dato está en el artefacto en bruto que cita el resultado BACKUP-001.
  - La página lo lee solo si cuadra todo: la clave canónica, la fuente `backups`, el sha256,
    un máximo de 4 MiB y el mismo repositorio.
  - Si algo no cuadra, muestra la fecha de la comprobación BACKUP-001 y lo dice, sin
    atribuirle antigüedad de copia.
- **Periodicidad** (verificado en el plan):
  - La política no es legible por la máquina.
  - Se muestra la frecuencia declarada en el activo del inventario, como texto.
  - También se muestran los umbrales configurados para las comprobaciones (`max_backup_age_hours`,
    `restore_test_validity_days`), presentados como umbrales y no como la política.
- **Prueba de restauración.** Se muestran los datos declarados (fecha, revisor, caducidad
  declarada y si la revisión la aprueba). El veredicto es solo el resultado registrado de
  BACKUP-005.
- La URL de Backrest sale de la configuración del kit (`copias.<dominio>`), nunca de un dato
  de la petición.

## Referencias

- `docs/specs/kit-backups.md`, `docs/specs/collector-restic.md`, `docs/specs/ui-inventario.md`
- `docs/runbooks/kit-copias.md`
- `docs/decisions/0008-evidencias-y-copia-independiente.md`
