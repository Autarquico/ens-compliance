# 0009 — Categorización, riesgos y Declaración de Aplicabilidad

- Estado: aceptada
- Fecha: 2026-10-29

## Contexto

La categoría del sistema decide qué medidas del anexo II son mínimos
exigibles y si el objetivo es la autoevaluación (BÁSICA) o la preparación
para certificación (MEDIA y ALTA). El RD 311/2022 (texto consolidado del
BOE) fija:

- **Anexo I:** cinco dimensiones (C, I, T, A, D); cada información y servicio
  se adscribe por dimensión a BAJO, MEDIO o ALTO, o a ningún nivel si no se ve
  afectada; el nivel del sistema en cada dimensión es el mayor; la categoría
  es ALTA, MEDIA o BÁSICA según el nivel más alto alcanzado (apartado 4.1), y
  se re-evalúa anualmente o ante cambios significativos (apartado 1).
- **Art. 41:** las valoraciones corresponden al responsable de la información
  o del servicio; la determinación de la categoría, al responsable de la
  seguridad.
- **Art. 14.2:** la gestión de riesgos emplea alguna metodología reconocida
  internacionalmente.
- **Art. 28:** las medidas del anexo II son mínimos exigibles, ampliables por
  el responsable de la seguridad; la relación de medidas seleccionadas se
  formaliza en la **Declaración de Aplicabilidad**, firmada por el
  responsable de la seguridad; una medida puede reemplazarse por
  compensatorias que protejan igual o mejor, con correspondencia detallada y
  aprobación formal.

## Decisión

- **Categorización** en `scope/categorization.yaml`: valoración por
  información y servicio (todos los servicios del alcance aprobado), por
  dimensión, con justificación y con quién y cuándo valora (roles
  `information_owner` y `service_owner`, ver 0002). Niveles del sistema y
  categoría se calculan, nunca se escriben a mano. La aprueba un
  `security_officer`, atada al alcance aprobado (huella, 0006) y al sha256
  del fichero, con vigencia de un año.
- **Compromiso:** BÁSICA → autoevaluación; MEDIA o ALTA → preparado para
  certificación. Es un objetivo: nunca establece `READY`, `COMPLIANT` ni
  `DECLARED` (0002).
- **Riesgos** en `risk/`: la organización declara su metodología (nombre,
  referencia y adaptación) y sus escalas; la herramienta valida coherencia y
  referencias, nunca genera valores ni juzga si la metodología es
  "reconocida". El análisis se revisa y aprueba cada año (anexo III, 1.1.d:
  "Que se ha realizado un análisis de riesgos, con revisión y aprobación
  anual"); que lo apruebe un `security_officer` es decisión del producto. La
  aprobación queda atada a la huella del alcance, la versión del catálogo y la
  categorización vigente. Quién
  acepta el riesgo residual se registra, sin restringir el rol: el RD no lo
  fija en el articulado contrastado.
- **Aplicabilidad:** el estado por medida sigue en `controls/measures.yaml`
  (0007). La herramienta propone `APPLICABLE` solo para lo que el anexo II
  exige según la categoría o el nivel de la dimensión, y nunca
  `NOT_APPLICABLE`. Una medida exigida solo puede quedar `NOT_APPLICABLE` si
  el tipo de activo al que se refiere no existe en el sistema
  (`absent_assets`: tipo de activo y justificación específica): el anexo II, 2.1.a empieza la
  selección por la "identificación de los tipos de activos presentes" y el
  art. 28.1.a manda tener en cuenta los activos del sistema. Si el activo
  existe, la medida solo puede reemplazarse por compensatorias implantadas y
  justificadas (art. 28.3); la ausencia del activo nunca es una compensación.
  Qué medidas pueden quedar sin objeto por falta de un activo, y de cuál, lo
  decide el producto, no el texto legal: es una lista cerrada en
  `src/ens_evidence/applicability/absent_asset_types.yaml` (el catálogo sale
  del BOE y no se toca, 0007). Criterio: una medida entra solo si su base y
  todos sus refuerzos tratan exclusivamente del tipo de activo nombrado, de
  modo que su ausencia deja sin objeto cada obligación; basta una cláusula
  que sobreviva sin el activo para dejarla fuera (`op.ext.3`: productos y
  componentes de la cadena de suministro, `mp.if.*`: locales donde se ubican los
  sistemas o hay equipamiento esencial, que existen aunque no haya CPD ni
  instalaciones propias, `mp.sw.1`: pruebas previas a implantar o
  modificar cualquier sistema y componentes de producto adquirido,
  `mp.info.4`: sellos cualificados y productos certificados sin atarlos a la
  evidencia electrónica). `tests/applicability/test_absent_criterion.py`
  fija por entrada las cláusulas que lo justifican y las compara con el
  catálogo. Nunca entran `org.*`, `op.pl.*`, `op.exp.*`,
  `op.cont.*`, `op.mon.*` ni `mp.per.*`: son marco o proceso, no activos que
  puedan faltar. `validate` rechaza `absent_assets` fuera de la lista, con un
  tipo ajeno o con una justificación genérica (menos de 20 caracteres, «n/a»,
  «no aplica»), y cuando el inventario desmiente la ausencia (portátil en
  `mp.eq.3`, aplicación con dominio en `mp.s.2`, proveedor en `op.ext.*`).
  La lista
  no incluye ninguna `mp.si.*` (`mp.si.3.1` cubre todos los soportes bajo
  responsabilidad de la organización, `mp.si.2.r2.1` cifra las copias de
  seguridad y `mp.si.4` el transporte de dispositivos, removibles o no) ni
  `mp.info.3`: no tratan de un activo que pueda faltar. `validate` y `approve` muestran las exclusiones y avisos antes
  de firmar.
  El expediente se ata a la categoría aprobada vigente:
  `categorization.open_dossier` lo crea con su categoría y su compromiso, y
  `require_dossier_category` rechaza un `Dossier` de otra categoría. MEDIA y
  ALTA quedan «preparado para certificación»; `DECLARED` sigue siendo solo
  BASICA (guarda de 0002).
  El anexo III, 1.1.e lo confirma: la auditoría constata el anexo II "en
  función de las condiciones de aplicación en cada caso".
- **Declaración de Aplicabilidad** en `controls/applicability.yaml`: registro
  firmado por un `security_officer` con la relación de medidas seleccionadas,
  sus refuerzos (incluido el refuerzo elegido en cada alternativa), la
  correspondencia de las compensatorias (con las obligaciones, `base` o
  refuerzos, que sustituyen cada una) y, aparte, las medidas exigidas no
  aplicables por ausencia de activos, atado al sha256
  de `controls/measures.yaml`, a la categorización aprobada, a la versión del
  catálogo y a la huella del alcance. Deja de estar vigente si cambia
  cualquiera.

## Consecuencias

- Una decisión por medida guarda en qué categoría y versión del catálogo se
  tomó; si cambian, queda pendiente de revisión.
- Ningún paso de este flujo declara conformidad: la evaluación y la revisión
  humana de cada medida siguen siendo las de 0002.
