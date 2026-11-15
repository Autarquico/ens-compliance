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
| Cuenta | Access: Organizations, Identity Providers, and Groups Read | Grupos (`/access/groups`) y proveedores de identidad (`/access/identity_providers`) de Access |
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
| 3 | Recogida parcial: alguna sección, o el conjunto, sin permiso, no soportado o con error |
| 1 | Error global (token inválido, revocado o no activo); la evidencia del intento se guarda igual |

## Qué se recoge

Zonas, DNS, TLS, WAF, aplicaciones, políticas, grupos y proveedores de identidad de Access, túneles (con su
ingress) y un resumen de audit logs de los últimos 30 días. Cada sección
tiene su propio estado (`access_groups` incluido): "sin permiso" nunca se confunde con "no
configurado".

De cada grupo de Access solo se guardan id, nombre, los tipos de selector de
include/require/exclude y los ids de los grupos anidados que referencia. Una
configuración remota de túnel sin `config` o sin `ingress` (ausentes o `null`) es
`ingress_status: ERROR`, nunca un túnel sin rutas.

De cada proveedor de identidad (sección `identity_providers`) solo se guardan
id, nombre y tipo. Nunca su configuración (identificador y secreto de cliente)
ni la de SCIM. El permiso es el mismo que ya pide `access_groups`.

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
- Un selector `group` solo cuenta como identidad si el contenido del grupo
  (resuelto de forma recursiva) la exige con la misma regla que una política:
  include solo de identidad, o algún require de identidad. Un grupo solo de IP,
  país o service token no basta. Grupo inexistente, ciclo entre grupos, selector
  sin id o sección `access_groups` no recogida: `UNKNOWN`, nunca `PASS`.
- Un túnel cuyo ingress remoto no se pudo recoger (incluida una configuración
  sin `config` o `ingress`) emite un resultado de cobertura no resuelta con la
  traducción del ADR 0002.
- Los hostnames se comparan en minúsculas.
- Un destino de Access cuyo host no se puede interpretar (con puerto o mal
  formado) da `UNKNOWN` en toda ruta que pudiera alcanzar; solo se descarta si
  su host es sin duda otro. Un destino ilegible puede dejar en `UNKNOWN` todas
  las rutas de la cuenta: corrige el destino en Cloudflare.
- Si la sección de túneles se recoge con éxito pero está vacía, no se emite ningún
  resultado: no hay rutas que evaluar y nunca se declara `PASS` sin evidencia.

## Check: Access solo con el proveedor de la pyme

`cloudflare.access_idp_restricted` es una restricción técnica de los métodos de
acceso: para cada aplicación de Access que publica un host de los túneles de la
cuenta (menos las `public_routes`, la misma población que
`tunnel_requires_access`), mira qué proveedores admite.

- `PASS` solo si admite un único proveedor de directorio (`google-apps` o
  `azureAD`); el motivo lo nombra. **No prueba** que sea el tenant de la pyme ni
  que exija doble factor: una persona lo verifica y el MFA se evidencia en
  `collector-workspace` / `collector-m365`.
- `FAIL` con código de motivo `otp_or_consumer_idp` (código por correo o
  proveedor personal) o `bypass`.
- `UNKNOWN` con `permission_denied`, `no_identity_providers`, `idp_review`,
  `non_identity`, `scope_incomplete` o `stale_evidence`. `idp_review` cubre
  varios motivos: SAML, OIDC u otro proveedor o más de un directorio; sin
  política `allow` o sin proveedor admitido; `deny` que menciona un método de
  código o personal; decisión, selector, grupo o `allowed_idps` desconocido,
  ilegible o ausente de la evidencia; y `allowed_idps` ausente o `null` en la
  respuesta de Cloudflare («la respuesta no indica qué proveedores admite la
  aplicación»). Nunca `PASS` con datos incompletos; la lista de proveedores
  permitidos vacía admite todos los de la cuenta.

Guía de configuración y de acceso de emergencia:
[acceso con proveedor de identidad](../runbooks/acceso-con-proveedor-de-identidad.md).

### Cobertura de op.acc.6

El vínculo a una medida lo hace una persona con `ens-evidence matrix link`; el
código no lo crea. Cobertura declarada por cláusula:

| Cláusula | Cobertura |
|----------|-----------|
| op.acc.6.6 | No cubre |
| op.acc.6.r2.1 | No cubre |
| Resto de op.acc.6 | No cubre |

op.acc.6.6 pide inhabilitar las credenciales cuando el usuario termina su
relación con el sistema. Limitar las aplicaciones al proveedor de la pyme
centraliza en ese proveedor la baja de cuentas, pero el check no evidencia que
las bajas se hagan: solo observa qué métodos de inicio de sesión admite cada
aplicación.

Fuente: documentación de la API de Cloudflare, 2026-11-15; no probado contra una
cuenta real. Siguen sin verificar: el campo ausente en la lista de aplicaciones,
las formas exactas de algunos selectores, la expansión de políticas
reutilizables y si Access puede exigir el segundo factor.
