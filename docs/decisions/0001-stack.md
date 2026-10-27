# 0001 — Stack

- Estado: aceptada
- Fecha: 2026-10-27

## Contexto

El núcleo tiene que ser determinista, tipado, fácil de probar con fixtures y
ejecutable en una caja pequeña (`docs/vision.md`, "Arquitectura propuesta").
La guía propone Python con Pydantic, Typer, HTTPX, Pytest y Ruff
(`docs/ens-compliance-guide.md:284-293`, `:632-646`).

## Decisión

- Python ≥ 3.12, paquete `ens_evidence` en layout `src/`.
- uv para entorno y lockfile; hatchling como backend de build.
- Pydantic v2 para modelos y esquemas JSON; Typer para la CLI; HTTPX para
  APIs; PyYAML para configuración y datos.
- Pytest y Ruff; CI en GitHub Actions con `uv sync --locked`.
- Licencia AGPL-3.0 (`LICENSE`, texto oficial).

## Consecuencias

- Un solo lenguaje para núcleo, CLI y (en v0.2) la UI renderizada en servidor.
- El lockfile fija dependencias; actualizarlas es un cambio revisado.
- Los esquemas JSON se generan desde los modelos, no se mantienen a mano.
