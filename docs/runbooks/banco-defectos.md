# Banco de defectos sembrados

Cómo validar, construir, grabar, repetir y medir el banco que evalúa la
pre-auditoría. Contrato: [ADR 0018](../decisions/0018-banco-defectos.md).
Pre-auditoría: [runbook](pre-auditoria.md).

El banco **mide la herramienta**. Cobertura del banco, no garantía
universal: superar el banco no aprueba un expediente real. El expediente lo
aprueban las personas.

Todas las órdenes se ejecutan desde la raíz del repo. Por defecto usan el
banco de `benchmark/` y la partición de desarrollo (`--partition dev`).
`--cases <dir>` cambia la raíz del banco.

## Partición reservada: antes de nada

`--partition holdout` solo se usa para medir el umbral, por decisión expresa
del usuario. Todas las órdenes la rechazan sin `--measurement` (sale con 2) y,
con ella, añaden una línea a `benchmark/measurements.jsonl`. Ese fichero hace
visibles las miradas hechas con la herramienta; no impide mirarla por otros
medios. No abras sus ficheros ni ajustes el pack con lo que muestre
(`AGENT.md`).

## 1. Valida el banco

```bash
uv run ens-evidence benchmark validate
uv run ens-evidence benchmark validate --recordings
```

Comprueba el esquema de perfiles y casos, que cada cita es literal del texto
de su cláusula en el catálogo, ids únicos, particiones disjuntas y datos
ficticios. Con `--recordings`, además, que cada grabación es válida, no
lleva datos detectables y sigue ligada al pack y al payload actuales (para
eso construye cada caso grabado).

| Código | Significado |
|---|---|
| 0 | Banco válido (y grabaciones ligadas, con `--recordings`) |
| 2 | Un caso, un perfil o una grabación no vale, o lleva un dato detectable |
| 3 | Una grabación está desligada del pack o del payload: regraba (§ 4) |

## 2. Construye un caso

```bash
uv run ens-evidence benchmark build dev/ct-copias-desenlazadas
uv run ens-evidence benchmark build consultora-tic          # un perfil
uv run ens-evidence benchmark build dev/<caso> --out /tmp/ws-caso
```

Construye el workspace y le pasa solo la capa determinista. No envía nada.
Sin `--out`, usa un directorio temporal que borra al acabar; con `--out`,
tiene que ser nuevo o estar vacío. Imprime el expediente, la huella del
payload (o «no aplica» en un caso solo determinista) y los hallazgos:

```
expediente: 20261001T130000000000Z-f566586ebc09
payload_sha256: b7ecf2d5…
hallazgos deterministas: 3
  EVIDENCE_MISSING mp.info.6: sin evidencia. …
  EVIDENCE_MISSING mp.info.6: la cláusula mp.info.6.1 no tiene evidencia. …
  EVIDENCE_MISSING mp.info.6: la cláusula mp.info.6.2 no tiene evidencia. …
```

Una sola ausencia da varios `EVIDENCE_MISSING` (base y cláusulas); el banco
los cuenta como un defecto. Sale con 2 si el caso no se construye, con el
paso y el motivo.

### Escribir un caso nuevo

1. Elige el requisito y lee su texto en el catálogo:
   `uv run ens-evidence catalog show mp.info.6`.
2. Crea `benchmark/cases/dev/<caso>.yaml`. El nombre del fichero es el `id`.
   Ejemplo real del banco:

   ```yaml
   format: ens-benchmark-case/1
   id: ct-copias-desenlazadas
   partition: dev
   profile: consultora-tic
   kind: defective
   mutations:
     - op: unlink_evidence
       alias: copias
       measure: mp.info.6
   defects:
     - id: copias-sin-evidencia
       layer: deterministic
       measure: mp.info.6
       target: {kind: none}
       expected_rule: EVIDENCE_MISSING
       requirement:
         measure: mp.info.6
         clause: mp.info.6.1
         quote: Se realizarán copias de seguridad que permitan recuperar datos perdidos, accidental o intencionadamente.
       rationale: La medida de copias de seguridad se declara aplicable y no tiene ninguna evidencia enlazada.
   ```

   - `quote` se copia del catálogo; nunca se escribe libre ni se parafrasea.
   - Un defecto de juicio lleva `expected_criterion` (`coherencia-politica-evidencia`,
     `calidad-documental` o `solidez-justificacion-aplicabilidad`) y no
     `expected_rule`.
   - Los textos que usan `replace_evidence_text` y `collected_override` van
     en `benchmark/cases/dev/files/<caso>/`.
   - Con `tamper_artifact`, el caso lleva `judgment: false`.
   - Solo datos ficticios: dominios `.example`, `.test` o `.invalid`; IP de
     documentación o privadas.
