# Motor de checks y estado de la autoevaluación

Cómo convertir la evidencia recogida en resultados de checks y consultar si la
autoevaluación está preparada. Contrato:
[ADR 0012](../decisions/0012-motor-de-checks.md).

## 1. Recoge y ejecuta

```bash
ens-evidence collect github            # y el resto de conectores configurados
ens-evidence check --all
```

`check --all` no accede a la red: evalúa la última evidencia guardada de cada
conector. Por cada conector muestra:

- si ha escrito una ejecución nueva o si no hay cambios;
- cuántos resultados hay en PASS, FAIL, UNKNOWN y ERROR;
- una línea por cada resultado que no sea PASS.

Código de salida:

| Código | Significado |
|---|---|
| 0 | Todo en PASS |
| 4 | Algún FAIL (y ningún conector roto) |
| 3 | UNKNOWN, ERROR, ejecución incompleta o ningún conector configurado, sin ningún FAIL |
| 2 | No se pudo evaluar: configuración inválida, historial ilegible, ejecución rechazada o alcance no aprobado |

Un conector sin configuración no es un error: sale como «no configurado» y no
cambia el código.

Ejecútalo después de cada recogida y siempre de uno en uno. La evidencia
técnica deja de valer a los 30 días de recogerse.

Si el reloj del equipo va por detrás de la última ejecución guardada, la
ejecución nueva se rechaza (código 2): el historial no admite instantes que
retrocedan. El mensaje dice la hora del equipo, la de esa ejecución y el
siguiente paso. Corregir la hora no basta si la ejecución ya se guardó con el
reloj adelantado: mira «Reloj adelantado» en Problemas.

Si un conector no tiene evidencia, o su evidencia está dañada, sus checks
quedan sin evaluar. El motor nunca usa una evidencia anterior en su lugar.

## 2. Vincula los checks en la matriz

```bash
ens-evidence matrix link op.acc.6 --check github.org_2fa_required --covers base
```

En `matrix status` cada check vinculado muestra su última ejecución, o
«sin ejecución; ejecuta `ens-evidence check --all`». Un check en PASS no da
por implantada la medida: lo valora una persona con `matrix review`
([matriz](matriz-de-cumplimiento.md)). Un FAIL bloquea la medida aunque esté
revisada.

## 3. Consulta el estado

```bash
ens-evidence assessment status
ens-evidence assessment status --format json
```

La salida incluye:

- el estado de preparación y los bloqueos, cada uno con su medida
  (`op.exp.1: …`); la cabecera cuenta los bloqueos mostrados y las medidas
  afectadas;
- la última ejecución de cada conector. Un conector sin configuración sale
  como «no configurado»: configúralo solo si lo usas. «sin ejecución; ejecuta
  `ens-evidence check --all`» aparece solo en los configurados.

Un FAIL se resuelve en el sistema que falla, no en la herramienta: corrígelo,
vuelve a recoger y ejecuta `ens-evidence check --all`.

Mientras no exista la pre-auditoría, el estado avisa siempre de que hay
**revisión humana pendiente**, también cuando sale preparado. La herramienta
no declara conformidad.

Código de salida: 0 si está preparado y no hay errores; 1 en cualquier otro
caso.

## Problemas

- **`check_history_unreadable`**: una ejecución guardada está dañada, falta, está
  repetida o sus instantes retroceden respecto a su número de orden. Restaura `evidence/checks/` desde la copia (`ens-evidence
  backup …`) y vuelve a ejecutar `check --all`.
- **`check_run_missing`**: hay evidencia recogida después de la última
  ejecución guardada. O falta ejecutar `ens-evidence check --all`, o se borró
  el último registro del historial. Ejecuta `check --all`; si no recuerdas
  haberlo borrado, contrasta con la copia (`backup` + `evidence verify`).
