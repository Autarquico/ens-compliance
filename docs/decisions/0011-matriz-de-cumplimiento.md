# 0011 — Matriz de cumplimiento por medida

- Estado: aceptada
- Fecha: 2026-10-30

## Contexto

La autoevaluación de categoría BÁSICA debe estar documentada «indicando si
cada medida de seguridad está implantada y sujeta a revisión regular, así como
las evidencias que sustentan la valoración anterior» (RD 311/2022, anexo III,
2.1.a). Las medidas que se aplican salen de la Declaración de Aplicabilidad
firmada (art. 28.2, ADR 0009). La matriz une las dos cosas: para cada medida
de la Declaración, qué la sustenta, quién la revisa y cada cuánto.

## Decisión

- **Las filas salen de la Declaración vigente.** Son exactamente sus medidas
  seleccionadas, no seleccionadas y exigidas con ausencia de activo. Sin una
  Declaración vigente, la aplicabilidad no está determinada y todo queda
  bloqueado. La matriz nunca escribe en `controls/measures.yaml`: hacerlo
  invalidaría la Declaración.
- **Almacenamiento:** `controls/matrix.yaml` guarda el responsable, la
  periodicidad y los enlaces a evidencias. Las revisiones se guardan aparte,
  en `controls/reviews/<medida>/`, solo se añaden y nunca se editan. La matriz
  no entra en las claves que publica `workspace push`: se edita con la CLI
  directamente contra el workspace.
- **Implantada solo por revisión humana.** `COMPLIANT` sale únicamente de una
  revisión registrada por el administrador del sistema o su delegado
  (anexo III, 2.1.a). El responsable de la seguridad analiza el informe
  (2.1.b) y no revisa medidas. Ni un check en verde ni un documento aprobado
  dan por implantada una medida.
- **Cobertura por cláusula.** Cada enlace declara qué cláusulas cubre. Una
  obligación (la base, cada refuerzo exigido o elegido y la compensación del
  art. 28.3) queda sustentada solo si todas sus cláusulas están cubiertas por
  fuentes válidas. Fuentes válidas:
  - **Evidencia manual:** manifiesto de procedencia manual con el artefacto
    íntegro. Si no indica hasta cuándo vale, solo cuenta si se recogió dentro
    de la periodicidad de la medida.
  - **Check:** la última ejecución completa de su conector, con todos los
    sujetos esperados en PASS y evidencia de menos de 30 días. La evidencia
    no puede ser posterior al resultado que la cita ni al fin de su ejecución
    (`evidence_inconsistent`): retocar `collected_at` no rejuvenece un PASS
    antiguo, hace falta una ejecución nueva.
  - **Documento organizativo:** aprobado y vigente (ADR 0010), y solo para las
    cláusulas que cubre.
- **La revisión caduca.** Una revisión deja de valer:
  - al vencer su periodicidad (obligatoria, como máximo 365 días);
  - si cambian los enlaces, una evidencia manual o un documento revisado. De
    la evidencia se guarda la huella del manifiesto completo (vigencia,
    fechas, procedencia y artefacto), no solo la del artefacto;
  - si cambia el contexto de la medida: su entrada en la Declaración, la
    justificación, los refuerzos, la compensación o el texto de la ficha del
    catálogo.

  La huella del alcance no entra: dar de alta un activo no obliga a revisar
  todas las medidas. Los activos se controlan por la cobertura de los checks,
  y cuando cambia el alcance la Declaración se vuelve a firmar.
- **Revisión vigente:** de cada medida vale la revisión más reciente, sea de
  implantación o de aplicabilidad, y solo para el contexto en que se hizo:
  volver a un contexto anterior no la recupera. Para que sea determinista, cada
  firma de la Declaración se archiva en `controls/declarations/` (solo
  adición, junto a la vigente en `controls/applicability.yaml`) y una revisión
  solo vale si ninguna firma posterior a su registro dio a la medida otro
  contexto, aunque nadie ejecutara `matrix` entre medias. Una copia archivada
  ilegible bloquea las medidas revisadas (`declaration_history_unreadable`).
  Cada revisión guarda, por
  obligación, las fuentes y las cláusulas que la persona confirmó; el export
  las muestra junto con su conclusión. Si el historial de una medida contiene
  un fichero ilegible, su contenido es desconocido (podría revocar la
  revisión anterior): la medida queda bloqueada con `review_unreadable:<clave>`,
  las revisiones legibles se muestran pero no la satisfacen y el fallo sale en
  el export y en el código de salida de `matrix status`. Lo mismo con el
  historial de aprobaciones de un documento organizativo: un registro ilegible
  (`org_document_unreadable:<nombre>`) impide que el documento sustente
  medidas, y `docs status` lo muestra. Los YAML editados a mano que lee la
  matriz (revisiones, `matrix.yaml`, aprobaciones) se rechazan si repiten una
  clave, también anidada: `safe_load` se quedaría con la última.
- **Los historiales de solo adición se contrastan con su clave.** La clave de
  una revisión codifica su instante y la huella de su contenido validado
  (`<instante>-<sha12>.yaml`), igual que la de una firma archivada; la de una aprobación documental, documento
  e instante; el id de una evidencia, el día de su alta. Si el contenido no
  coincide (p. ej. `recorded_at`, la valoración o `approved_at` retocados), el registro es
  ilegible: `review_unreadable`, `declaration_history_unreadable`,
  `org_document_unreadable` o la evidencia no verificada. Una edición a mano no
  puede favorecer a la organización: o se detecta y bloquea, o no cambia nada.
- **Los cambios de la matriz tienen historial.** `matrix.yaml` se sobrescribe,
  así que quitar un enlace y volver a ponerlo dejaba el mismo fichero. `init`,
  `set`, `link` y `unlink` registran el estado resultante de la entrada en
  `controls/matrix-history/<medida>/<n.º>-<instante>-<sha12>.yaml` (solo
  adición; número, instante y huella se contrastan con el contenido). Una
  revisión solo vale si, desde su `recorded_at`, ningún registro dejó los
  enlaces o coberturas en otro estado, aunque el final coincida
  (`links_changed`). Si la entrada de `matrix.yaml` no coincide con el último
  estado registrado, se editó a mano: la medida se bloquea con
  `matrix_edited:<medida>` y el fallo sale en `errors`. La siguiente orden
  `matrix set/link/unlink/init` registra primero lo que había y después su
  cambio, de modo que editar a mano y ordenar luego no borra el rastro. Un
  registro del historial ilegible o con hueco bloquea con
  `matrix_history_unreadable:<clave>`.
- **Una sola regla de preparación.** `matrix.assess()` es el único punto de
  integración. Aplica la regla compartida de `models/readiness.py`
  (`measure_blockers`, `evaluate_readiness`) y le añade los huecos de la
  matriz como `coverage_gaps`. El motor de checks (`checks-engine`) consume
  solo esa función.

## Consecuencias

- Los checks cuentan con las ejecuciones que guarda `check --all` (0012); sin
  ejecución, el check vinculado bloquea con `check_no_results`.
- Un FAIL en un check vinculado bloquea la medida aunque haya una revisión
  favorable. Para quitarlo hay que desvincular el check, y ese cambio queda
  visible y obliga a revisar de nuevo.
- Los nombres de quien revisa son declarados, no autenticados, como en el
  resto de aprobaciones.
