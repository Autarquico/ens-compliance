# 0018 — Banco de defectos sembrados

- Estado: aceptada
- Fecha: 2026-11-05

## Contexto

La visión (`docs/vision.md`, «Validación del auditor») pide validar la
pre-auditoría contra la legislación con un banco de defectos sembrados: casos
con incumplimientos conocidos, expedientes correctos y pymes sintéticas, una
partición de desarrollo y otra reservada, y un umbral de cierre de v0.1. La
ADR 0017 dejaba la eficacia de la pre-auditoría a este banco: sus tests
prueban el contrato, no si el modelo acierta. Procedimientos:
[runbook del banco](../runbooks/banco-defectos.md).

El banco **mide la herramienta**. No aprueba ni dictamina sobre ningún
expediente (visión, punto 5).

## Decisión

### Se mide el producto real

Cada caso pasa por `preaudit.service.prepare` y `preaudit.service.run`, con
el payload, la capa determinista, la validación de hallazgos y la cadena de
registros del producto. Lo único que se sustituye es el destino: un
`client_factory` que repite o graba respuestas. Los hallazgos se leen de los
registros de pre-auditoría escritos, no de valores intermedios.

Para eso el producto tiene dos costuras que la CLI no expone: la fábrica del
cliente de juicio en `service` y la fuente de ids de evidencia en el
importador. Por defecto se comportan como antes.

### Disposición

En la raíz del repo, fuera del paquete:

```
benchmark/
  profiles/<partición>/<perfil>/profile.yaml   # pyme ficticia, completa y correcta
  profiles/<partición>/<perfil>/files/...      # alcance, org-docs, evidencias, recogidas
  cases/<partición>/<caso>.yaml
  cases/<partición>/files/<caso>/...    # textos que usan las mutaciones
  recordings/<partición>/<caso>/<provider>--<modelo>.json
  baseline/dev.json
  measurements.jsonl                    # solo adición
```

Los perfiles van por partición, como los casos: validar, construir o preparar
una partición no lee los perfiles de la otra, y la exclusividad de perfiles
sale por construcción. Rotar la reservada es mover sus casos y perfiles a
`dev` (runbook, «Rotar la reservada»).

En el repo solo hay pymes ficticias. Los ficheros admitidos son `.txt`,
`.md`, `.yaml` y `.json`, y `benchmark validate` les pasa la heurística de
datos ficticios: correos, URL y dominios solo de los reservados (`.example`,
`.test`, `.invalid`); IP solo de documentación o privadas; nunca un DNI/NIE,
un NIF de empresa, un IBAN, un teléfono ni algo con forma de secreto. Un
enlace simbólico o un fichero que resuelve fuera del banco no se lee nunca, y
`validate` lo rechaza.

### Perfil

`profile.yaml` (`format: ens-benchmark-profile/1`, `fictional: true`) declara
lo que una persona haría con la herramienta: reloj, niveles de
categorización, personas y roles, decisiones de aplicabilidad que difieren de
la propuesta, refuerzos elegidos, firmantes de los seis documentos,
evidencias con sus enlaces y la revisión de cada medida. El builder lo
construye con las APIs del producto, paso a paso, y termina en la instantánea
del expediente. No escribe a mano en `evidence/` ni en `assessment/`. Un
perfil correcto da cero hallazgos deterministas.

Dos ayudas del builder evitan repetir lo que no es el objeto del caso:

- **`incident_drill`:** un simulacro de incidente, como `incident new
  --simulated`, una actuación de emergencia y el cierre. Su historial es la
  evidencia que admite `op.exp.9`, y se enlaza como cualquier evidencia.
- **`matrix_defaults`:** responsable, periodicidad, revisor y conclusión de
  las medidas que no tienen fila propia en `matrix`. Una medida aplicable sin
  fila se revisa como `COMPLIANT`; una no aplicable confirma la decisión.

Reproducibilidad:

- reloj fijo: cada paso usa el instante siguiente a `clock.start`, de minuto
  en minuto, pasado como `now=` a las funciones de dominio;
- ids de evidencia sembrados: `EV-<fecha>-<8 hex>` derivados del sha256 de la
  semilla y un contador. La base se siembra con el id del perfil y las
  mutaciones con la clave del caso;
- la base de cada perfil se construye una vez por ejecución y cada caso
  trabaja sobre una copia.

Construir dos veces el mismo caso da el mismo expediente y la misma huella
del payload. Lo prueban los tests, también en dos procesos separados.

### Caso

`case.yaml` (`format: ens-benchmark-case/1`):

- `id`, `partition` (`dev` o `holdout`), `kind` (`defective` o `correct`) y
  la base: `profile`, o `workspace` (ver «Límites»);
- `judgment`: `false` en un caso solo determinista, que se ejecuta sin
  consentimiento ni proveedor;
