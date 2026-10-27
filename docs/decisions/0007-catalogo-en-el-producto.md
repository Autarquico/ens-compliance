# 0007 — Catálogo en el producto, estado en el workspace

- Estado: aceptada
- Fecha: 2026-10-27
- Decidida por: Alejandro Perez

## Contexto

El catálogo de medidas del anexo II del RD 311/2022 es texto legal público
(art. 13 LPI) e igual para todas las organizaciones. El estado de cada
organización frente a cada medida es dato de la organización y, por el ADR
0006, no puede vivir en el producto.

## Decisión

- El catálogo se empaqueta en `src/ens_evidence/catalog/data/`, extraído de
  forma determinista de una instantánea del anexo II (BOE, texto
  consolidado) con su fecha de consolidación y sha256.
- Cada ficha distingue el eje de aplicación: por categoría del sistema
  (BÁSICA, MEDIA, ALTA) o por nivel de una dimensión (BAJO, MEDIO, ALTO de
  C, I, D, A o T). Requisitos base y refuerzos con ID propio; la exigencia
  por nivel es una expresión estructurada (`all_of`, `any_of`,
  `not_applicable`) y conserva la expresión original del anexo.
- El estado de la organización vive en `controls/measures.yaml` del
  workspace, con la versión del catálogo. Estado inicial de cada medida:
  `Applicability.UNDETERMINED` y `MeasureAssessment.NOT_ASSESSED`. El
  "pendiente de revisar" de la guía (`REVIEW`) se representa así en la
  aplicabilidad (ver 0002).
- Cualquier cambio del catálogo exige una revisión humana nueva, registrada
  en `src/ens_evidence/catalog/review.yaml` con el sha256 revisado.

## Consecuencias

- Actualizar el catálogo es volver a extraer de una instantánea nueva y
  revisar; nunca editar fichas a mano.
- Una organización con estado creado con un catálogo anterior recibe aviso
  y las medidas nuevas en su estado inicial; sus decisiones se conservan.
