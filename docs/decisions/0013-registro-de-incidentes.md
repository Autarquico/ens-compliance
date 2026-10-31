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
  servicios a entidades públicas a notificar sus incidentes al INCIBE-CERT.

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
- **Simulacro:** se marca con `--simulated` y con el prefijo `SIM-`. Exige el
  procedimiento aprobado y vigente, y la matriz lo muestra como «simulacro».
- **Minimización:** solo se admiten los campos de una lista cerrada y se
  validan antes de escribir nada:
  - se rechazan los secretos y los identificadores personales (correo,
    DNI/NIE, IBAN, teléfono);
  - las IP se admiten con aviso;
  - no se adjuntan logs;
  - los errores nombran el campo, nunca el valor.
- `incidents/` no se publica con `workspace push`; entra en la copia restic.

## Consecuencias

- Sin incidentes ni simulacros dentro de la periodicidad de la medida,
  op.exp.9 queda bloqueada. La organización tiene que planificar simulacros.
- Hay dos manipulaciones que no se detectan en local; solo las detecta la
  copia externa (ADR 0008):
  - borrar a la vez el último registro y su evidencia;
  - alterar de forma coordinada los registros y sus evidencias.
- Las comprobaciones de datos personales son heurísticas y pueden dar falsos
  positivos.
