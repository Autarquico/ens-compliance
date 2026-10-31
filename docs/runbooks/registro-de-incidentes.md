# Procedimiento y registro de incidentes

Cómo documentar el procedimiento de gestión de incidentes y llevar un
registro verificable de cada incidente. Lo exigen op.exp.7 y op.exp.9 del
anexo II y los arts. 25 y 33.7 del RD 311/2022.

Contrato: [ADR 0013](../decisions/0013-registro-de-incidentes.md).

## 1. Redacta y aprueba el procedimiento

El procedimiento es un documento más de la documentación organizativa
([guía](documentacion-organizativa.md)). Responde en `org/answers.yaml` las
preguntas nuevas:

- Cómo detectáis los incidentes.
- Vuestros niveles de clasificación: nivel, criterio y a quién se escala.
- Cómo se analizan y resuelven.
- A quién se comunica cada incidente.
- Cómo se tienen en cuenta el RGPD y la LOPDGDD.
- Quién decide qué evidencia preservar y quién da el asesoramiento legal.
- Cuándo se revisan los eventos auditables.
- Si prestáis servicios a entidades públicas: cómo notificáis al INCIBE-CERT,
  quién lo hace y en qué momento del procedimiento. El art. 33.7 obliga a
  notificar los incidentes que os afecten; la herramienta no decide cuáles se
  notifican ni fija plazos o contenido, porque dependen de la Instrucción
  Técnica de Seguridad aplicable, que no ha verificado.
- Si usáis el registro de esta herramienta (`ens-evidence incident`),
  indicadlo al describir el registro: es el único que la matriz acepta para
  op.exp.9.

Después genera y aprueba:

```bash
ens-evidence docs build
ens-evidence docs approve procedimiento-incidentes --by "Nombre Apellido" --role <autoridad declarada>
```

Si el texto legal empaquetado ha cambiado (en esta versión se añadieron los
arts. 25 y 33.7), las aprobaciones anteriores quedan obsoletas y
`docs status` lo indica. Vuelve a generar y a aprobar cada documento.

## 2. Registra los incidentes

```bash
ens-evidence incident new --title "Phishing a cuentas de administración" \
  --detected-at 2026-11-03T09:12:00+01:00 --by "Hugo Serrano Ortiz" \
  --severity alto --asset workspace --personal-data unknown
ens-evidence incident update INC-2026-001 --by "Hugo Serrano Ortiz" \
  --action "Bloqueo de las cuentas afectadas" --action-kind emergency
ens-evidence incident close INC-2026-001 --by "Hugo Serrano Ortiz" \
  --conclusion "Credenciales robadas por phishing; MFA reforzado." \
  --auditable-events-result changed \
  --auditable-events-detail "Se registran ahora los inicios de sesión fallidos." \
  --legal-relevance no
```

- Cada orden escribe un registro nuevo que no se modifica nunca: es el
  reporte inicial, intermedio o final que pide op.exp.9.1.
- Cada registro se guarda también como evidencia con su huella.
- El cierre es definitivo y genera el **historial** del incidente, una
  evidencia `EV-…`. La herramienta te muestra qué comandos usar para
  vincularlo a op.exp.9 y a op.exp.7.
- Para cerrar hay que haber decidido si el incidente afecta a datos
  personales (sí o no): `incident update INC-… --personal-data yes|no`. Si
  tiene relevancia jurisdiccional, indica quién da el asesoramiento legal y
  qué evidencias se preservan.

Qué **no** escribir: datos personales (correos, DNI/NIE, IBAN, teléfonos),
claves ni logs. Describe a las personas por su rol y los datos por referencia,
por ejemplo «ticket 123». La herramienta rechaza esos datos sin guardar nada.

```bash
ens-evidence incident list
ens-evidence incident show INC-2026-001
ens-evidence incident verify           # sale con 1 si algún registro está roto
```

Usa siempre la CLI: un registro editado o borrado a mano se detecta, rompe el
incidente y bloquea op.exp.9. Para recuperarlo, restaura `incidents/` y
`evidence/` desde la copia (`ens-evidence backup …`). `incident verify` también
avisa de los huecos en la numeración (`INC-AAAA-NNN` es correlativa).

### Un cierre sin historial

Si un corte interrumpe el cierre después de escribir el registro final, la
orden termina con error y dice que el cierre está registrado pero el historial
no se generó. El cierre es definitivo y no hay que repetirlo. Completa el
historial, sin tocar ningún registro:

```bash
ens-evidence incident history INC-2026-001 --by "Hugo Serrano Ortiz"
```

Mientras falte, `incident verify` lo marca y op.exp.9 sigue bloqueada.

## 3. Haz un simulacro

Sin incidentes reales en el periodo, op.exp.9 queda sin evidencia. Puedes
ensayar el procedimiento con un incidente simulado:

```bash
ens-evidence incident new --simulated --title "Simulacro: ransomware en un portátil" \
  --detected-at … --by "…"
```

El simulacro exige el procedimiento aprobado y vigente. Lleva el prefijo
`SIM-`, y la matriz, la revisión y el export lo muestran como «simulacro».

Un simulacro muestra que el procedimiento y el registro funcionan; no
sustituye el registro de incidentes reales. Quien revisa op.exp.9 lo verá
marcado como simulacro y decide si basta.

## 4. Vincúlalo en la matriz y revisa

```bash
ens-evidence matrix link op.exp.7 --document procedimiento-incidentes --covers op.exp.7.1,op.exp.7.2
ens-evidence matrix link op.exp.7 --evidence <EV del historial> --covers op.exp.7.1
ens-evidence matrix link op.exp.9 --evidence <EV del historial> --covers base
ens-evidence matrix review op.exp.9 --by "…" --role system_administrator --assessment COMPLIANT --conclusion "…"
```

op.exp.9 solo admite el historial de un incidente cerrado. No valen el
procedimiento, una evidencia manual ni un registro suelto. El historial cuenta
mientras esté dentro de la periodicidad de la medida
([matriz](matriz-de-cumplimiento.md)).

Ni registrar ni cerrar ni vincular un incidente da por implantada una medida:
eso lo decide la revisión.
