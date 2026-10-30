# Categorización, riesgos y Declaración de Aplicabilidad

Cómo pasar del alcance aprobado a la Declaración de Aplicabilidad firmada.
Contrato: [ADR 0009](../decisions/0009-categorizacion-riesgos-y-aplicabilidad.md).
Fuente legal: RD 311/2022, anexo I y arts. 11, 14, 28 y 41 (texto consolidado
del BOE).

Requisito previo: el alcance aprobado (`ens-evidence scope status`). Cada paso
lo comprueba en el momento; si el alcance cambia o se revoca, lo que dependía
de él deja de estar vigente.

## 1. Categorización

```bash
ens-evidence categorization init      # plantilla con los servicios del alcance
```

Edita `scope/categorization.yaml`:

- Añade las **informaciones** que trata el sistema y valora cada información
  y cada servicio en las cinco dimensiones (C, I, T, A, D): `BAJO`, `MEDIO`,
  `ALTO`, o `not_affected` si esa dimensión no se ve afectada. Cada valor
  lleva su justificación con los criterios de perjuicio del anexo I, 3
  (limitado, grave, muy grave).
- Valora la información su **responsable de la información** y el servicio su
  **responsable del servicio** (art. 41.1). En una pyme puede ser la misma
  persona: queda registrado con qué rol valora.
- Ojo con los arts. 11.2 y 13.3: la responsabilidad de la seguridad "estará
  diferenciada de la responsabilidad sobre la explotación de los sistemas", y
  el responsable de la seguridad "será distinto del responsable del sistema,
  no debiendo existir dependencia jerárquica entre ambos". Solo en
  situaciones excepcionales, por ausencia justificada de recursos, pueden
  coincidir, y entonces hay que aplicar medidas compensatorias. Esa excepción
  se documenta en la designación de responsables (documentación
  organizativa); esta parte de la herramienta no la comprueba.

La herramienta calcula el nivel del sistema en cada dimensión (el mayor) y la
categoría (anexo I, 4.1). Después, el **responsable de la seguridad** la
determina (art. 41.2):

```bash
ens-evidence categorization approve --by "Nombre Apellido"
ens-evidence categorization status
```

La aprobación vale un año (anexo I, 1: re-evaluación anual) y deja de valer si
cambia el fichero o el alcance. Si hay un cambio significativo que no toca el
inventario, edita la valoración y vuelve a aprobar.

`status` muestra el compromiso: BÁSICA → autoevaluación; MEDIA o ALTA →
preparado para certificación. Es el objetivo del expediente, no una
declaración de nada.

## 2. Análisis de riesgos

```bash
ens-evidence risk init
```

- `risk/methodology.yaml` y `risk/methodology.md`: declara la metodología
  (nombre, referencia y cómo la adaptas). El art. 14.2 pide "alguna
  metodología reconocida internacionalmente"; la plantilla trae una propuesta
  de escalas que puedes cambiar, y la herramienta no juzga cuál usas.
- `risk/register.yaml`: un riesgo por amenaza sobre activos o servicios del
  inventario, con valoración, probabilidad, impacto, riesgo inherente (según
  tu matriz), salvaguardas (medidas del catálogo), riesgo residual,
  tratamiento y, siempre que quede riesgo residual, quién lo acepta.
- `risk/treatment-plan.yaml`: acciones, responsable, fecha y estado.

```bash
ens-evidence risk validate
ens-evidence risk approve --by "Nombre Apellido"   # responsable de la seguridad, anual
ens-evidence risk status
```

`risk approve` exige alcance y categorización aprobados y vigentes, y guarda
la huella de la categorización; si esta cambia o caduca, la aprobación queda
obsoleta.

La herramienta nunca pone valores de riesgo: los decide la organización.

## 3. Aplicabilidad y Declaración

```bash
ens-evidence controls init            # si aún no existe controls/measures.yaml
ens-evidence applicability propose
```

`propose` marca `APPLICABLE` lo que el anexo II exige para tu categoría (o
para el nivel de cada dimensión, en las medidas que van por dimensión), con
sus refuerzos. Nunca marca `NOT_APPLICABLE` ni toca tus decisiones.

