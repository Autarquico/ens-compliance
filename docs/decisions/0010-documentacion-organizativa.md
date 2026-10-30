# 0010 — Documentación organizativa desde plantillas y respuestas

- Estado: aceptada
- Fecha: 2026-10-30

## Contexto

La mayor parte del trabajo de una pyme en el ENS es documental: política,
normativa, procedimientos, proceso de autorización y designación de
responsables. El riesgo es la documentación "de papel": textos genéricos que
nadie aplica. El RD 311/2022 (texto consolidado del BOE) fija el contenido
mínimo y quién aprueba:

- **Anexo II, org.1–org.4:** contenido de la política (org.1.1–1.5), la
  normativa (org.2.1–2.2), los procedimientos (org.3.1–3.4) y el proceso de
  autorización (org.4.1–4.8).
- **Art. 12.1:** contenido mínimo de la política, incluida (f) los riesgos del
  tratamiento de datos personales. **Art. 11.3:** atribuciones, coordinación y
  resolución de conflictos. **Art. 13.2, 13.3 y 13.5:** funciones de los
  responsables, separación entre responsable de la seguridad y del sistema (y
  su excepción) y punto de contacto de los servicios prestados.
- **Art. 2.3:** en entidades privadas, la política la aprueba el órgano con las
  máximas competencias ejecutivas. **Art. 15.2:** las normas de seguridad las
  aprueba la dirección o el órgano superior.

## Decisión

- **Plantillas versionadas** en el producto (`src/ens_evidence/orgdocs/templates/`):
  política, designación de responsables, normativa, procedimientos y proceso
  de autorización. La plantilla solo aporta estructura y frases de enlace; el
  contenido sale de las respuestas de la organización en `org/answers.yaml`.
- **Trazabilidad por cláusula:** cada sección declara las cláusulas que cubre.
  Las del anexo II se leen del catálogo (ADR 0007); las del articulado y del
  anexo III, de una fuente revisada empaquetada
  (`orgdocs/sources/articulado.yaml`, con URL, fecha de consolidación y
  sha256, y revisión humana ligada a ese hash como la del catálogo). Nunca se
  copian a mano en las plantillas.
- **Sin relleno:** una respuesta obligatoria vacía o de relleno inequívoco
  ("n/a", "por definir"…) detiene la generación y nombra la pregunta; no se
  escribe nada. Un "no aplica" solo existe donde la pregunta lo admite y con
  motivo. Otras sospechas son avisos para la revisión humana.
- **Aprobación:** política, normativa y designación de responsables, la
  dirección (rol `management`, arts. 2.3 y 15.2; la designación es decisión
  del producto); procedimientos y proceso de autorización, el rol que la
  organización declare en castellano (`la dirección`, `el responsable de la
  seguridad`, `el responsable del sistema`; el dominio las traduce al rol).
  Quien firma debe ser la persona declarada para ese rol, comparada por nombre
  completo (sin tildes ni mayúsculas, nunca por fragmentos): la dirección puede
  ser un órgano, así que firma la persona de la pregunta `firmante_direccion`,
  que ha de figurar en `personas`. Los ids de pregunta y opciones de los que
  depende el motor están en `orgdocs/contract.py` y la carga de plantillas
  falla si faltan. La aprobación registra
  persona, rol, fecha y los sha256 del documento y de sus entradas (respuestas,
  plantilla, catálogo, fuente, huella del alcance) y deja de estar vigente si
  cambia cualquiera; un build idéntico la conserva. Se conserva el historial.
- **Cobertura no es implantación:** aprobar un documento nunca cambia la
  evaluación de ninguna medida. `approved_coverage()` dice qué documento
  aprobado y vigente cubre cada cláusula; lo consultarán la matriz
  (`compliance-matrix`, task T4) y el expediente (`assessment-dossier`).
- **Vigencia del firmante:** la aprobación deja de estar vigente si quien
  firmó deja de ser la persona declarada para ese rol, o si cambia el rol que
  aprueba el documento, aunque el texto no cambie.

## Consecuencias

- Cambiar el texto legal empaquetado exige una revisión humana nueva.
- La organización revisa y aprueba documentos que describen su sistema real;
  la herramienta no redacta contenido sustantivo por ella.