- **`check_evidence_inconsistent`** / **`check_evidence_unverified`**: algún
  registro de evidencia de un collector no coincide con su artefacto (fuente,
  collector, activo o fecha) o su artefacto no pasa la verificación de
  integridad, y no se sabe cuál es la última recogida del conector. No se usa
  ninguna anterior: los checks dan `UNKNOWN` con `evidence_inconsistent` o
  `evidence_unverified`. Ejecuta `evidence verify --all`, restaura `evidence/`
  desde la copia (`ens-evidence backup …`) y ejecuta `check --all`.
- **`check_scope_changed`**: el alcance cambió después de la última
  ejecución. Vuelve a ejecutar `check --all`.
- **`check_config_changed`**: `connectors/<conector>.yaml` cambió después de la
  última ejecución. Vuelve a ejecutar `check --all`.
- **`check_config_missing`**: `connectors/<conector>.yaml` falta o no se lee.
  `check --all` responde «no configurado» y no escribe nada, así que no
  desbloquea. Restaura el fichero desde la copia o configura el conector de
  nuevo y ejecuta `check --all`. Si ya no usas el conector, desvincula sus
  checks de la matriz (`matrix unlink`).
- **`evidence_unreadable`**: no se puede listar o leer algún registro de
  evidencia y no se sabe si hay una recogida posterior a la última ejecución.
  Ejecuta `evidence verify --all`, restaura `evidence/` desde la copia
  (`ens-evidence backup …`) y ejecuta `check --all`.
- **`check_connector_unknown`**: `evidence/checks/<nombre>/` tiene ejecuciones
  de un conector que esta versión no conoce (un resto, una copia). Sácalo de
  `evidence/checks/` (la herramienta no borra nada) o restaura desde la copia.
- **`check_run_in_future`**: hay una ejecución con fecha posterior al reloj del
  equipo, porque se guardó con el reloj adelantado. El aviso sale en
  `assessment status` y en `matrix status`, con el conector y la fecha.
  Corregir la hora **no** basta: `check --all` no guarda nada anterior a esa
  ejecución. Hay dos salidas, ambas sin borrar nada:
  1. Esperar a que el reloj llegue a esa fecha y ejecutar `check --all`.
  2. Con la hora ya corregida, ejecutar
     `ens-evidence check --all --clock-corrected`. Escribe una ejecución nueva
     con la hora correcta, anota en ella qué ejecuciones sustituye
     (`clock_correction`) y esas ejecuciones dejan de contar, aunque siguen
     guardadas. Reevalúa la evidencia de hoy: un FAIL real sigue bloqueando.
  Si la recogida también se hizo con el reloj adelantado, su fecha también es
  futura y el conector sigue bloqueado con `check_run_in_future` hasta que el
  reloj la alcance, o hasta recoger de nuevo y restaurar desde la copia.
- **`check_evidence_changed`**: alguien modificó la evidencia después de la
  ejecución. Vuelve a recoger y a ejecutar.
- **`check_no_results`**: el check no ha emitido ningún resultado. Recoge y
  ejecuta. Si sigue igual, ese check no aplica a tu sistema; no lo vincules.

## Qué no detecta el historial

- La huella del nombre de cada ejecución detecta ediciones a mano, no una
  falsificación deliberada con la huella recalculada.
- Borrar a la vez la última ejecución y la evidencia recogida después de ella
  no deja rastro dentro del workspace.
- Borrar la última ejecución **y** el manifiesto de la evidencia posterior.

Editar un campo del manifiesto de la evidencia (`asset_id`, `collected_at`,
`collector.name`, `provenance.kind`) sí se detecta: el motor compara el manifiesto con su
artefacto y bloquea con `check_evidence_inconsistent`. Ojo: `evidence verify`
local no compara esos campos, así que puede dar la evidencia por íntegra.

Estos casos solo los detecta la copia externa: `ens-evidence backup` y
`ens-evidence evidence verify` contra la copia inmutable
([ADR 0008](../decisions/0008-evidencias-y-copia-independiente.md)).