3. `benchmark validate` y `benchmark build dev/<caso>`: los hallazgos
   deterministas tienen que ser exactamente los declarados.
4. El caso es de juicio: grábalo (§ 3) antes de abrir el PR, o el CI sale
   con 3.

Cambiar o quitar un caso o un defecto que ya está en la línea base exige
anotarlo (§ 5).

## 3. Graba

Graba cada caso de juicio de la partición con los dos pares que son puerta
de v0.1: `claude-code/claude-opus-5-5` y `codex/gpt-6.1-sol`. Es un envío
real: los payloads (ficticios) salen hacia Anthropic u OpenAI.

El banco no lee el fichero de ajustes: el proveedor y el modelo vienen de
`--provider` y `--model`, sin `base_url` y con el tiempo de espera por
defecto.

### 3.1 Con el plan contratado (`claude-code`, `codex`)

Requisito: la CLI (`claude` o `codex`) en el `PATH`, con tu sesión iniciada
en este equipo. Ver «Configura el proveedor» en el
[runbook de la pre-auditoría](pre-auditoria.md).

1. Prepara, sin red:

   ```bash
   uv run ens-evidence benchmark prepare --provider claude-code --model claude-opus-5-5
   ```

   Construye y prepara cada caso de juicio. Imprime el destino, el aviso de
   la shell si el destino la conserva, la huella del payload de cada caso, la
   **huella combinada** y la orden de grabar ya rellena. Los casos solo
   deterministas se listan aparte: no se graban.

   La huella combinada cubre el proveedor, el modelo, el destino (la CLI con
   su sha256), la huella del pack y la lista de casos con su huella. Si
   cambia cualquiera, cambia la huella. Construye todos los casos: en la
   prueba del 2026-11-05, la partición de desarrollo (21 casos de juicio)
   tardó unos 6 minutos.

2. Revisa la lista y graba con la huella que imprimió `prepare`:

   ```bash
   uv run ens-evidence benchmark record --provider claude-code --model claude-opus-5-5 \
     --consent <huella combinada> --by "<tu nombre>"
   ```

3. Repite con `codex`. Su destino conserva una herramienta de shell que no se
   puede quitar, así que hay que aceptarlo expresamente:

   ```bash
   uv run ens-evidence benchmark prepare --provider codex --model gpt-6.1-sol
   uv run ens-evidence benchmark record --provider codex --model gpt-6.1-sol \
     --consent <huella combinada> --by "<tu nombre>" --accept-shell-risk
   ```

   La orden que imprime `prepare` no lleva `--accept-shell-risk`: añádela
   tú. Sin ella, `record` no envía nada y sale con 1.

Qué hace `record`:

- vuelve a preparar y compara la huella con `--consent`. Si no coincide, no
  envía nada y sale con 1;
- envía cada caso `--repetitions` veces (por defecto 3; la repetición exige
  exactamente 3) por la pre-auditoría real;
- escribe `benchmark/recordings/dev/<caso>/<provider>--<modelo>.json` e
  imprime una línea por caso con el resultado de cada repetición
  (`response`, `schema_error` o `provider_error`);
- no escribe una grabación si detecta en ella algo con forma de dato real o
  de secreto, y lo dice. Las demás se escriben igual;
- se niega (sale con 1) si ya hay grabaciones de ese par y falta
  `--replace`.