- `mutations`: lista cerrada, todas antes de generar el expediente:
  `replace_evidence_text`, `expire_evidence`, `unlink_evidence`,
  `unlink_document`, `set_answer`, `declare_not_applicable`, `compensate`,
  `collected_override` y `tamper_artifact`. Cada una usa la API de dominio o
  edita los YAML como lo haría el usuario y vuelve a aprobar con la función
  del producto. Tras cada una, la persona que revisa cada medida afectada la
  revisa otra vez como dice el perfil. Si el producto no admite esa revisión
  (por ejemplo, `COMPLIANT` con una evidencia caducada), no se registra y la
  anterior queda desfasada. Así lo que siembra el caso es el defecto, no una
  revisión olvidada. Un caso con `tamper_artifact` es siempre
  `judgment: false`;
- `defects`, cada uno con:
  - `id`, `layer` (`deterministic` o `judgment`) y `measure`;
  - `target`: `evidence` (alias del perfil), `document` (ref), `justification`
    (la de la medida) o `none` (una ausencia sin objeto que citar);
  - `expected_rule` si es determinista, o `expected_criterion` (uno de los
    tres criterios del pack) si es de juicio. Nunca los dos;
  - `requirement {measure, clause, quote}`: `clause` es el id de la cláusula
    en el catálogo empaquetado y `quote` tiene que ser literal de su texto.
    Solo se normalizan los espacios. Una cita parafraseada o inventada no
    valida;
  - `rationale`.

Dentro de un caso no puede haber dos defectos con la misma medida, el mismo
objetivo y la misma regla o el mismo criterio: competirían por los mismos
hallazgos. Por lo mismo, un defecto de ausencia (`target: none`) no convive
con otro de objetivo concreto con su misma medida y regla o criterio: la
ausencia casa con cualquier hallazgo que case el otro. Los ids de caso no se
repiten entre particiones.

### Grabación y su ligadura

`recordings/<partición>/<caso>/<provider>--<modelo>.json`
(`format: ens-benchmark-recording/1`) lleva el caso y el sha256 de su YAML,
el proveedor, el modelo configurado, el destino, la versión y la huella del
pack, la huella del payload, la versión de la herramienta, la huella del
catálogo, `recorded_at`, `recorded_by` y una entrada por repetición.

- De cada repetición se graba lo que devuelve `judge()`: el texto, solo si
  pasa el esquema; si no, `schema_error`; de un error del proveedor, solo su
  tipo. Nunca stdout ni stderr.
- Antes de escribir se recorren los valores decodificados (también la
  respuesta parseada) con los detectores de datos personales y de secretos.
  Si detectan algo, no se escribe. `benchmark validate --recordings` repite
  ese escaneo sobre las grabaciones existentes.
- El destino de una CLI se guarda sin la ruta absoluta del ejecutable: queda
  su nombre y su sha256.
- La temperatura se registra como `null`, con la nota «no aplica»:
  `JudgmentConfig` no la expone y los clientes no la envían.

**Ligadura (decisión D1):** la grabación se liga a la huella completa del
payload (`payload_sha256`) y a la del pack (`pack_sha256`). La huella
completa incluye la referencia al expediente, y con ella la versión de la
herramienta, la huella del catálogo y los instantes. Al repetir, el destino
comprueba las dos. Si no cuadran, lanza `RecordingStale`, que aborta la
ejecución con el código 3 (no cuenta como fallo del proveedor). La repetición
exige además el mismo proveedor y modelo que el par pedido y exactamente tres
repeticiones.

El destino se liga al grabar: forma parte de la huella combinada que se
consiente (proveedor, modelo, destino, pack y la lista ordenada de pares
caso y huella del payload). Sin esa huella en `--consent`, `record` no envía
nada.

### Puntuación

Por proveedor y modelo, con funciones puras:

| Defecto | Casa con un hallazgo de… |
|---|---|
| De juicio | juicio, misma medida, mismo `expected_criterion` y la ref del objetivo entre las suyas |
| Determinista con objetivo | determinista, misma medida, `criterion` igual a `expected_rule` y la ref de la evidencia |
| Determinista sin objetivo (ausencia) | determinista, misma medida, misma regla y alguna ref de sección |

- **Excepción en las ausencias (decisión D2).** La visión pide «su medida y
  su evidencia o documento». Una evidencia ausente o un check `UNKNOWN` no
  tienen evidencia ni documento que citar: casan por medida, regla y
  referencia de sección.
- Cada hallazgo casa con un solo defecto (emparejamiento máximo). Varios
  hallazgos sobre un mismo defecto son duplicados y cuentan una vez. Un
  hallazgo que casa con algún defecto nunca es falsa alarma.
- **Mayoría (decisión D5).** Un defecto cuenta como detectado si lo está en
  al menos 2 de 3 repeticiones.
