# ens-compliance

Herramienta open source para que una pyme española prepare la
autoevaluación del Esquema Nacional de Seguridad (RD 311/2022) con
evidencias reales: documentación, comprobaciones técnicas deterministas,
expediente y pre-auditoría.

> **Aviso.** Esta herramienta **prepara**; no certifica ni garantiza el
> cumplimiento. En categoría BÁSICA la conformidad la declara la propia
> organización tras su autoevaluación (RD 311/2022, art. 38.1 y anexo III).
> En MEDIA y ALTA hace falta una auditoría de certificación externa.

Estado: en desarrollo (v0.1). Visión y fases en
[`docs/vision.md`](docs/vision.md); decisiones en
[`docs/decisions/`](docs/decisions/).

## Desarrollo

Requisitos: Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run pytest
uv run ens-evidence --help
```

## Licencia

[AGPL-3.0](LICENSE).
