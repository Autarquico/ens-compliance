# Pre-auditoría

Cómo revisar el expediente antes de analizarlo: una capa determinista que
corre siempre y otra de juicio que usa un modelo de lenguaje solo si das tu
consentimiento. Contrato: [ADR 0017](../decisions/0017-pre-auditoria.md).

La pre-auditoría **propone** hallazgos. No dictamina ni declara conformidad.
Cada hallazgo es una propuesta y la decide una persona.

Antes de empezar, genera el expediente
([expediente](expediente-de-autoevaluacion.md)):

```bash
ens-evidence assessment dossier generate     # imprime el id
```

## 1. Configura el proveedor y el modelo

En el fichero de ajustes (por defecto `~/.config/ens-evidence/settings.yaml`):

```yaml
workspace:
  backend: local
  local: { path: /ruta/a/mi-workspace }
preaudit:
  provider: anthropic     # anthropic | openai | claude-code | codex
  model: <modelo>         # obligatorio; no hay valor por defecto
  # base_url: https://…   # opcional, solo para anthropic y openai; solo https
  # timeout: 120          # segundos
```

- **Nunca pongas claves en los ajustes.** La herramienta rechaza el fichero si
  lo intentas.
- Con `anthropic` o `openai`, la clave va por el entorno:
  `ANTHROPIC_API_KEY` u `OPENAI_API_KEY`.
- Con `claude-code` o `codex` usas tu plan contratado. Necesitas la CLI
  (`claude` o `codex`) instalada en el `PATH` y con la sesión iniciada en tu
  equipo. La herramienta no guarda tokens.
- En la caja de Docker ([caja](caja.md)) no hay CLIs ni sesión. Allí solo
  sirven `anthropic` y `openai`.
- Con `codex` hay un riesgo extra: le quitamos la herramienta de shell, pero
  le queda una forma de ejecutar comandos que no se puede quitar y podría
  leer ficheros de tu equipo. Para enviar tendrás que aceptarlo con
  `--accept-shell-risk`.

`--provider` en `prepare` y `run` solo confirma el proveedor de los ajustes.
No lo cambia: si no coincide, da error.

## 2. Prepara el envío y revísalo

```bash
ens-evidence preaudit prepare <expediente>
```

No envía nada. Muestra el payload completo y, al final:

- el destino y el modelo;
- la versión del audit pack;
- la huella del payload (sha256).

El payload lleva solo lo que piden los criterios del pack:

- las medidas, aplicables o no, con sus enlaces a documentos y evidencias;
- las justificaciones de no aplicabilidad y de compensación;
- el texto de los documentos organizativos;
- las evidencias enlazadas a medidas aplicables: sus datos y su texto, si es
  texto y ocupa hasta 64 KiB. Si no, van sin texto.

Antes se redacta:

- secretos → `[REDACTADO:secreto]`;
- correos, teléfonos, DNI/NIE, IBAN… → `[REDACTADO:<tipo>]`;
- IP → `[IP]`;
- personas declaradas con un rol → su rol, por ejemplo
  `[responsable de la seguridad]`;
- personas que firman un registro sin rol → `[PERSONA]`.

Si después de redactar queda algo detectable, `prepare` se niega y dice dónde.

**Léelo antes de consentir.** Busca sobre todo nombres de personas que no
constan en ningún registro: esos no se redactan (ver «Límites»). Si ves algo
que no debe salir, corrígelo en el workspace, genera un expediente nuevo y
vuelve a preparar.

`prepare` se niega si el expediente no está íntegro o ya no describe el
estado actual.

## 3. Ejecuta

Solo la capa determinista, sin red:

```bash
ens-evidence preaudit run <expediente>
```

Con la capa de juicio, consintiendo la huella que mostró `prepare`:

```bash
ens-evidence preaudit run <expediente> --consent <huella> --by "Lucía Navarro Gil"
# con codex, añade --accept-shell-risk
```

- `--by` es quien consiente el envío. Queda registrado con la fecha, el
  destino y la huella.
- Solo se envía si coinciden con lo preparado la huella, el destino, el
  proveedor, el modelo y el pack. Si algo ha cambiado, `run` se niega sin
  conectarse: vuelve a preparar y a consentir.
- Hacia las API, solo HTTPS y sin seguir redirecciones.
- `run` imprime el número de la ejecución y el estado de cada capa.

La capa de juicio puede acabar así:

- `DONE`: con sus hallazgos y el número de descartados por no apoyarse en el
  expediente;
- `ERROR`: la respuesta no sirve y no deja ningún hallazgo. Sale con 1;
- `NOT_RUN`: no se pidió.

Con `ERROR` o `NOT_RUN`, el estado sigue siendo «revisión humana pendiente».

## 4. Lee los hallazgos

