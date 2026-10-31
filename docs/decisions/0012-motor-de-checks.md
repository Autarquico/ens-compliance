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
    - hay un duplicado;
    - el instante de fin retrocede al avanzar la secuencia.
  - **Reloj.** Se rechaza guardar una ejecución con un instante anterior al
    de la última: el orden por secuencia y por instante es el mismo, así que un
    FAIL posterior nunca queda «antes» de un PASS.
  - **Identidad de la evidencia.** El motor elige la última evidencia de un
    conector por lo que dice el artefacto (`CollectionResult`: conector,
    activo y `collected_at`), que está verificado por hash, y no por los
    campos del manifiesto, que se pueden editar. Si algún manifiesto de
    procedencia collector no coincide con su artefacto (fuente, collector,
    activo o fecha), o su artefacto no verifica, o un manifiesto de otra
    procedencia (`manual`) tiene un artefacto íntegro que es un
    `CollectionResult` de un conector (la procedencia declarada no decide qué
    es candidato; los paquetes de endpoints y la evidencia manual legítimos no
    lo son), los conectores a los que
    pudiera pertenecer (el del artefacto y el que declara el manifiesto; si no
    se puede saber, todos) dan `UNKNOWN` con `evidence_inconsistent` o
    `evidence_unverified`, y la matriz bloquea con `check_evidence_inconsistent`
    o `check_evidence_unverified`. Nunca se cae a una evidencia anterior. Una
    evidencia legítima siempre coincide con su artefacto, así que recoger otros
    activos no bloquea. En endpoints se aplica igual: un paquete cuyo artefacto
    es de endpoint pero cuyo `source` se editó cuenta como incoherente en vez
    de saltarse.
  - **Evidencia posterior.** Si hay evidencia recogida después de lo que cita
    la última ejecución, el conector bloquea con `check_run_missing` hasta
    que se ejecute `check --all`.
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

- **Borrado de la última ejecución.** Borrarla ya no devuelve el PASS
  anterior mientras exista la evidencia recogida después de esa ejecución:
  el conector bloquea con `check_run_missing`. Si no se puede listar o leer
  la evidencia, no se sabe si hay una recogida posterior y el conector
  bloquea con `evidence_unreadable` (ante la duda se bloquea, nunca se
  favorece). Un directorio de conector desconocido en `evidence/checks/`
  se informa como `check_connector_unknown` y no tumba ningún comando.
- **Límite: manipulación coordinada.** Editar un campo del manifiesto
  (`asset_id`, `collected_at`, `collector.name`) ya no basta para que la
  evidencia deje de ser candidata: bloquea (ver «Identidad de la evidencia»).
  Cambiar `provenance.kind` de `collector` a otra tampoco: el artefacto sigue
  siendo de collector y el conector bloquea. Queda una vía que no deja rastro
  dentro del workspace: borrar a la vez la última ejecución y el manifiesto de
  la evidencia posterior; o cambiar `provenance.kind` y además romper el
  artefacto, de modo que ya no se sabe que era de collector (`evidence verify`
  sí lo señala). Solo lo detecta contrastar con la copia externa
  inmutable (ADR 0008, `backup`). Lo limitan además los
  30 días de vigencia de la evidencia técnica.
- **La huella no autentica.** El `sha12` del nombre detecta ediciones a mano,
  no una falsificación que recalcule contenido y nombre. Ambas se detectan
  contrastando con la copia externa.
- **Reloj adelantado.** Una ejecución con un instante futuro cuenta desde ese
  instante, y `check --all` no guarda nada anterior a ella. Una sola fuente de
  checks, construida con el reloj de quien consulta, la comparten
  `assessment status`, `matrix status`, `matrix export` y los informes: si
  el historial tiene una ejecución posterior a ese reloj, el conector
  bloquea con `check_run_in_future` (con la fecha y el conector), también con
  `--at`. La salida es explícita y conservadora:
  - Esperar a que el reloj alcance esa fecha.
  - O corregir la hora y ejecutar `check --all --clock-corrected`. Si hay
    ejecuciones con fecha futura, escribe un intento nuevo con la hora
    correcta y deja constancia (`clock_correction`, con las claves de las
    ejecuciones sustituidas). No borra ni modifica nada: las ejecuciones
    futuras siguen en el historial pero dejan de contar. El intento nuevo
    reevalúa la evidencia de hoy, de modo que un FAIL real sigue
    bloqueando. Sin ejecuciones futuras, el indicador no cambia nada.
  - La corrección solo vale sobre ejecuciones previas del mismo conector con
    un instante posterior al suyo; entre las ejecuciones que cuentan (las no
    sustituidas), cualquier inversión del orden sigue dejando el historial
    ilegible, también la de una corrección parcial.
  - Si la recogida también se hizo con el reloj adelantado, su fecha también
    es futura: el conector sigue bloqueado con `check_run_in_future` hasta que
    el reloj la alcance o se vuelva a recoger y se restaure desde la copia.
    Mientras haya una ejecución futura no se emite además `check_run_missing`:
    la fecha futura ya explica el bloqueo.
- **Configuración cambiada.** Si el sha256 actual de `connectors/<conector>.yaml`
  difiere del `config_sha256` del último intento (o el fichero falta o no se
  lee), el conector bloquea hasta ejecutar `check --all`:
  `check_config_changed` si el fichero cambió y `check_config_missing` si
  falta o no se lee. Con la configuración borrada, `check --all` responde
  «no configurado» y no escribe nada: la salida es restaurar el fichero (o
  configurar el conector de nuevo), o desvincular sus checks de la matriz
  (`matrix unlink`) si ya no se usa.
- **Checks que no emiten ningún resultado** (por ejemplo, sin túneles o sin
  usuarios suspendidos) dan `check_no_results` y bloquean. No conviene
  vincularlos hasta que el conector emita un resultado explícito.
- **`check --all` se ejecuta en serie.** Dos ejecuciones simultáneas producen
  un duplicado de secuencia, que bloquea.
