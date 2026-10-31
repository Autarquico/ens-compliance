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
  quién lo hace y cuándo. El art. 33.7 lo exige. La herramienta no fija
  plazos ni el contenido de la notificación.

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
  vincularlo.
- Al cerrar no puede quedar pendiente si hay datos personales afectados. Si el
  incidente tiene relevancia jurisdiccional, indica quién da el asesoramiento
  legal.

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
`evidence/` desde la copia (`ens-evidence backup …`).

## 3. Haz un simulacro

Sin incidentes reales en el periodo, op.exp.9 queda sin evidencia. Ensaya el
procedimiento con un incidente simulado:

```bash
ens-evidence incident new --simulated --title "Simulacro: ransomware en un portátil" \
  --detected-at … --by "…"
```

El simulacro exige el procedimiento aprobado y vigente. Lleva el prefijo
`SIM-`, y la matriz y el export lo muestran como «simulacro».

## 4. Vincúlalo en la matriz y revisa

```bash
ens-evidence matrix link op.exp.7 --document procedimiento-incidentes --covers base
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
