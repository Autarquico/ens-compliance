# 0003 — Conectores como plugins

- Estado: aceptada
- Fecha: 2026-10-27

## Contexto

Cada pyme usa fuentes distintas (Google Workspace o Microsoft 365, GitHub o
no, Cloudflare o no). El núcleo no puede cambiar cada vez que se añade una
fuente (`docs/vision.md`, escenario "conectores enchufables").

## Decisión

- Interfaz `Connector`: `name`, `config_model` (Pydantic) y
  `collect(config) -> CollectionResult`, de solo lectura. Hook opcional
  `prepare(config, store) -> config` para completar la configuración con
  datos del workspace (p. ej. las zonas del inventario) antes de recoger. Constructor `from_settings(settings)` (por defecto
  `cls()`) para los conectores que necesitan los ajustes del workspace abierto.
- La configuración de cada conector vive en el workspace, en
  `connectors/<name>.yaml`; sus credenciales, solo en variables de entorno.
  `collect <name>` resuelve el conector por el registro.
- Descubrimiento por entry points del grupo `ens_evidence.connectors`, más
  registro explícito (para tests y conectores internos).
- El conector devuelve `CollectionStatus` y datos; nunca un `CheckStatus`
  (ver 0002).

## Consecuencias

- Un conector nuevo es un paquete o módulo que declara su entry point; el
  núcleo no se toca.
- Los conectores de terceros se ejecutan con los permisos de la caja: solo se
  cargan los instalados explícitamente.