| Código | Significado |
|---|---|
| 0 | Todas las grabaciones escritas |
| 1 | Huella que no coincide, shell sin `--accept-shell-risk`, grabaciones que ya existen sin `--replace`, envío rechazado por el producto o alguna grabación no escrita |
| 2 | Entrada no válida: `--by` vacío o con un dato personal, `--model` vacío, pack no íntegro, caso que no se construye o que el producto no deja preparar |

Antes de commitear, lee el diff de `benchmark/recordings/`: las grabaciones
no van firmadas y la revisión del diff es lo que las protege.

### 3.2 Con claves de API (`anthropic`, `openai`)

Soportadas y medidas, pero no son puerta de v0.1. La clave va solo por el
entorno de la sesión, nunca en un fichero del repo ni en los ajustes:

```bash
export ANTHROPIC_API_KEY=…     # u OPENAI_API_KEY con --provider openai
uv run ens-evidence benchmark prepare --provider anthropic --model <modelo>
uv run ens-evidence benchmark record --provider anthropic --model <modelo> \
  --consent <huella combinada> --by "<tu nombre>"
unset ANTHROPIC_API_KEY
```

No hay modelo por defecto.

## 4. Regraba tras un cambio del pack o del payload

Síntoma: `benchmark validate --recordings` o el job de CI salen con 3
(«el audit pack ha cambiado; regraba» o «no corresponde al payload actual»).
La ligadura es a la huella completa del payload: también la cambian la
versión de la herramienta, el catálogo o un perfil.

Para cada par que es puerta:

```bash
uv run ens-evidence benchmark prepare --provider claude-code --model claude-opus-5-5
uv run ens-evidence benchmark record --provider claude-code --model claude-opus-5-5 \
  --consent <huella combinada> --by "<tu nombre>" --replace
uv run ens-evidence benchmark prepare --provider codex --model gpt-6.1-sol
uv run ens-evidence benchmark record --provider codex --model gpt-6.1-sol \
  --consent <huella combinada> --by "<tu nombre>" --replace --accept-shell-risk
uv run ens-evidence benchmark validate --recordings
```

Después, la línea base (§ 5) y el commit de `recordings/` y
`baseline/dev.json` en el mismo PR. Cuesta todos los casos de juicio × 3
repeticiones × 2 pares.

## 5. Escribe la línea base

```bash
uv run ens-evidence benchmark baseline           # solo el diff
uv run ens-evidence benchmark baseline --write   # escribe benchmark/baseline/dev.json
```

Repite todo lo grabado de la partición de desarrollo e imprime un diff
resumido: altas, bajas y cambios del inventario; pares nuevos o que salen;
defectos ganados o perdidos; cambios de las tasas A y B; cambio del pack.
Solo existe para `dev`. Sale con 3 si falta una grabación o está desligada.

**Retirar o cambiar un caso o un defecto** que ya estaba en la línea base:
añade a mano la entrada en `benchmark/baseline/dev.json` y después vuelve a
escribirla (`--write` conserva las retiradas):

```json
"retired": [
  {"id": "dev/<caso>#<defecto>", "reason": "<por qué sale o cambia>"}
]
```

Un `id` sin `#` retira el caso entero con sus defectos. Sin esa entrada, la
puerta del CI falla.

## 6. Repite y puntúa (CI y local)

El job `benchmark` del CI, sin red ni claves:

```bash
uv run ens-evidence benchmark validate --recordings
uv run ens-evidence benchmark run --partition dev --all-recorded \
  --gate baseline --baseline-ref origin/main --format json --out bench.json
```

En un PR compara con la línea base de la rama base; en un push a `main`, con
`HEAD^`. Bajar la línea base en el mismo PR no sirve. Para ver el informe en
texto, quita `--format json --out bench.json`. Para un solo par:
`--provider codex --model gpt-6.1-sol` en lugar de `--all-recorded`.

