# Modelo de amenaza — copia de evidencias

Alcance: evidencias y estado del workspace, y su copia independiente con
restic (ADR 0008).

## Activos

- Evidencias (`evidence/raw/`) y manifiestos (`evidence/manifests/`).
- Estado de la organización (alcance, aplicabilidad, revisiones).
- Secretos de la copia: contraseña del repositorio restic y credenciales del
  destino.

## Amenazas y controles

| Amenaza | Control | Qué queda sin cubrir |
|---------|---------|----------------------|
| Alteración de un artefacto | `evidence verify` compara con el sha256 del manifiesto | Alteración conjunta de artefacto y manifiesto |
| Alteración conjunta o borrado en el workspace | Copia restic externa; comparación del inventario restaurado | Solo si el destino es inmutable durante la retención |
| Borrado o sobrescritura del destino con las credenciales de la caja | Bloqueo de objetos del proveedor; `backup verify-immutability` con canarios | Hipótesis hasta probarla contra el bucket real; un rechazo aislado no acredita toda la política |
| Compromiso de las credenciales del workspace | Credenciales de la copia distintas y aisladas en el entorno del subproceso | Si el atacante controla la caja en ejecución, puede leer ambas |
| Caída del proveedor de fuentes vigiladas (p. ej. Cloudflare) arrastrando la copia | Destino de la copia en otra cuenta y, preferentemente, otro proveedor | Si el destino es R2 en la misma cuenta, Cloudflare es punto único de fallo |
| Pérdida de la contraseña restic | Guardada fuera de la caja (gestor de contraseñas) | Sin ella la copia es irrecuperable |
| Copia parcial dada por buena | Exportación abortada si cambia el listado o falla una descarga; registro `started`/`completed`/`failed` | — |
| Secretos en registros | Errores de restic redactados; registros sin variables de entorno | — |

## Controles organizativos (verificación manual, registrada como evidencia)

- [ ] El destino de la copia está en una cuenta distinta de las fuentes vigiladas, con administración separada.
- [ ] Bloqueo de objetos configurado y `backup verify-immutability` superado contra el bucket real.
- [ ] Contraseña restic y credenciales del destino guardadas fuera de la caja.
- [ ] Restauración completa probada en una máquina distinta (runbook).
