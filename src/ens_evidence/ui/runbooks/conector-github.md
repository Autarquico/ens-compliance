# Conector de GitHub

Recoge, en solo lectura, la configuración de seguridad de una organización
de GitHub y sus repositorios, y la guarda como evidencia. Nunca cambia nada
en GitHub.

Spec: [collector-github](../specs/collector-github.md).

## Token de API

Crea un **fine-grained personal access token** con propietario = la
organización, acceso a todos sus repositorios (o a los del inventario) y
solo estos permisos de lectura:

| Ámbito | Permiso | Para qué |
|--------|---------|----------|
| Repositorio | Metadata: Read | Repositorios, rulesets, reglas de la rama por defecto |
| Repositorio | Administration: Read | Protección clásica de ramas, alertas de vulnerabilidad, permisos de Actions, deploy keys |
| Repositorio | Dependabot alerts: Read | Estado de las alertas de Dependabot |
| Repositorio | Secret scanning alerts: Read | Estado de secret scanning |
| Repositorio | Environments: Read | Entornos de despliegue y sus reglas |
| Organización | Members: Read | Miembros sin 2FA (recuento) |
| Organización | Administration: Read | Instalaciones de apps |

Confirmado en la documentación de GitHub: Administration: Read para
`/actions/permissions` y `/actions/permissions/workflow`; Members: Read para
los miembros de la organización; `GET /orgs/{org}` no exige permisos, pero
`two_factor_requirement_enabled` solo es visible para owners. El resto de
nombres sigue la documentación de GitHub y no se ha verificado contra una
organización real.

El token va solo en la variable de entorno; nunca en el YAML ni en Git:

```bash
export ENS_GITHUB_TOKEN=...   # desde el gestor de contraseñas
```

### Límites de un token de solo lectura

- **Bypass de rulesets:** GitHub solo devuelve `bypass_actors` a quien puede
  escribir el ruleset. Con este token no se ve, así que la protección que
  aporta un ruleset queda `UNKNOWN` ("bypass no visible") y pasa a revisión
  humana. La protección clásica de ramas sí puede dar `PASS`.
- **Tokens de la organización:** el listado de fine-grained PATs de la
  organización solo existe para GitHub Apps, así que no se recoge; los
  tokens con acceso a la organización se revisan a mano.
- **Owner:** sin un owner detrás del token, la 2FA obligatoria de la
  organización no es visible y su check queda `UNKNOWN`.

Una GitHub App como credencial (más visibilidad, pero con clave privada)
queda para más adelante.

## Configuración

En el workspace, `connectors/github.yaml`:

```yaml
asset_id: github              # activo code_hosting de scope/assets.yaml
organization: mi-organizacion
```

Los repositorios esperados salen del campo `repos` de ese activo.

## Uso

```bash
ens-evidence collect github
```

| Código | Significado |
|--------|-------------|
| 0 | Todo recogido |
| 3 | Recogida parcial: alguna sección sin permiso, no soportada o con error |
| 1 | Error global (token inválido o revocado); la evidencia del intento se guarda igual |

## Qué se recoge

Organización (2FA obligatoria, recuento de miembros sin 2FA, instalaciones
de apps), repositorios (visibilidad, archivado, rama por defecto,
`security_and_analysis`), rulesets con los heredados de la organización
(incluidos `evaluate` y `disabled`), reglas efectivas y protección clásica de
la rama por defecto, Dependabot, secret scanning, colaboradores externos,
entornos, permisos de Actions y deploy keys. Solo se revisa la rama por
defecto; el inventario de todas las ramas queda para más adelante.

Un 404 genérico de GitHub nunca se interpreta como "desactivado": GitHub
responde 404 también cuando falta permiso.

No se guardan: el token, logins de colaboradores (solo recuentos por
permiso), títulos ni claves de deploy keys, secretos o variables de Actions,
ni texto de los errores de la API.

## Checks

| Check | `PASS` solo si | `FAIL` si |
|-------|----------------|-----------|
| `github.org_2fa_required` | `two_factor_requirement_enabled: true` visible | el campo es `false` |
| `github.default_branch_protected` | la rama por defecto exige PR y bloquea force-push y borrado, sin bypass | no hay protección demostrable (con evidencia explícita de GitHub) |
| `github.secret_scanning_enabled` | `secret_scanning.status: enabled` | `disabled` |
| `github.dependabot_alerts_enabled` | `/vulnerability-alerts` responde 204 | GitHub indica que las alertas están desactivadas |

Todo lo demás es `UNKNOWN` con su motivo: sin permiso, función no
disponible en el plan, bypass configurado o no visible, dato ausente o mal
formado. Los repositorios archivados no se evalúan.
