"""Plantillas de `risk init`. Todo valor de escala es una propuesta editable, no un requisito."""

METHODOLOGY_MD = """\
# Metodología de análisis de riesgos (PROPUESTA)

> Esta plantilla es una propuesta que la organización debe revisar y adaptar.
> No es un requisito legal ni una metodología reconocida por sí misma.

El artículo 14.2 del RD 311/2022 exige emplear "alguna metodología reconocida
internacionalmente". La herramienta no juzga cuál es: la organización declara
la que usa en `risk/methodology.yaml` (`name`, `reference`, `adaptation`) y
documenta aquí cómo la aplica.

## Metodología declarada

- Nombre y versión:
- Referencia (norma o guía publicada):
- Adaptaciones respecto a la referencia:

## Cómo se aplica

- Activos y servicios considerados (ids de `scope/assets.yaml` y `scope/services.yaml`):
- Valoración de activos por dimensión (C, I, T, A, D):
- Criterio para identificar amenazas:
- Escalas de probabilidad e impacto y matriz de riesgo: ver `risk/methodology.yaml`.
- Quién acepta el riesgo residual y con qué criterio:

## Qué fija el producto y qué no

La herramienta valida la coherencia del registro (referencias, escalas, matriz,
aceptaciones). Nunca genera ni sugiere valores de riesgo. Los campos del
registro son convención del producto; la ley no los prescribe en este formato.
El rol que acepta el riesgo residual se registra sin restringirlo.

## Revisión

El anexo III, 1.1.d pide un análisis de riesgos con revisión y aprobación
anual. El análisis se revisa y aprueba cada año o ante cambios significativos
(`ens-evidence risk approve`). Que lo apruebe el `security_officer` es una
decisión del producto, no un requisito del RD; el anexo II (op.pl.1) no fija
la periodicidad.
"""

METHODOLOGY_YAML = """\
# PROPUESTA editable: la organización declara aquí su metodología y sus escalas.
# `adopted: false` hasta que la organización confirme que esta es la suya.
# El art. 14.2 del RD 311/2022 exige una metodología reconocida internacionalmente;
# la herramienta no juzga cuál es.
adopted: false
name: ""        # p. ej. el nombre y versión de la metodología que sigue la organización
reference: ""   # norma o guía publicada que la respalda
adaptation: ""  # qué se adapta respecto a esa referencia

# Escalas ordenadas de menor a mayor (propuesta).
likelihood:
  - {id: baja, description: "Poco probable en un año"}
  - {id: media, description: "Posible en un año"}
  - {id: alta, description: "Esperable en un año"}
impact:
  - {id: bajo, description: "Perjuicio limitado"}
  - {id: medio, description: "Perjuicio grave"}
  - {id: alto, description: "Perjuicio muy grave"}
# Niveles de riesgo, de menor a mayor. El primero es el mínimo de la escala.
risk_levels:
  - {id: bajo, description: "Riesgo asumible sin medidas adicionales"}
  - {id: medio, description: "Riesgo que requiere seguimiento"}
  - {id: alto, description: "Riesgo que requiere tratamiento"}
# Riesgo inherente por probabilidad (fila) e impacto (columna). Propuesta.
matrix:
  baja: {bajo: bajo, medio: bajo, alto: medio}
  media: {bajo: bajo, medio: medio, alto: alto}
  alta: {bajo: medio, medio: alto, alto: alto}
"""

REGISTER_YAML = """\
# Un riesgo por elemento. Los valores los decide la organización; la herramienta
# solo los valida contra risk/methodology.yaml. Ejemplo de la forma de un riesgo:
#
# - id: R-001
#   assets: [id-de-un-activo]       # ids de scope/assets.yaml
#   services: []                    # ids de scope/services.yaml (al menos uno de los dos)
#   threat: "descripción de la amenaza"
#   dimensions: [C, I]              # C, I, T, A, D
#   valuation: {C: ALTO, I: MEDIO}  # BAJO, MEDIO o ALTO, una por dimensión afectada
#   likelihood: media              # id de la escala
#   impact: alto                   # id de la escala
#   inherent: alto                 # debe coincidir con la matriz
#   safeguards: [op.acc.1]         # ids de medidas del catálogo
#   residual: bajo                 # id de risk_levels
#   treatment: mitigate            # mitigate | accept | transfer | avoid
#   owner: "persona responsable"
#   acceptance:                    # obligatoria si el residual no es el mínimo de la escala
#     accepted_by: "nombre"
#     role: system_owner
#     accepted_at: 2026-01-31T10:00:00+00:00
risks: []
"""

TREATMENT_PLAN_YAML = """\
# Acciones de tratamiento por riesgo. Ejemplo de la forma de una acción:
#
# - id: A-001
#   risk: R-001                    # id de un riesgo de risk/register.yaml
#   description: "qué se hará"
#   owner: "persona responsable"
#   due: 2026-12-31
#   status: pending                # pending | in_progress | done | cancelled
actions: []
"""