- **Tasa A:** falsas alarmas de juicio / hallazgos de juicio emitidos, sobre
  todos los expedientes (defectuosos y correctos), con duplicados agrupados.
- **Tasa B:** expedientes correctos con alguna falsa alarma / expedientes
  correctos.
- A y B se calculan en cada repetición y se toma la mediana baja de las
  tres: siempre una repetición real. Una repetición sin hallazgos de juicio
  emitidos tiene A = 0 y se informa aparte.
- Un hallazgo determinista sin casar es un error del caso o una regresión:
  hace fallar cualquier puerta.

### Puertas

**`threshold`, sobre la reservada, por proveedor y modelo:** 100 % de los
defectos deterministas, ≥ 80 % de los de juicio, A ≤ 20 % y B ≤ 20 %
(decisión D6), sin hallazgos deterministas sin casar. Falla también si la
muestra no llega al tamaño mínimo de la reservada (`HOLDOUT_MINIMUM` en
`score.py`) o si no hay expedientes correctos: B de 0/0 no cuenta como 0 %.
Solo se acepta con `--partition holdout`.

El 100 % determinista cubre solo las reglas que siembra el banco.
`DOSSIER_INTEGRITY` queda fuera (decisión D3): no hay texto del anexo II que
citar y la preparación rechaza un expediente que no es íntegro. Esos casos ya
los cubren los tests de la pre-auditoría.

**`baseline`, sobre la de desarrollo, frente a la línea base de la rama
base.** Falla si:

- un defecto detectado en la línea base deja de detectarse;
- sube la tasa A o la tasa B;
- falta un proveedor y modelo que la línea base tenía;
- lo determinista baja del 100 %;
- el inventario cambia sin una retirada anotada.

Las dos primeras se miden solo sobre lo que no ha cambiado: los defectos
con su huella y la de su caso iguales en los dos inventarios, y, para A y B,
los casos con todo su inventario igual (la línea base guarda el detalle por
caso para recalcularlas). Así un caso o un defecto nuevo nunca cuenta como
regresión, aunque el pack no lo detecte o dé una falsa alarma; lo
determinista sí le exige el 100 %.

Bajar la línea base en el mismo PR no sirve: la puerta compara con la de la
rama base (`--baseline-ref`).

**`target`, sobre la de desarrollo:** los umbrales de `threshold` por cada
par de la puerta (los dos obligatorios, `--all-recorded`), sin la muestra
mínima de D7 pero sin denominadores vacíos. Es un objetivo de ajuste, no
cierra v0.1; se pasa antes de `baseline --write`.

### Pares de v0.1 (decisiones D4 y D7)

- «Anthropic» es `claude-code/claude-opus-5-5` y «OpenAI» es
  `codex/gpt-6.1-sol`, con el plan contratado del usuario. Son los pares que
  deciden el umbral.
- Las API con clave (`anthropic`, `openai`) siguen soportadas, se graban y
  se informan, pero no son puerta de v0.1.
- Con `--all-recorded` y una puerta, faltar cualquiera de los dos pares sale
  con 3. El CI lo aplica en la partición de desarrollo: un banco sin grabar
  no pasa.
- Tamaño mínimo: reservada con 20 defectos de juicio, 10 deterministas y 3
  correctos; desarrollo con 10, 6 y 2. Solo BÁSICA.

**Enmienda de 2026-11-08 (decisión del usuario, por cuota):** el par OpenAI
(`codex/gpt-6.1-sol`) se pospone porque la cuota del plan está agotada hasta
el 2026-11-11 («postponemos la prueba de codex»). Mientras tanto,
`claude-code/claude-opus-5-5` es el único par que es puerta (`REQUIRED_PAIRS`).
El par de OpenAI sigue soportado: se puede grabar, informar y puntuar, pero
no decide. v0.1 se cierra de forma provisional solo sobre Anthropic; el
umbral de OpenAI queda pendiente de su seguimiento, que repone el par y
regraba. Esta enmienda no declara que OpenAI cumpla el umbral ni ninguna
conformidad. Las grabaciones de desarrollo de codex se retiran del árbol
(siguen en el historial de git).
La puerta de línea base admite retirar un par con una entrada `retired` cuyo
`id` es `proveedor/modelo` y cuyo `sha256` es el de su entrada en la línea base
de la rama base; el par `codex/gpt-6.1-sol` se retira así, hasta su seguimiento.

### Disciplina de la reservada

Es procedimental, y así se dice:

- la reservada la escribió un agente distinto, que no había visto el prompt
  del pack ni sus ajustes;
- el CI nunca la ejecuta: valida y repite solo la de desarrollo;
- todas las órdenes que la usan (`validate`, `build`, `prepare`, `record`,
  `run`) pasan por una sola función que exige `--measurement`;
