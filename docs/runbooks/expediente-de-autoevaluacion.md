# Expediente de autoevaluación

Cómo generar el expediente, recorrer sus estados y, solo en BÁSICA, registrar
la declaración de conformidad que hace la dirección. Contrato:
[ADR 0015](../decisions/0015-expediente-de-autoevaluacion.md).

Antes de empezar:

- la categorización tiene que estar vigente;
- la Declaración de Aplicabilidad tiene que estar firmada;
- conviene tener la matriz revisada ([matriz](matriz-de-cumplimiento.md)), los
  documentos organizativos aprobados y los checks ejecutados.

## 1. Genera el expediente

```bash
ens-evidence assessment dossier generate     # imprime el id
ens-evidence assessment dossier show <id>    # las 11 secciones con sus fuentes
```

El expediente es una foto del estado actual. Tiene 11 secciones:

1. identificación;
2. alcance;
3. categorización;
4. riesgos;
5. Declaración de Aplicabilidad;
6. metodología;
7. resultado por medida;
8. evidencias;
9. incumplimientos y tratamiento;
10. conclusiones;
11. aprobación.

Cada sección cita sus fuentes con su huella y su fecha. Se puede generar aunque
el sistema todavía no esté preparado. En ese caso `generate` y `status` lo
avisan: el expediente sirve para la autoevaluación y el análisis (anexo III,
2.1.b), no para declarar. Si quieres declarar, resuelve los bloqueos y genera
uno nuevo (ver «Si no puedes declarar»).

Las secciones con estado INCOMPLETE (por ejemplo, riesgos sin registro) se
listan con su número y nombre en `generate` y `status`.

En MEDIA o ALTA se genera también un paquete por medida para el auditor
(`evidence/assessments/<id>/package/`). En BÁSICA se pide con
`ens-evidence assessment dossier package <id>`.

## 2. Autoevaluación

```bash
ens-evidence assessment dossier self-assess <id> --by "Ana Pérez" --role system_administrator
```

La registra el administrador del sistema o su delegado (anexo III, 2.1.a).
Todas las medidas tienen que tener su revisión en la matriz.

## 3. Análisis del responsable de la seguridad

```bash
ens-evidence assessment dossier analyze <id> --by "Lucía Navarro Gil" \
  --conclusion "…" --elevated-to "Hugo Serrano Ortiz" --elevated \
  --manual-judgment-review [--corrective-measure "…"]
```

- Lo registra la persona designada como responsable de la seguridad.
- Las conclusiones se elevan a la persona designada como responsable del
  sistema (2.1.b), aunque no haya medidas correctoras. La herramienta no
  envía nada: `--elevated` confirma que lo has hecho tú.
- `--manual-judgment-review` confirma que una persona ha revisado lo que la
  herramienta no puede valorar sola. Mientras no exista la pre-auditoría, sin
  esta confirmación no se puede declarar: en BÁSICA `analyze` la exige.
  El análisis no se puede repetir.

## 4. Declaración (solo BÁSICA)

```bash
ens-evidence assessment dossier declare <id> --by "Marta Ibáñez Soler" \
  [--evidence EV-…] [--acknowledge-incomplete]
```

- La registra la dirección, con la persona declarada en `firmante_direccion`.
- Exige que el sistema esté preparado al generar el expediente y también
  ahora.
- Si el expediente tiene secciones INCOMPLETE, `declare` se rechaza y las
  lista con su número y nombre. La dirección puede completarlas en un
  expediente nuevo o reconocerlas con `--acknowledge-incomplete`; el
  reconocimiento queda en el registro DECLARED (campo
  `acknowledged_incomplete`, con los números de sección) y no se puede
  reconocer una parte. El expediente (sección 11) también lo muestra en el
  bloque DECLARED.
- La herramienta solo registra el acto: no redacta la declaración ni genera
  el distintivo.
- Con `--evidence` enlazas el documento firmado que hayas importado. La
  herramienta comprueba que no ha cambiado; la firma, el formato PDF-A y que
  corresponda a este expediente los revisa una persona.

En MEDIA o ALTA no hay declaración: hace falta una auditoría de certificación
(art. 38.1). La autoevaluación y el análisis se pueden registrar como revisión
interna.

## 5. Estado, cambios y verificación

```bash
ens-evidence assessment dossier status <id>   # estado, siguiente paso y cambios desde la generación
ens-evidence assessment dossier verify <id>   # reproduce el expediente y comprueba cadena, derivados y paquete
```

- Si después de generar el expediente cambia algo organizativo (la matriz,
  los riesgos, la Declaración, los documentos…), `status` dice qué sección ha
  cambiado y no deja registrar más pasos: genera un expediente nuevo. Lo ya
  registrado se conserva como historia.
- Una ejecución nueva de checks que dé lo mismo no cuenta como cambio. Si un
  check falla, la declaración se rechaza.
- Si alguien edita a mano `dossier.json`, `dossier.md`, una vista guardada, el
  paquete o un registro de transición, `status` y `verify` lo señalan con un
  error visible y salen con 1. Para recuperarlo, restaura
  `evidence/assessments/` desde la copia o genera uno nuevo.
- Hay una manipulación que no se detecta en local: borrar a la vez el último
  registro de la cadena y su reserva deja el expediente en el estado anterior
  sin rastro. Solo la detecta la copia externa (ADR 0008); haz la copia antes
  de cada paso.
- Si falta `dossier.md`, `ens-evidence assessment dossier render <id>` lo
  vuelve a crear (`status` y `verify` avisan de que falta, sin cambiar su código
  de salida); en MEDIA/ALTA también el paquete si falta entero. En BÁSICA
  el paquete se vuelve a pedir con `dossier package <id>`. Nunca sobrescribe
  nada.

## 6. Si no puedes declarar

`status` explica por qué. Hay dos casos que no se arreglan en el mismo
expediente:

- **El expediente se generó sin estar preparado el sistema.** Resuelve los
  bloqueos que indica `status` (`matrix show`, `check --all`) y genera un
  expediente nuevo con `dossier generate`. Rehaz la autoevaluación y el
  análisis en el nuevo.
- **El análisis se registró sin `--manual-judgment-review`** (expedientes
  anteriores a que `analyze` la exigiera en BÁSICA). El análisis no se puede
  repetir: genera un expediente nuevo y rehaz la autoevaluación y el análisis
  con esa confirmación.

Si el motivo es que el sistema ya no está preparado, o que ha cambiado algo
organizativo, resuelve lo que indica `status` y vuelve a intentarlo, o genera
uno nuevo si dice que el expediente ya no describe el estado actual.

Tras registrar la declaración, la herramienta no da más pasos, pero el trabajo
no ha terminado: la organización debe darle publicidad (art. 38.2 del RD
311/2022, según la ITS aplicable). La herramienta no lo hace.

Los nombres son declarados; la herramienta no los autentica.
