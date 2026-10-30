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
    sujetos esperados en PASS y evidencia de menos de 30 días.
  - **Documento organizativo:** aprobado y vigente (ADR 0010), y solo para las
    cláusulas que cubre.
- **La revisión caduca.** Una revisión deja de valer:
  - al vencer su periodicidad (obligatoria, como máximo 365 días);
  - si cambian los enlaces, una evidencia manual o un documento revisado;
  - si cambia el contexto de la medida: su entrada en la Declaración, la
    justificación, los refuerzos, la compensación o el texto de la ficha del
    catálogo.

  La huella del alcance no entra: dar de alta un activo no obliga a revisar
  todas las medidas. Los activos se controlan por la cobertura de los checks,
  y cuando cambia el alcance la Declaración se vuelve a firmar.
- **Una sola regla de preparación.** `matrix.assess()` es el único punto de
  integración. Aplica la regla compartida de `models/readiness.py`
  (`measure_blockers`, `evaluate_readiness`) y le añade los huecos de la
  matriz como `coverage_gaps`. El motor de checks (`checks-engine`) consume
  solo esa función.

## Consecuencias

- Hasta que exista el motor de checks no cuenta ningún check: la única vía de
  evidencia es la manual o la documental, y `matrix status` lo dice.
- Un FAIL en un check vinculado bloquea la medida aunque haya una revisión
  favorable. Para quitarlo hay que desvincular el check, y ese cambio queda
  visible y obliga a revisar de nuevo.
- Los nombres de quien revisa son declarados, no autenticados, como en el
  resto de aprobaciones.