- cada uso deja una línea en `benchmark/measurements.jsonl`, de solo adición,
  con el instante y la acción y, si los hay, el pack, el proveedor y el
  modelo. `run` añade además el resultado de cada par.

`measurements.jsonl` **no es una garantía**: los ficheros están en el repo y
se pueden leer por otros medios. Hace visibles las miradas hechas con la
herramienta. `AGENT.md` prohíbe a los agentes leer o ejecutar la reservada
sin una petición expresa del usuario.

No se ajustan prompts ni criterios con lo que muestre la reservada.

### Inventario protegido

La línea base guarda una huella por caso (su YAML sin los defectos, más sus
ficheros y los de su perfil: `profile.yaml` y `files/`) y otra por defecto.
Cambiar un perfil cambia la huella de todos sus casos: la puerta los da por
cambiados y exige retirarlos con la huella que imprime; retirados, quedan
fuera de la comparación de regresiones hasta la siguiente línea base. En la
puerta `baseline`, un caso o un defecto de la línea base que falta o cambia de
huella falla, salvo que `baseline/dev.json` del árbol de trabajo lo anote en
`retired: [{id, sha256, reason}]`. Las altas no necesitan anotación. `sha256`
es la huella de lo retirado (con sus defectos, si es un caso) en la línea base
de la rama base, y la puerta la imprime: una retirada solo cubre esa versión,
así que un cambio posterior del mismo id exige anotar la nueva huella.
`benchmark baseline --write` conserva las retiradas que ya había; las de
versiones pasadas no cubren nada.

### Informe

Cada informe, en texto o JSON (`format: ens-benchmark-result/1`), lleva:

- el aviso «Cobertura del banco, no garantía universal. Superar el banco no
  aprueba un expediente real.»;
- el alcance del 100 % determinista y las reglas sembradas;
- sus fuentes y timestamps: versión de la herramienta, versión y huella del
  pack, huella del catálogo, huella de los casos y de las grabaciones de cada
  par, `recorded_at`, `recorded_by` y `run_at`;
- el detalle por caso y por repetición.

Nunca emite un dictamen.

## Consecuencias

- El CI repite la partición de desarrollo sin red ni claves. Un cambio del
  pack o del payload sin regrabar sale con 3; una bajada de la detección o
  una subida de las falsas alarmas sale con 1.
- El umbral de v0.1 lo da una medición manual de la reservada con los dos
  pares. Si un par no llega, v0.1 no cierra y queda anotado en
  `measurements.jsonl`.
- El benchmark llama a `service` directamente: no ejerce la envoltura de la
  CLI `preaudit run`. Esa parte la cubren los tests de la pre-auditoría.

## Límites

- **Las grabaciones no van firmadas.** Una edición a mano no se detecta por
  sí sola. Lo mitigan la revisión del diff, que la repetición vuelva a pasar
  por la validación de hallazgos (las citas tienen que ser literales del
  payload), `recorded_by` y `recorded_at`.
- **Regrabar cuesta.** Cualquier cambio del pack o del payload (también la
  versión de la herramienta o el catálogo, por la ligadura completa) obliga a
  regrabar todos los casos de juicio, 3 repeticiones, con los dos pares, a
  mano y con el plan del usuario.
- **`case_sha256` se registra pero no liga.** Cambiar el YAML de un caso sin
  cambiar su payload (por ejemplo, el `rationale` de un defecto) no
  invalida la grabación. Ese cambio lo detecta el inventario protegido.
- **El destino no se reverifica al repetir.** La repetición usa el destino
  grabado. El CI no tiene las CLIs, así que no puede comparar con el
  ejecutable instalado.
- **Una ausencia da varios hallazgos.** Una evidencia que falta produce un
  `EVIDENCE_MISSING` por la base y otro por cada cláusula sin cubrir. El
  banco los agrupa en un solo defecto, como duplicados. Si algún día se exige
  «un hallazgo por defecto», habrá que revisarlo.
- **No se puede sembrar una exclusión débil de una medida exigida en
  BÁSICA.** El producto solo deja excluir una medida exigida por el anexo II
  si falta el tipo de activo (`absent_assets`), y la mutación
  `declare_not_applicable` no lo fija. Los defectos de solidez de la
  justificación tienen que salir de compensaciones débiles o de excluir con
  una razón débil una medida no exigida que el perfil había declarado
  aplicable.
- **Las bases `workspace:` aún no se construyen.** El validador las rechaza
  dentro de un repo git, pero el builder solo construye `profile:`. Además,
  la heurística de datos ficticios y el escaneo de las grabaciones se aplican
  también fuera del repo. Hoy las variantes con datos reales de Autarqui no
  se pueden ejecutar con el banco.
- **Cobertura del banco, no garantía universal.** Superar el banco no aprueba
  un expediente real: el expediente lo aprueban las personas.
