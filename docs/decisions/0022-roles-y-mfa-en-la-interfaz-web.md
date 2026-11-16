# 0022 — Roles y MFA en la interfaz web

- Estado: aceptada
- Fecha: 2026-11-16
- Actualiza: [0021 — Interfaz web](0021-interfaz-web.md)

## Contexto

El ADR 0021 deja entrar a cualquier persona autenticada por Cloudflare Access
con permiso de escritura sobre todo lo que la lista blanca admite. La spec
`web-ui` (`docs/specs/web-ui.md`) añade tres cosas: quién puede escribir y con
qué rol ENS, cómo saber si el segundo factor está exigido y cómo se refrescan
los certificados de Access. Este ADR recoge las decisiones y sus límites.

## Decisión

### Roles: un fichero que solo escribe la CLI

- `settings/ui-roles.yaml` asigna, por correo, un permiso de edición
  (`edit`, booleano estricto) y una lista de roles ENS (`Role`,
  `src/ens_evidence/models/enums.py:51-58`). Solo lo escribe
  `ens-evidence ui roles` (`src/ens_evidence/ui/cli.py:135-190`), con el lock
  del ciclo. `settings/` no está en la lista blanca de escritura de la UI
  (`ui/store.py:7-18`): la UI solo lo lee.
- Se **lee en cada petición** (`ui/authz.py:206`): una revocación se aplica en
  la siguiente.
- **Falla cerrado**: fichero ausente, ilegible, con campos desconocidos, con
  correos vacíos, mal formados o repetidos (tras normalizar) o con un rol
  desconocido: nadie tiene permisos y la UI queda de solo lectura
  (`ui/roles.py:86-124`). La CLI se niega a modificar un fichero inválido para
  no pisarlo (`ui/cli.py:94-103`).
- Normalización única del correo (`strip` + `casefold`) en CLI, fichero e
  identidad del JWT (`ui/roles.py:47-49`).

### Política por operación

Cada operación de escritura (método + ruta registrada y, donde la ruta reparte,
el paso del asistente o la acción) tiene una política explícita en
`ui/authz.py:48-103`. Lo que no está en la tabla se deniega con 403 antes de
escribir (`ui/authz.py:140-143`); un test exige que la tabla coincida con las
rutas, los pasos y las acciones. Los GET y HEAD nunca escriben: la
inicialización de la matriz, que antes ocurría al abrirla, es ahora
`POST /matriz/iniciar` (`ui/authz.py:89`, `ui/views/matrix.py:476-491`).

Requisitos: `edit` (basta la edición), un rol fijo (edición y ese rol) o
`chosen` (edición y el rol que la persona elige en el formulario, que el
handler valida contra los asignados).

| Operación | Rol ENS exigido | Función del producto que ya lo exige |
|---|---|---|
| Aprobar alcance (`aprobacion-alcance`) | `system_owner` | `scope/approval.py:44` |
| Aprobar categorización (`aprobacion-categorizacion`) | `security_officer` | `categorization/approval.py:240` |
| Aprobar riesgos (`aprobacion-riesgos`) | `security_officer` | `risk/approval.py:87` |
| Aprobar aplicabilidad (`aprobacion-aplicabilidad`) | `security_officer` | `applicability/declaration.py:166` |
| Firmar una valoración (`valoraciones`) | `information_owner` o `service_owner`, según el elemento; los borradores solo piden `edit` | `categorization/models.py:54,63`; `ui/wizard/categorization.py:39` |
| Aprobar un documento (`documentos-aprobar`) | el de la ficha del documento (p. ej. `management` para la política y la normativa) | `orgdocs/approval.py:45-52` |
| Cambiar quién aprueba los procedimientos (bloque «Responsables», `autoridad_procedimientos`) | `management` (política de la interfaz: lo designa la dirección; si el valor no cambia, basta la edición) | `ui/wizard/docs.py` |
| Revisar una medida (`matriz revisar`) | `chosen`: uno de los autoevaluadores (`system_administrator` o su delegado) | `models/dossier.py:13`; `ui/views/matrix.py:52` |
| Autoevaluación del expediente (`self-assess`) | `chosen`: igual que el anterior | `models/dossier.py:13` |
| Análisis del expediente (`analyze`) | `security_officer` | `assessment/dossier/transitions.py:73,516` |
| Declaración del expediente (`declare`) | `management` | `assessment/dossier/transitions.py:74,579` |
| Custodios de claves | `chosen` (entre los roles de revisión) | `ui/views/custody.py:129` |
| Enviar a pre-auditoría y decidir hallazgos | `security_officer` | **política web de esta decisión** (ver abajo) |
| Resto (borradores del asistente, enlazar/responsable en la matriz, evidencias, incidentes, herramientas, generar expediente, preparar pre-auditoría, iniciar matriz) | solo `edit` | no hay firma de un rol |

La columna «función del producto» es la fuente; la tabla de `authz.py` debe
coincidir con ella (los pasos `chosen` y `documentos-aprobar` los resuelve el
handler en el momento de escribir). Las firmas de valoraciones se comprueban
todas antes de guardar ninguna.

### Pre-auditoría: el rol es una política web

El producto no exige hoy ningún rol para enviar datos a un proveedor externo
(consentimiento) ni para decidir los hallazgos de una pre-auditoría. La
interfaz exige `security_officer` para ambas (`ui/authz.py:97-98`). Es una
**política de autorización web**, no una regla del producto: la CLI no la
aplica. Se comprueba antes de crear la petición; si se rechaza no queda
petición ni disposición. La petición se autoriza **en el momento de encolar**:
si el permiso se revoca después, la petición ya encolada se ejecuta.