Completa `controls/measures.yaml`: el resto de medidas, con estado y
justificación. Cada decisión propia se confirma con
`ens-evidence applicability propose --confirm <id>`, que le registra el
fundamento vigente; hasta entonces `validate` la da por «decisión sin
registrar». Una medida exigida solo tiene tres salidas:

- **La aplicas:** `APPLICABLE`. Si el anexo II da refuerzos alternativos
  (`R1 o R2 o …`), indica en `chosen_reinforcements` cuál adoptas en cada
  grupo.
- **La reemplazas:** `APPLICABLE` con `compensation`: las medidas
  compensatorias que tienes implantadas, en `replaces` qué obligaciones
  sustituyen (`base` y/o refuerzos como `R4`) y por qué protegen igual o mejor
  (art. 28.3). Citar un refuerzo de un grupo alternativo (`R2 o R3`) sustituye
  la elección de ese grupo; los grupos que no cites siguen exigiendo
  `chosen_reinforcements`. La Declaración muestra qué sustituye cada
  compensatoria.
- **El tipo de activo no existe en tu sistema:** `NOT_APPLICABLE` con
  `absent_assets` (anexo II, 2.1.a):

  ```yaml
  absent_assets:
    asset_type: usuarios externos
    justification: No hay usuarios externos; el sistema no expone servicios a terceros.
  ```

  Solo vale para las medidas de una lista cerrada que fija el producto
  (`src/ens_evidence/applicability/absent_asset_types.yaml`; no es texto
  legal): `op.acc.5`, `op.ext.1`, `op.ext.2`, `op.ext.4`, `op.nub.1`, `mp.eq.3`, `mp.eq.4`, `mp.info.1`, `mp.s.1` y `mp.s.2`. Criterio: solo entra
  una medida cuya base y todos sus refuerzos tratan exclusivamente de ese tipo de activo.
  Quedan fuera `mp.si.*` (la custodia, el cifrado de copias y el transporte
  alcanzan también a soportes y copias que no son removibles), `mp.info.3`, `op.ext.3`,
  `mp.if.*` (hablan de los locales donde se ubican los sistemas, que existen aunque no haya CPD), `mp.sw.1` y `mp.info.4` (alguna cláusula sobrevive sin el activo):
  se aplican o se compensan. Nunca las organizativas ni de proceso (`org.*`, `op.pl.*`,
  `op.exp.*`, `op.cont.*`, `op.mon.*`, `mp.per.*`). `asset_type` es el de la
  lista de esa medida, la justificación tiene al menos 20 caracteres y no
  puede ser genérica («n/a», «no aplica»), y el inventario no puede
  contradecirla (p. ej. un portátil en `scope/assets.yaml` impide excluir
  `mp.eq.3`). `validate` y `approve` listan estas exclusiones antes de firmar:
  revísalas. Que no exista el activo no es una compensación.

Las salvaguardas que citas en `risk/register.yaml` tienen que estar
seleccionadas.

Si cambian la categoría o el catálogo, tus decisiones sobre las medidas
afectadas quedan **pendientes de revisión**. Revísalas y, si siguen valiendo,
confírmalas con `ens-evidence applicability propose --confirm <id>` (una por
medida); `propose` nunca las cambia por su cuenta.

```bash
ens-evidence applicability validate
ens-evidence applicability approve --by "Nombre Apellido"   # responsable de la seguridad
ens-evidence applicability status
```

`approve` firma la **Declaración de Aplicabilidad** (`controls/applicability.yaml`,
art. 28.2). Deja de estar vigente si cambian `controls/measures.yaml`, la
categorización aprobada, la versión del catálogo o el alcance.

Si algo deja de estar vigente, vuelve a aprobar en este orden: alcance
(`scope approve`), categorización (`categorization approve`), riesgos
(`risk approve`), y después `applicability propose`, `validate` y
`approve`.

Nada de esto declara conformidad: la evaluación de cada medida, con su
evidencia y su revisión, es la del expediente.
