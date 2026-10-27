# 0004 — Caja con Docker Compose

- Estado: aceptada
- Fecha: 2026-10-27

## Contexto

El producto tiene que instalarse en una sola VM o en un portátil, por alguien
"apañado", con pocos recursos (`docs/vision.md`, "Arquitectura propuesta").

## Decisión

- Despliegue con Docker Compose: `caddy` (TLS, separa `/agent/*` público de
  la UI privada) y `app` (núcleo, CLI, scheduler; en v0.2 también UI y
  servidor osquery).
- Perfiles opcionales: `deteccion` (wazuh-manager sin indexer ni dashboard +
  VictoriaLogs) e `ia-local` (Ollama).
- Fuera: Kubernetes, Fleet, MySQL/Postgres, Redis, MinIO, colas, Grafana.

## Consecuencias

- Una sola unidad que operar, respaldar y actualizar.
- La implementación llega en `box-packaging` (v0.1); el instalador para
  terceros, en `installer` (v0.2).
