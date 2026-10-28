# Conector de Cloudflare

Recoge, en solo lectura, la configuración perimetral de una cuenta de
Cloudflare y la guarda como evidencia. Nunca cambia nada en Cloudflare.

Spec: [collector-cloudflare](../specs/collector-cloudflare.md).

## Token de API

Crea un **API token de cuenta** (no uses nunca la Global API Key) con solo
estos permisos de lectura:

| Ámbito | Permiso | Para qué |
|--------|---------|----------|
| Cuenta | Account Settings Read | Audit logs |
| Cuenta | Cloudflare Tunnel Read | Túneles y su ingress |
| Cuenta | Access: Apps and Policies Read | Aplicaciones y políticas de Access |
| Zona | Zone Read | Listado de zonas |
| Zona | DNS Read | Registros DNS (hostnames de túneles locales) |
| Zona | Zone Settings Read | TLS: modo SSL, versión mínima, HTTPS forzado |
| Zona | Zone WAF Read | Rulesets del WAF |

Limita las zonas del token a las del inventario. Los nombres de permiso
siguen la documentación de Cloudflare; no se han verificado contra una cuenta
real.

El token va solo en la variable de entorno; nunca en el YAML ni en Git:

```bash
export ENS_CLOUDFLARE_API_TOKEN=...   # desde el gestor de contraseñas
```

Un token de usuario también sirve si pones `token_kind: user`.

## Configuración

En el workspace, `connectors/cloudflare.yaml`:

```yaml
asset_id: cloudflare            # activo edge_network de scope/assets.yaml
account_id: 0123456789abcdef0123456789abcdef
token_kind: account             # account (por defecto) o user
public_routes:                  # rutas públicas por diseño, con motivo
  - hostname: caja.example.com
    path: /agent/*
    reason: los agentes osquery reportan desde fuera sin sesión de usuario
```

Las zonas esperadas salen del campo `zones` de ese activo del inventario.

## Uso

```bash
ens-evidence collect cloudflare
```

| Código | Significado |
|--------|-------------|
| 0 | Todo recogido |
| 3 | Recogida parcial: alguna sección sin permiso, no soportada o con error |
| 1 | Error global (token inválido, revocado o no activo); la evidencia del intento se guarda igual |

## Qué se recoge

Zonas, DNS, TLS, WAF, aplicaciones y políticas de Access, túneles (con su
ingress) y un resumen de audit logs de los últimos 30 días. Cada sección
tiene su propio estado: "sin permiso" nunca se confunde con "no
configurado".

No se guardan: el token, los valores de los selectores de Access (correos),
el contenido de registros DNS que no son CNAME, credenciales o parámetros de
las URL de origen, ni las entradas de los audit logs (solo ventana y
recuento).

## Check: túnel sin Access

`cloudflare.tunnel_requires_access` evalúa cada hostname y path publicado por
un túnel. Tunnel no implica autenticación:

- `PASS` solo si una aplicación de Access cubre la ruta completa con
  políticas que exigen identidad.
- `FAIL` si no hay aplicación, si hay `bypass` o si se permite a `everyone`
  sin más requisitos.
- `UNKNOWN` cuando no se puede demostrar (cobertura parcial, ingress local no
  visible, sección sin permiso) y para las `public_routes` declaradas, que
  quedan para revisión humana.

Detalles de la evaluación:

- Un path sin `*` (en una aplicación de Access o en una `public_routes`) cubre
  ese path y sus subpaths: `/agent` cubre `/agent/x`. La `/` final no cuenta
  (`host/a/` equivale a `host/a`).
- Una regla de ingress con path y sin hostname aplica a cualquier hostname del
  túnel: da `UNKNOWN` (subject `<túnel> (sin hostname)<path>`), nunca se
  descarta. Lo mismo vale para una catch-all final con origen real
  (`<túnel> (sin hostname)/*`): publica cualquier hostname que llegue al túnel.
  Solo las respuestas `http_status` se ignoran.
- Los hostnames se comparan en minúsculas.
- Un destino de Access cuyo host no se puede interpretar (con puerto o mal
  formado) da `UNKNOWN` en toda ruta que pudiera alcanzar; solo se descarta si
  su host es sin duda otro. Un destino ilegible puede dejar en `UNKNOWN` todas
  las rutas de la cuenta: corrige el destino en Cloudflare.
- Si la sección de túneles se recoge con éxito pero está vacía, no se emite ningún
  resultado: no hay rutas que evaluar y nunca se declara `PASS` sin evidencia.
