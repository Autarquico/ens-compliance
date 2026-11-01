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
el sistema todavía no esté preparado: el expediente lo dice y sirve para el
análisis.

En MEDIA o ALTA se genera también un paquete por medida para el auditor
(`evidence/assessments/<id>/package/`). En BÁSICA se pide con
`ens-evidence assessment dossier package <id>`.

## 2. Autoevaluación

```bash
ens-evidence assessment dossier self-assess <id> --by "Hugo Serrano Ortiz" --role system_administrator
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
  esta confirmación no se puede declarar.

## 4. Declaración (solo BÁSICA)

```bash
ens-evidence assessment dossier declare <id> --by "Marta Ibáñez Soler" [--evidence EV-…]
```

- La registra la dirección, con la persona declarada en `firmante_direccion`.
- Exige que el sistema esté preparado al generar el expediente y también
  ahora.
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
ens-evidence assessment dossier verify <id>   # reproduce el expediente byte a byte
```

- Si después de generar el expediente cambia algo organizativo (la matriz,
  los riesgos, la Declaración, los documentos…), `status` dice qué sección ha
  cambiado y no deja registrar más pasos: genera un expediente nuevo. Lo ya
  registrado se conserva como historia.
- Una ejecución nueva de checks que dé lo mismo no cuenta como cambio. Si un
  check falla, la declaración se rechaza.
- Si alguien edita a mano el expediente o un registro de transición, el
  expediente queda bloqueado con un error visible. Para recuperarlo, restaura
  `evidence/assessments/` desde la copia o genera uno nuevo.
- Si falta el Markdown o el paquete, `ens-evidence assessment dossier render <id>`
  lo vuelve a crear. Nunca sobrescribe nada.

Los nombres son declarados; la herramienta no los autentica.
