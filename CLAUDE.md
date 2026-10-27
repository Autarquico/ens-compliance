# ens-compliance — instrucciones para agentes

Producto: herramienta open source (AGPL-3.0) que prepara la autoevaluación
ENS de una pyme. Visión: `docs/vision.md`. Decisiones: `docs/decisions/`.
Convenciones PM: `AGENT.md`.

## Reglas no negociables

1. Nunca marcar una medida ENS como cumplida solo porque una herramienta esté instalada.
2. Usar identificadores y requisitos oficiales del RD 311/2022, anexo II.
3. Nunca inventar requisitos legales, identificadores de medida ni evidencias.
4. Respetar el modelo de estados de `docs/decisions/0002-modelo-de-estados.md`: un enum por eje.
5. La ausencia de evidencia nunca es `PASS`.
6. Nunca guardar en Git credenciales, tokens, claves privadas ni logs sensibles en bruto.
7. Nunca modificar infraestructura de producción sin aprobación explícita.
8. Nunca ejecutar operaciones destructivas de forma automática.
9. Todos los collectors son de solo lectura por defecto.
10. Todo check tiene tests automatizados.
11. Todo informe generado identifica fuentes y timestamps.
12. La revisión humana es obligatoria para medidas organizativas y evaluaciones finales; la herramienta nunca declara conformidad por sí sola.

Origen: `docs/ens-compliance-guide.md:269-282`, adaptado al modelo de estados.

## Stack

Python ≥ 3.12 · uv · Pydantic v2 · Typer · HTTPX · PyYAML · Pytest · Ruff.
Paquete en `src/ens_evidence/`. Esquemas JSON exportados en `schemas/`.

## Flujo de trabajo

- Cada cambio parte de una spec en `docs/specs/` con escenarios Given/When/Then.
- Commits pequeños; tests junto con la implementación.
- APIs externas solo con fixtures en tests; nunca credenciales reales en unit tests.
- Checks: `~/.claude/bin/run-checks` (lee `.claude/checks.yml`).
- Tras cambiar modelos: `uv run ens-evidence schema export` y commitear `schemas/`.
- Documentar despliegue, rollback y recuperación de todo lo que se despliegue.