### MFA: se verifica en la política de Access, no en el token

La documentación de Cloudflare no describe, en el token de aplicación de
Access, ningún claim de segundo factor (revisado el 2026-11-16: «application
token», «independent MFA», «validating JSON»). Por eso la interfaz **no
rechaza ni acepta por un claim** (`amr` u otro). Se comprueba la política:

- El check `cloudflare.ui_access_requires_mfa` del conector Cloudflare, de solo
  lectura (`connectors/cloudflare/mfa_checks.py`), identifica la aplicación de
  Access que cubre el hostname configurado en `ui_hostname` y da `PASS` solo
  si toda política `allow` que la alcanza exige MFA. Una política `bypass` o
  una `allow` sin MFA es `FAIL`; sin `ui_hostname`, sin aplicación, con datos
  ilegibles o con evidencia anterior al cambio, `UNKNOWN`.
- **No verificado**: la forma exacta con que la API de Cloudflare expresa la
  MFA (selector `require` de método de autenticación `mfa`;
  `mfa_config.mfa_disabled`) está tomada de la documentación y solo se prueba
  con fixtures (`mfa_checks.py:3-6`).
- Un `PASS` no prueba que cada persona haya enrolado el factor: eso lo
  evidencian los conectores del proveedor de identidad.
- El check no está vinculado a ninguna medida ENS: no existe un mapeo de checks
  de Cloudflare a medidas y no se ha inventado ningún identificador. Lo
  vincula una persona con `ens-evidence matrix link`.
- Se elimina la idea de `ENS_UI_REQUIRE_MFA`: no hay nada que activar o
  desactivar en la aplicación.

### Certificados (JWKS)

El ADR 0021 dejaba el refresco como posible followup; **ya está
automatizado**. `app` descarga
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` al arrancar, cada
6 h y, tras un fallo, a los 15 min (`scheduling/access_jwks.py:34-35,186-204`).
Solo acepta hosts `<equipo>.cloudflareaccess.com`, sin redirecciones, 10 s en
total, un máximo de 64 KiB y 20 claves, `kid` únicos y claves RSA de al menos
2048 bits (`access_jwks.py:26-38,127-157`). Ante cualquier fallo conserva el
fichero anterior y registra un código estable. Publica de forma atómica en el
volumen `access-jwks`, que `app` monta con escritura y `ui` **solo lectura**
(`compose.yaml:33,100`), en `/run/access-jwks/access-jwks.json`
(`ENS_UI_ACCESS_JWKS_FILE` en `deploy/ui.env`). Variables de `app`:
`ENS_ACCESS_JWKS_TEAM_DOMAIN` y `ENS_ACCESS_JWKS_FILE` (`deploy/.env.example`);
sin ellas el trabajo no se registra. Refresco manual:
`ens-evidence scheduler refresh-access-jwks` (`cli.py:3285`).

### Panel de agentes

`GET /agentes` y `GET /agentes/{activo}` (`ui/views/agents.py`) muestran, solo
lectura, el alta de cada equipo, su último contacto en la evidencia y los
resultados HOST. La plantilla recibe objetos con campos permitidos (nunca filas
de osquery, UUID ni el registro completo). No tiene acciones ni formularios.

### Límite de confianza (explícito)

La protección de `settings/ui-roles.yaml` es **de código, no de aislamiento**:

- La UI solo escribe bajo los prefijos de `WRITABLE_PREFIXES`
  (`ui/store.py:7-18`, ampliada desde el ADR 0021), entre los que no está
  `settings/`: ningún handler puede escribir el fichero de roles.
- Pero `ui` corre con la misma imagen y el mismo usuario que `app`, y monta el
  volumen `data` con escritura (`compose.yaml:97`) porque la lista blanca lo
  necesita. Quien ejecute código arbitrario dentro de `ui` podría reescribir
  `settings/ui-roles.yaml` y darse roles.
- El JWKS sí está aislado: va en un volumen aparte, de solo lectura en `ui`.

El aislamiento real (que `ui` monte los ajustes en solo lectura y su escritura
vaya a otro volumen) queda como followup. Mientras tanto, la defensa es la
lista blanca, la revisión del código y la auditoría.

### Campo `by`

El ADR 0021 dice que `by` «se prerrellena con el email del JWT». **El código no
lo hace** (no hay prerrelleno desde la identidad; el formulario de evidencias
usa un valor por defecto propio, `evidencias_nueva.html:20`) y esta decisión no
cambia ese comportamiento. El producto trata `by` como el nombre **declarado**
de quien asume la decisión, y no todas las APIs comprueban que coincida con el
firmante declarado. Lo que prueba quién operaba la pantalla es el log de
auditoría: `identity` (correo del JWT), `roles` usados, paso, acción, claves y
hora, y las líneas `denied` de las operaciones rechazadas
(`ui/audit.py:25-55`). El rol validado prueba que esa persona tenía asignado el
rol; no prueba que `by` sea su nombre.

## Consecuencias

- Al actualizar, todo el mundo queda en solo lectura hasta que se asignen roles
  con `ui roles`. Está documentado en el runbook.
- Una instalación puede quedarse sin nadie que edite si el fichero se corrompe;
  se corrige con la CLI o a mano.
- La MFA es una comprobación de configuración de Access, no una prueba por
  petición. Un `PASS` es evidencia para una persona, no una declaración de
  conformidad: la revisión humana sigue siendo obligatoria.
- La política de la pre-auditoría puede divergir de la CLI; si el producto fija
  después un rol, este ADR se actualiza.
