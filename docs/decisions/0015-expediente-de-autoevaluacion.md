# 0015 — Expediente de autoevaluación y sus estados

- Estado: aceptada
- Fecha: 2026-11-01

## Contexto

El anexo III, 2.1 del RD 311/2022 regula la autoevaluación de categoría
BÁSICA:

- «Bastará una autoevaluación realizada por el mismo personal que administra
  el sistema de información o en quien éste delegue», documentada medida a
  medida con sus evidencias (2.1.a).
- Sus informes «serán analizados por el responsable de la seguridad
  competente, que elevará las conclusiones al responsable del sistema para
  que adopte las medidas correctoras adecuadas» (2.1.b).

El art. 38.1 reserva la declaración de conformidad a BÁSICA y exige auditoría
de certificación en MEDIA y ALTA; los detalles los remite a una Instrucción
Técnica de Seguridad, que no hemos verificado. El RD no trae ningún modelo de
texto de declaración ni fija qué rol la firma. El anexo IV define el
distintivo como un PDF-A firmado por la entidad, con un enlace a la
declaración.

La guía (`docs/ens-compliance-guide.md:943-959`) fija las 11 secciones del
expediente.

## Decisión

- **Instantánea inmutable.** El expediente es una instantánea con las 11
  secciones. Cada sección cita sus fuentes con clave, sha256 e instante. Se
  guarda como solo adición en
  `evidence/assessments/<instante>-<sha12>/dossier.json`, con un Markdown
  derivado. Su huella es la que firman las transiciones. Las secciones 10
  (conclusiones) y 11 (aprobación) se completan en una vista derivada de las
  transiciones.
- **Reproducible.** La instantánea guarda sus entradas congeladas: instantes,
  reloj de referencia y versiones del método, del producto y del catálogo.
  Con ellas se reconstruye byte a byte.
- **Deriva.** Se calcula sobre una proyección que conserva todo lo
  organizativo (alcance, categorización, riesgos, Declaración, enlaces y
  revisiones de la matriz, documentos, incidentes, plan de tratamiento) y
  excluye los metadatos temporales y los resultados de evaluación. Cualquier
  deriva bloquea nuevas transiciones: hay que generar un expediente nuevo.
  Las transiciones ya registradas se conservan como historia.
- **Estados.** Se reutiliza el modelo del ADR 0002, con guardas añadidas:
  - todas las transiciones llevan la misma huella;
  - el tiempo no retrocede;
  - SELF_ASSESSED: administrador del sistema o su delegado, con una revisión
    en cada medida;
  - ANALYZED: lo registra la persona designada como responsable de la
    seguridad. Incluye las conclusiones y la elevación al responsable del
    sistema (persona, fecha y confirmación), aunque no haya medidas
    correctoras, más la revisión manual del juicio mientras no exista la
    pre-auditoría;
  - DECLARED: solo en BÁSICA. Lo registra la dirección, firmando la persona
    declarada en `firmante_direccion`. Exige READY en la instantánea y en la
    evaluación actual, sin errores, calculado por el servicio y nunca
    aportado por quien llama. Cita la huella del registro ANALYZED.
- **Incidentes y checks.** Del registro de incidentes solo derivan los
  incidentes cerrados. El historial de checks se reproduce congelado al
  instante de referencia guardado en la instantánea: las ejecuciones
  posteriores (`check --all`) no rompen `verify` ni cuentan como deriva por sí
  solas.
- **Declaración.** La herramienta solo registra el acto humano. No genera
  texto ni distintivo. Se puede enlazar la evidencia del documento firmado
  que importe la organización; la herramienta verifica su integridad, y la
  firma, el formato PDF-A y la vinculación los revisa una persona.
- **MEDIA y ALTA.** Aviso de auditoría de certificación (art. 38.1), sin
  declaración posible. La autoevaluación y el análisis se admiten como
  «revisión interna; no sustituye a la auditoría de certificación». Se
  genera un paquete por medida para el auditor, con un índice cerrado de
  huellas; los artefactos se referencian, no se copian.
- **Cadena de transiciones.** Igual que en el registro de incidentes (ADR
  0013): cada transición reserva su número de secuencia de forma exclusiva y
  queda en un registro encadenado con el anterior. Cualquier inconsistencia
  bloquea el expediente con un error visible: un registro cuya clave no
  coincide con el contenido, un hueco, un duplicado, una reserva sin
  registro.
- **Secretos.** Los detectores de credenciales y datos personales son los
  mismos que en el registro de incidentes (`privacy_text.py`). Se aplican al
  escribir y al leer el expediente, que será la entrada de la pre-auditoría.
- **Sin caducidad.** Ni la autoevaluación ni la declaración caducan; solo se
  muestran sus fechas. Cada medida sigue caducando por su propia revisión.

## Consecuencias

- Una actualización del producto o del catálogo invalida los expedientes en
  curso: hay que generar uno nuevo.
- Cada transición reconstruye la instantánea completa para comprobar la
  deriva. Es lento con muchas medidas, pero seguro.
- Hay una manipulación que no se detecta en local y solo detecta la copia
  externa (ADR 0008): borrar a la vez el último registro de la cadena y su
  reserva. El expediente vuelve al estado anterior y el análisis o la
  declaración se pueden rehacer sin rastro. Un hueco en medio, un registro
  editado o una reserva sin registro sí se detectan.
- `dossier.md` y las vistas guardadas con `show --save` son derivados: `verify`
  y `status` los comparan byte a byte con lo que sale de `dossier.json` y la
  cadena, y bloquean el expediente si alguien los edita.
- Los nombres son declarados, no autenticados. La autenticación llega con la
  UI (v0.2).
