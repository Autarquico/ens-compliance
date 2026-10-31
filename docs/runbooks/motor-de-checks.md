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
| 4 | Algún FAIL |
| 3 | Resultados sin evaluar (UNKNOWN o ERROR), sin ningún FAIL |
| 2 | Falta configuración o el alcance no está aprobado |

Ejecútalo después de cada recogida y siempre de uno en uno. La evidencia
técnica deja de valer a los 30 días de recogerse.

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

- el estado de preparación y los bloqueos de cada medida;
- la última ejecución de cada conector.

Mientras no exista la pre-auditoría, el estado avisa siempre de que hay
**revisión humana pendiente**, también cuando sale preparado. La herramienta
no declara conformidad.

Código de salida: 0 si está preparado y no hay errores; 1 en cualquier otro
caso.

## Problemas

- **`check_history_unreadable`**: una ejecución guardada está dañada o
  incompleta. Restaura `evidence/checks/` desde la copia (`ens-evidence
  backup …`) y vuelve a ejecutar `check --all`.
- **`check_scope_changed`**: el alcance cambió después de la última
  ejecución. Vuelve a ejecutar `check --all`.
- **`check_evidence_changed`**: alguien modificó la evidencia después de la
  ejecución. Vuelve a recoger y a ejecutar.
- **`check_no_results`**: el check no ha emitido ningún resultado. Recoge y
  ejecuta. Si sigue igual, ese check no aplica a tu sistema; no lo vincules.
