# 0013 — Procedimiento y registro de incidentes

- Estado: aceptada
- Fecha: 2026-10-31

## Contexto

En categoría BÁSICA aplican op.exp.7 (gestión de incidentes) y op.exp.9
(registro de la gestión de incidentes) del anexo II del RD 311/2022. Hay
además dos artículos que la afectan:

- **Art. 25.1:** exige disponer de procedimientos de gestión de incidentes
  «de acuerdo con lo previsto en el artículo 33» y la ITS correspondiente.
- **Art. 25.2:** pide mecanismos de detección, criterios de clasificación,
  análisis y resolución, cauces de comunicación y el registro de las
  actuaciones para la mejora continua.
- **Art. 33.7:** obliga a las organizaciones del sector privado que prestan
  servicios a entidades públicas a notificar al INCIBE-CERT «los incidentes que
  les afecten». No deja a la organización decidir qué niveles se notifican; los
  umbrales y plazos dependen de la ITS, que no está verificada.

El registro de incidentes es una evidencia sensible. Si se pudiera editar sin
dejar rastro, no serviría para acreditar nada.

## Decisión

- **El procedimiento es un documento organizativo** (`procedimiento-incidentes`,
  ADR 0010). Se añade a las plantillas v1, porque añadir una plantilla no
  cambia la versión; modificar una existente sí. Lo aprueba la autoridad
  declarada en `autoridad_procedimientos`.
- **Qué cubre el procedimiento:** op.exp.7.1, op.exp.7.2, art. 25.2 y art.
  33.7. El art. 25.1 se cita pero no se cubre, porque remite a una ITS que la
  plantilla no recoge.
- **Qué no se cubre con documentos:** op.exp.9 nunca se da por cubierto con
  un documento. Un test fija que ninguna plantilla lo cubre.
- **Articulado:** se amplía con los arts. 25.1, 25.2 y 33.7, en texto literal
  del BOE. La revisión humana del articulado ampliado está firmada. Cambiar el
  articulado deja obsoletas las aprobaciones de los documentos, que hay que
  generar y aprobar de nuevo.
- **Sin taxonomías inventadas:**
  - Los niveles de clasificación los declara la organización en el
    procedimiento (nivel, criterio y escalado). No hay categorías.
  - Los tipos de actuación (emergencia, cambio del sistema, notificación,
    otra) son vocabulario del producto, no del RD.
  - La herramienta no fija plazos ni contenido de las notificaciones.
- **Registro de solo adición:**
  - Cada `incident new|update|close` escribe un registro nuevo en
    `incidents/<id>/<seq>-<instante>-<sha12>.yaml`, que corresponde al
    reporte inicial, intermedio o final de op.exp.9.1.
  - Cada registro va encadenado con la huella del anterior y su número de
    secuencia se reserva antes de escribir, de forma exclusiva.
  - Los mismos bytes se importan como evidencia `incident-record`.
  - Se considera roto, y bloquea tanto nuevas escrituras como la cobertura,
    cualquiera de estos casos:
    - un registro cuya clave no coincide con su contenido;
    - un hueco o una bifurcación en la secuencia;
    - una evidencia sin su registro, o una reserva sin registro;
    - una referencia rota.
  - `incident verify` informa además de los huecos en la numeración
    (`INC-AAAA-NNN` es correlativa) y de los activos de incidente no válidos;
    no bloquean la cobertura de los demás incidentes.
- **Historial al cerrar:**
  - El cierre exige la conclusión, la relevancia jurisdiccional (op.exp.9.2) y
    el resultado de revisar los eventos auditables (op.exp.9.3).
  - Si hay relevancia jurisdiccional, exige también quién da el asesoramiento
    legal.
  - Al cerrar se genera la evidencia `incident-history` con la lista exacta de
    registros. Es la única fuente que la matriz acepta para las cláusulas de
    op.exp.9 (`matrix/policy.py`).
  - La matriz verifica ese historial en cada evaluación. Solo la revisión
    humana implanta.
  - El mismo historial se vincula a op.exp.7.1 (`--covers op.exp.7.1`) junto
    al procedimiento, que cubre op.exp.7.1 y op.exp.7.2.
  - Si el cierre queda escrito y el historial no se genera, el cierre no se
    rechaza: es definitivo. La orden lo dice, sale con error y
    `incident history` completa el historial sin tocar ningún registro.
- **Simulacro:** se marca con `--simulated` y con el prefijo `SIM-`. Exige el
  procedimiento aprobado y vigente, y la matriz lo muestra como «simulacro» en
  `status`, en `matrix review` (con un aviso) y en las filas, las revisiones y
  las fuentes del export.
- **Minimización:** solo se admiten los campos de una lista cerrada y se
  validan antes de escribir nada:
  - se rechazan los secretos y los identificadores personales (correo,
    DNI/NIE, IBAN, teléfono) en todos los campos, incluidos `assets`,
    `alert_ids` y `--by`;
  - las IP se admiten con aviso;
  - la detección es estructural y conservadora, no por formato: se rechaza
    cualquier secuencia de 9 o más dígitos aunque la separen espacios,
    puntos, guiones, barras, paréntesis, `+` o `:`. Solo se admite, si el
    tramo completo encaja, una IP válida (con aviso), una IP con puerto
    (`IPv4:puerto` o `[IPv6]:puerto`, IP válida y puerto 1-65535; mismo
    aviso) o un instante ISO 8601. Hay dos excepciones más: en
    `source.alert_ids`, y solo ahí, se admite el id de alerta de Wazuh con
    formato exacto `^\d{10}\.\d{1,10}$` (epoch con fracción; el mismo número
    en texto libre u otro campo se rechaza), y los ids propios (`INC-…`,
    `SIM-…`, `EV-…`) no cuentan. Puede rechazar textos con números largos
    (un id de alerta numérico fuera de ese formato, un hash con 9 dígitos
    seguidos); se describen por referencia. IBAN,
    DNI/NIE y correo se buscan sin distinguir mayúsculas y con separadores;
    las credenciales admiten espacios alrededor de `=` y `:`, y un valor
    entrecomillado se analiza completo;
  - no se adjuntan logs;
  - los errores nombran el campo, nunca el valor.
- `incidents/` no se publica con `workspace push`; entra en la copia restic.

## Consecuencias

- Sin incidentes ni simulacros dentro de la periodicidad de la medida,
  op.exp.9 queda bloqueada. La organización tiene que planificar simulacros.
- Hay dos manipulaciones que no se detectan en local; solo las detecta la
  copia externa (ADR 0008):
  - borrar a la vez el último registro y su evidencia;
  - borrar el último incidente de la numeración con sus evidencias (un hueco
    en medio sí se detecta);
  - alterar de forma coordinada los registros y sus evidencias.
- Las comprobaciones de datos personales son heurísticas y pueden dar falsos
  positivos.
