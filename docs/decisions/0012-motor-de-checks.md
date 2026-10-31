# 0012 — Motor de checks: ejecuciones de solo adición y cierre seguro

- Estado: aceptada
- Fecha: 2026-10-31

## Contexto

Los checks de los conectores son funciones puras sobre la evidencia que ya se
ha recogido (ADR 0003). Hasta ahora nadie los ejecutaba ni guardaba sus
resultados, así que en la matriz (ADR 0011) ningún check podía contar. La
regla de preparación de `docs/vision.md` exige READY solo si:

- todas las medidas aplicables están satisfechas;
- cada una tiene evidencia vigente;
- cada una tiene revisión;
- no queda ninguna no conformidad abierta.

## Decisión

- **`check --all` no usa la red.** Lee la configuración de cada conector y la
  evidencia ya guardada en el workspace. Nunca instancia un conector, nunca
  recoge datos y nunca carga plugins.
- **Cierre seguro.** El motor nunca vuelve a una evidencia ni a una ejecución
  anterior. Estos casos dan resultados `UNKNOWN` con su motivo:
  - una evidencia ilegible, sin verificar o incoherente entre artefacto y
    manifiesto;
  - la ausencia de evidencia;
  - un check que falla con una excepción.

  En la matriz cuenta el **último intento** de cada conector. Si ese intento
  no es utilizable, el conector bloquea con `check_last_run_failed`.
- **Ejecuciones de solo adición.**
  - **Dónde se guardan.** Cada ejecución se guarda en
    `evidence/checks/<conector>/<seq>-<instante>-<sha12>.json`.
  - **Qué guardan.** La huella del alcance, la de la configuración y la del
    manifiesto de cada evidencia citada, además de los sujetos esperados.
  - **Cuándo no son legibles.** Bloquean el conector con
    `check_history_unreadable` en estos casos:
    - nombre y contenido no coinciden;
    - hay un hueco en la secuencia;
    - hay un duplicado.
  - **Si cambia la evidencia.** Si cambia un manifiesto citado, la matriz da
    `check_evidence_changed`.
  - **Si cambia el alcance.** Si el alcance cambia, el conector bloquea con
    `check_scope_changed` hasta la siguiente ejecución.
- **Idempotencia.** Una ejecución nueva solo se escribe si cambia algo
  relevante: evidencia, configuración, alcance, sujetos esperados,
  resultados o errores.
- **Sujetos esperados.**
  - GitHub: los repos del inventario aprobado, sin archivados, y la
    organización.
  - Endpoint: los portátiles y servidores del inventario.
  - Resto de conectores: la completitud la señalan los propios checks.
- **Catálogo neutral.** `checks/catalog.py` es el único catálogo de checks.
  La matriz y el motor lo comparten.
- **`assessment status` es solo lectura.**
  - Consume únicamente `matrix.assess()`, con las ejecuciones guardadas.
  - Recibe una fuente de hallazgos. Hasta que exista la pre-auditoría no hay
    ninguna, así que el estado siempre dice:
    - «pre-auditoría no ejecutada: no hay hallazgos registrados»;
    - «Revisión humana pendiente», también cuando el resultado es READY.
- **Un check en PASS nunca implanta una medida.** Solo la revisión humana lo
  hace (ADR 0011).

## Consecuencias

- **Borrado de la última ejecución.** Si alguien la borra, vuelve a contar la
  anterior. Esto solo se detecta contrastando con la copia externa
  (ADR 0008). Lo limitan los 30 días de vigencia de la evidencia técnica.
- **Checks que no emiten ningún resultado** (por ejemplo, sin túneles o sin
  usuarios suspendidos) dan `check_no_results` y bloquean. No conviene
  vincularlos hasta que el conector emita un resultado explícito.
- **`check --all` se ejecuta en serie.** Dos ejecuciones simultáneas producen
  un duplicado de secuencia, que bloquea.
