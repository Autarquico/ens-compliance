# 0005 — SQLite diferido a v0.2

- Estado: aceptada
- Fecha: 2026-10-27

## Contexto

La guía pide empezar sin base de datos (`docs/ens-compliance-guide.md:648`).
La UI de v0.2 necesitará estado (usuarios, respuestas del asistente).

## Decisión

- v0.1: estado en ficheros versionables (YAML de alcance, aplicabilidad,
  matriz, respuestas) y evidencias en disco con manifiestos.
- v0.2: SQLite para estado de la UI. Los ficheros de v0.1 siguen siendo la
  fuente exportable.

## Consecuencias

- v0.1 no tiene servidor de base de datos ni migraciones.
- Al llegar SQLite habrá que definir la importación desde los ficheros de
  v0.1.

Actualización (2026-11-08): la UI no usa SQLite en su primer tramo; ver [0021](0021-interfaz-web.md).
