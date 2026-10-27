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

## Dónde viven tus datos

Los datos de tu organización (inventario, alcance, evidencias…) nunca se
guardan en este repo. Viven en un *workspace* que eliges tú: una carpeta
local o un bucket S3-compatible (S3, Cloudflare R2…). Se configura en
`~/.config/ens-evidence/settings.yaml` (o con `--settings`):

```yaml
workspace:
  backend: local            # local | s3
  local: { path: /ruta/a/mi-workspace }
```

Para S3, instala el extra (`ens-evidence[s3]`) y pasa las credenciales solo
por variables de entorno (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`);
nunca en el fichero de ajustes. Detalle en
[`docs/decisions/0006-workspace-y-almacenamiento.md`](docs/decisions/0006-workspace-y-almacenamiento.md).

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
