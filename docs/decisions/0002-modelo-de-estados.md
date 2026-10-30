# 0002 — Modelo de estados

- Estado: aceptada
- Fecha: 2026-10-27
- Aprobada por: Alejandro Perez

## Contexto

La guía usa tres conjuntos de estados que se solapan:

- Reglas del proyecto (`docs/ens-compliance-guide.md:274`): `PASS`, `FAIL`,
  `ERROR`, `UNKNOWN`, `REVIEW`, `NOT_APPLICABLE`.
- Estados de aplicabilidad, check y evaluación (`:353-377`).
- Collector de Workspace (`:511-517`): `PASS`, `FAIL`, `UNKNOWN`,
  `PERMISSION_DENIED`, `NOT_SUPPORTED`.

Mezclar ejes en un mismo enum permite estados absurdos (un check
`NOT_APPLICABLE`, una recogida `PASS`) y, sobre todo, falsos `READY`.

## Decisión

### Un enum por eje

| Eje | Valores |
|-----|---------|
| `CollectionStatus` (operación de recogida) | `SUCCESS`, `ERROR`, `PERMISSION_DENIED`, `NOT_SUPPORTED` |
| `CheckStatus` (resultado técnico) | `PASS`, `FAIL`, `ERROR`, `UNKNOWN` |
| `Applicability` | `APPLICABLE`, `NOT_APPLICABLE`, `UNDETERMINED` |
| `MeasureAssessment` | `COMPLIANT`, `NON_COMPLIANT`, `REQUIRES_REVIEW`, `NOT_ASSESSED` |
| `Readiness` | `READY`, `NOT_READY` |
| `DossierState` | `GENERATED`, `SELF_ASSESSED`, `ANALYZED`, `DECLARED` |

### Traducción recogida → check

| Recogida | Dato necesario | Check |
|----------|----------------|-------|
| `SUCCESS` | presente y cumple | `PASS` (exige al menos un `evidence_id`) |
| `SUCCESS` | presente y no cumple | `FAIL` |
| `SUCCESS` | ausente o no interpretable | `UNKNOWN` |
| `ERROR` | — | `ERROR` |
| `PERMISSION_DENIED` | — | `UNKNOWN` con motivo `permission_denied` |
| `NOT_SUPPORTED` | — | `UNKNOWN` con motivo `not_supported` |

- `PASS`/`FAIL`/`UNKNOWN` de la guía como salida del collector (`:511-515`)
  pasan a ser salida del check. El collector informa de la operación y del
  dato, no del cumplimiento.
- `REVIEW` (`:274`) es `MeasureAssessment.REQUIRES_REVIEW`.
- `NOT_APPLICABLE` (`:274`) es aplicabilidad, nunca resultado de un check.

### Roles

`Role` recoge los responsables que diferencia el art. 11 del RD 311/2022
(responsable de la información `information_owner`, del servicio
`service_owner`, de la seguridad `security_officer` y del sistema
`system_owner`), más el administrador del sistema y su delegado. Las
facultades de categorización (art. 41) están en 0009.

### Revisión humana

`Review`: `reviewer`, `role`, `reviewed_at`, `valid_until`, `conclusion`,
`evidence_ids`. Una medida `COMPLIANT` sin `Review` vigente no cuenta como
satisfecha. Vigente significa `reviewed_at <= fecha de evaluación <
valid_until`. Una revisión con `reviewed_at` posterior a la fecha de
evaluación todavía no ha ocurrido: bloqueo `MISSING_REVIEW` con detalle
`review_in_future`.

### Regla de preparación

Función pura. Entrada: medidas (aplicabilidad y justificación, evaluación,
revisión, evidencias con `valid_until`), hallazgos abiertos de la
pre-auditoría, si se ejecutó la capa de juicio, y la fecha de evaluación.

Salida: `Readiness`, lista de bloqueos y `pending_human_review`. Bloqueos:
`UNDETERMINED_APPLICABILITY`, `NOT_APPLICABLE_WITHOUT_JUSTIFICATION`,
`MISSING_EVIDENCE`, `EXPIRED_EVIDENCE`, `MISSING_REVIEW`, `EXPIRED_REVIEW`,
`NOT_COMPLIANT`, `OPEN_FINDING`.

- `READY` solo con cero bloqueos.
- `UNDETERMINED` siempre bloquea.
- `NOT_APPLICABLE` exige justificación y revisión vigente.
- Si no se ejecutó la capa de juicio, `pending_human_review` es verdadero.
- Una evidencia con `collected_at` posterior a la fecha de evaluación no
  cuenta; si no queda ninguna válida, bloqueo `MISSING_EVIDENCE` con
  detalle `evidence_in_future:<id>`. Las evidencias sin `collected_at` se
  siguen aceptando (0008).

### Transiciones del expediente

Orden estricto: `GENERATED → SELF_ASSESSED → ANALYZED → DECLARED`. Cada
transición registra `actor`, `role`, `at` y `dossier_sha256`.

- `SELF_ASSESSED`: por `system_administrator` o su delegado
  (`system_administrator_delegate`).
- `ANALYZED`: por `security_officer`, con conclusiones (obligatorias) y
  medidas correctoras dirigidas a `system_owner`. La lista de medidas
  correctoras puede ir vacía si no hacen falta (anexo III, 2.1: "las medidas
  correctoras adecuadas").
- `DECLARED`: solo si la categoría es `BASICA` y `Readiness` es `READY`.
  Si `pending_human_review` es verdadero (no se ejecutó la capa de juicio),
  además exige que la transición `ANALYZED` registre
  `manual_judgment_review: true`: el responsable de la seguridad revisó a
  mano lo que cubría esa capa. Nunca se declara con esa revisión pendiente.

Base: RD 311/2022, art. 38.1 y anexo III, 2.1.

## Consecuencias

- Ningún camino produce `PASS` sin evidencia ni `READY` por omisión.
- Cada eje evoluciona por separado; añadir un valor exige actualizar este
  ADR y los esquemas exportados.
- La regla de preparación vive en un solo sitio y el resto la consulta.