```bash
ens-evidence preaudit show <número>
```

Por cada hallazgo:

- la propuesta: `NC_MENOR`, `OBSERVACION` o, en la capa determinista,
  `SIN_CLASIFICAR`;
- la medida, el criterio y la capa;
- el motivo;
- las citas: documento, evidencia o sección, con el fragmento literal;
- la fuente: ITS de Auditoría §VI.2 o IC-01/19 §53;
- el id del hallazgo, que necesitas para disponerlo;
- su disposición y si bloquea;
- «Propuesta; la decide una persona».

Al final aparecen las agrupaciones propuestas (IC-01/19 §54). Por ejemplo,
menores en varias medidas de `op.acc` dan una propuesta de mayor. Los
hallazgos originales se conservan. Una persona decide si agrupa.

Los hallazgos `SIN_CLASIFICAR` indican falta de información, no un
incumplimiento. Los clasificas tú.

## 5. Dispón los hallazgos de juicio

```bash
ens-evidence preaudit review <id> --by "Lucía Navarro Gil" --decision accepted
ens-evidence preaudit review <id> --by "Lucía Navarro Gil" --decision resolved --evidence EV-…
ens-evidence preaudit review <id> --by "Lucía Navarro Gil" --decision dismissed --reason "…"
```

- **`accepted`**: lo reconoces, pero todavía no está corregido. **Sigue
  bloqueando.** Úsalo para dejar constancia mientras trabajas en él.
- **`resolved`**: ya está corregido. Cita la evidencia que lo demuestra
  (`evidence import` si es manual). La herramienta comprueba que existe y
  está íntegra.
- **`dismissed`**: no procede. Explica por qué en `--reason`. El motivo no
  puede llevar secretos ni datos personales.

Solo `resolved` y `dismissed` dejan de bloquear. Son definitivos para lo ya
propuesto.

Solo se disponen los hallazgos de juicio. Los deterministas no: sus hechos
(un check que falla, una evidencia caducada…) ya bloquean por sí mismos.
Arréglalos donde corresponde (`matrix show`, `check --all`).

Si una ejecución posterior vuelve a proponer un hallazgo ya resuelto o
descartado, se reabre: vuelve a `OPEN` y bloquea hasta que lo dispongas otra
vez. Ejecutar de nuevo nunca cierra un hallazgo.

`run` y `review` toman el lock del ciclo del workspace, como `collect`. Si hay
un ciclo en curso, salen con 2 sin escribir nada: espera a que termine y
repite.

## 6. Preparación y expediente

- Un hallazgo de juicio `NC_MENOR` sin `resolved` ni `dismissed` bloquea la
  preparación (`OPEN_FINDING`) en `assessment status`, en los informes, al
  generar el expediente y al declarar.
- El expediente revisado no cambia. Sus hallazgos afectan a la evaluación
  actual y a los expedientes que generes después.
- `assessment dossier status <id>` muestra los hallazgos de juicio sobre ese
  expediente y su estado de hoy, como vista derivada.
- Cada expediente congela las ejecuciones y disposiciones que existían al
  generarse. Una ejecución o disposición posterior no rompe `verify`.
- `--manual-judgment-review` **sigue siendo obligatorio** en `analyze` en
  BÁSICA. La pre-auditoría ayuda a la persona que revisa; no la sustituye.

## 7. Si la historia está rota

Las ejecuciones (`evidence/preaudits/runs/`) y las disposiciones
(`evidence/preaudits/dispositions/`) son registros encadenados de solo
adición. Si alguien edita, borra o desordena uno, o si un registro tiene
fecha futura:

- `run`, `show` y `review` lo señalan con un error visible;
- la preparación queda bloqueada (`preaudit_history_broken`) y no se da por
  cerrado ningún hallazgo.

Para recuperarlo, restaura `evidence/preaudits/` desde la copia
([restauración](restauracion-de-evidencias.md)). No edites los registros a
mano.

## 8. Límites

- Es una propuesta, no un dictamen. La herramienta nunca emite «favorable»
  ni declara conformidad.
- La capa de juicio no propone `NC_MAYOR`. Una mayor solo sale de una
  agrupación del §54 o de una persona.
- Los tests prueban el contrato con respuestas grabadas, no si el modelo
  acierta. La eficacia la mide `audit-benchmark`.
- Las personas que no constan en ningún registro ni en las respuestas de
  org-docs pueden aparecer por su nombre en un documento o una evidencia.
  Revisa el payload.
- Los detectores también redactan de más: algunos ids pueden salir como
  `[REDACTADO:telefono]`. No es una fuga, pero empobrece las citas.
- En local los ficheros se guardan con permisos 600. En S3 los protege el
  bucket.
- Los nombres son declarados; la herramienta no los autentica.