| Código de `run` | Significado |
|---|---|
| 0 | Pasa la puerta (o no se pidió puerta) |
| 1 | Falla la puerta: regresión, umbral no alcanzado, inventario sin `retired`; o banco vacío |
| 2 | Caso u opción no válida (`--baseline` y `--baseline-ref` juntas, `--gate baseline` fuera de `dev`, reservada sin `--measurement`) |
| 3 | Falta una grabación, falta un par que es puerta o una grabación está desligada: graba o regraba |

## 7. Mide la reservada (U2, cierre de v0.1)

Solo cuando el usuario decide medir. Cada orden lleva `--measurement` y deja
su línea en `benchmark/measurements.jsonl`.

1. Graba la reservada con los dos pares (§ 3.1), añadiendo
   `--partition holdout --measurement` a `prepare` y a `record`:

   ```bash
   uv run ens-evidence benchmark prepare --partition holdout --measurement \
     --provider claude-code --model claude-opus-5-5
   uv run ens-evidence benchmark record --partition holdout --measurement \
     --provider claude-code --model claude-opus-5-5 \
     --consent <huella combinada> --by "<tu nombre>"
   # igual con codex/gpt-6.1-sol y --accept-shell-risk
   ```

2. Mide:

   ```bash
   uv run ens-evidence benchmark run --partition holdout --measurement \
     --all-recorded --gate threshold
   ```

   Con `--all-recorded`, deciden los dos pares de v0.1; los demás grabados se
   informan. Por par: 100 % de los deterministas, ≥ 80 % de los de juicio,
   tasa A ≤ 20 % y tasa B ≤ 20 %.

   - **0:** los dos pares pasan el umbral del banco.
   - **1:** algún par no llega. v0.1 no cierra. El informe dice qué falla.
   - **2:** opción no válida (por ejemplo, sin `--measurement`).
   - **3:** falta la grabación de un par que es puerta, o una está desligada.

3. Commitea `benchmark/measurements.jsonl` con las grabaciones. Nunca
   reescribas ni borres sus líneas.

Si no pasa, no se ajustan prompts ni criterios con lo visto en la reservada.
Se ajusta con la de desarrollo y se vuelve a medir; la nueva mirada queda
anotada.

## 8. Variantes reales de Autarqui fuera del repo (U3)

`--cases` acepta un banco fuera del repo, con la misma disposición
(`profiles/`, `cases/dev/`, `recordings/`), y todas las órdenes funcionan
igual:

```bash
uv run ens-evidence benchmark validate --cases ~/ens-banco-autarqui
uv run ens-evidence benchmark build dev/<caso> --cases ~/ens-banco-autarqui
uv run ens-evidence benchmark prepare --cases ~/ens-banco-autarqui \
  --provider claude-code --model claude-opus-5-5
```

Hoy **no sirve para datos reales**:

- la base `workspace:` (copia de un workspace real) se rechaza si el banco
  está dentro de un repo git, y fuera de él el builder aún no la construye
  («las bases `workspace:` aún no se construyen»);
- un perfil tiene que pasar la heurística de datos ficticios, también fuera
  del repo, y `record` no escribe una respuesta que cite un dato detectable.

Lo que sí se puede: escribir en ese directorio variantes ficticias que no
deban estar en el repo. Las grabaciones y mediciones de ese banco se quedan
allí; no se copian a `benchmark/`.

## Deshacer

- Las grabaciones, la línea base y los casos son ficheros en git: se
  deshacen con `git restore` o revirtiendo el commit.
- `benchmark/measurements.jsonl` es de solo adición: nunca se reescribe. Una
  medición hecha por error se deja y se explica en el PR.
- `build` (sin `--out`), `prepare`, `record` y `run` construyen en
  directorios temporales y no tocan ningún workspace real.

## Límites

- Las grabaciones no van firmadas: protege la revisión del diff.
- La repetición usa el destino grabado; no lo compara con la CLI instalada.
- No se puede sembrar una exclusión débil de una medida exigida en BÁSICA:
  el producto solo la admite con `absent_assets` y la mutación no lo fija.
- El 100 % determinista cubre solo las reglas que siembra el banco;
  `DOSSIER_INTEGRITY` queda fuera.
