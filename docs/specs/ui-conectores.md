---
codename: ens-compliance
title: UI de conectores — estado y configuración
type: task
status: active
priority: P0
size: M
issue: 94
prs: [96]
related_issues: [94]
depends_on: [ui-inventario, ui-copias]
supersedes: []
related_specs: [ui-inventario, ui-copias, ui-recogida]
created: 2026-11-17
updated: 2026-11-17
---

# UI de conectores — estado y configuración

## Contexto / problema

Los conectores (GitHub, Cloudflare, Google Workspace, keystore, contraseñas, Restic, copias)
solo se configuran a mano. Hay que editar `connectors/<name>.yaml` y poner su credencial en
`deploy/.env`. Su estado solo se ve en la CLI o en el informe diario. Desde la web no se
responde a:
- «¿qué conectores tengo, cuáles faltan por configurar, cuándo recogieron por última vez y
  qué falló?»;
- «¿cómo configuro este?».

Es el tercer tramo de la UI de operación, tras inventario y copias. La acción «Recoger ahora»
va en el tramo siguiente (`ui-recogida`).

Restricciones que no cambian (ADR 0021):
- El servicio `ui` no tiene los secretos de los conectores (`deploy/.env` es solo de `app`).
- `ui` no tiene salida a Internet.
- La web nunca ve, pide ni guarda una credencial.
- `connectors/<name>.yaml` ya rechaza claves y valores que parecen secretos
  (`connectors/config.py:48-55`).

## Scope

**Entra:**
- **Página `/conectores`** con un bloque por cada conector registrado (entry points
  `ens_evidence.connectors`). Cada bloque muestra:
  - **Estado de la configuración:**
    - sin configurar;
    - configurado;
    - configuración inválida, con mensaje fijo y sin valores.
  - **Última recogida importada:**
    - fecha y estado (`collection_status`);
    - estado por sección, con fuente y timestamp (regla 11);
    - el motivo del fallo, en categorías fijas:
      - credencial ausente;
      - credencial rechazada;
      - sin permiso;
      - no soportado;
      - error del conector.
    - Nunca se copia el texto en bruto de `data.error`.
  - **Último resultado de sus checks**, sacado del historial sin recalcular. Usa la misma
    proyección que el inventario (`check_history.py`) y la regla de cobertura parcial (K6).
  - **La credencial que necesita, solo por nombre** (p. ej. `ENS_GITHUB_TOKEN`). Se indica
    dónde ponerla (`deploy/.env` de la caja) y se enlaza a su doc. La web no puede saber si
    está puesta: lo dice así y remite a la última recogida.
- **Formulario de configuración** para editores, solo con campos no secretos. Lo cubren:
  - `github`: activo y organización;
  - `cloudflare`: activo, cuenta, tipo de token y `ui_hostname`;
  - `google-workspace`: activo, customer, `admin_subject` y dominios, uno por línea;
  - `keystore`: activo, dirección y umbrales;
  - `passwords`: activo, ruta de export y umbrales;
  - `restic`: activo y umbrales.

  Normas del formulario:
  - El activo se elige de entre los del inventario.
  - Se valida con el mismo modelo y el mismo rechazo de secretos que el cargador.
  - Si hay rechazo, se vuelve a mostrar con los errores por campo y no se escribe nada.
  - Los campos que el formulario no muestra (`public_routes` de Cloudflare, `offsite` de
    Restic) se conservan tal cual al guardar.
- **`backups`** solo en lectura. Su configuración la hace el asistente `/herramientas/copias`
  y su estado está en `/copias`; la página enlaza a ambos.
- **Escritura:**
  - `connectors/` entra en los prefijos que `UiStore` puede escribir;
  - un lock de proceso, como el inventario;
  - auditoría de cada escritura, incluidos los fallos;
  - 303 al terminar.
- Enlace en la navegación principal y desde la ficha de cada activo del inventario cuyo
  conector lo cite.

**No entra:**
- «Recoger ahora» (tramo `ui-recogida`).
- Poner, ver o comprobar credenciales desde la web.
- Editar `public_routes` de Cloudflare, `offsite` de Restic o los repositorios de `backups`.
- Borrar la configuración de un conector. Se hace en el fichero; borrar desde la web es
  destructivo y no aporta en este tramo.

## Criterios de aceptación

### Scenario: conector sin configurar
- **Given** un workspace sin `connectors/github.yaml`
- **When** un editor abre `/conectores`
- **Then** GitHub aparece «sin configurar», con la variable `ENS_GITHUB_TOKEN`, el enlace a su doc y el botón «Configurar»; ningún estado es favorable

### Scenario: configurar un conector
- **Given** un editor y un activo `gh-org` en el inventario
- **When** envía el formulario de GitHub con activo `gh-org` y organización `acme`
- **Then** se escribe `connectors/github.yaml` válido para el cargador, la escritura queda auditada, y el bloque pasa a «configurado, sin recogidas todavía»

### Scenario: el formulario rechaza un secreto
- **Given** un editor
- **When** pone un valor con forma de token en cualquier campo (p. ej. `ghp_…`)
- **Then** el formulario se vuelve a mostrar con el error en ese campo, sin repetir el valor, y no se escribe nada

### Scenario: recogida con credencial ausente
- **Given** la última recogida de GitHub en `ERROR` porque `ENS_GITHUB_TOKEN` no está definida
- **When** se abre `/conectores`
- **Then** el bloque dice «credencial ausente: pon `ENS_GITHUB_TOKEN` en `deploy/.env` de la caja», con la fecha de la recogida, y no muestra el mensaje en bruto

### Scenario: recogida parcial
- **Given** una recogida en `SUCCESS` con una sección en `PERMISSION_DENIED`
- **When** se abre `/conectores`
- **Then** el bloque marca la recogida como parcial y nombra la sección sin permiso; los checks afectados se muestran como en el historial, sin presentarlos como resueltos

### Scenario: solo lectura
- **Given** un usuario sin permiso de edición
- **When** abre `/conectores`
- **Then** ve el mismo estado sin botones de configurar, y un POST directo devuelve 403 auditado sin escribir nada

### Scenario: configuración inválida en disco
- **Given** un `connectors/cloudflare.yaml` que el cargador rechaza
- **When** se abre `/conectores`
- **Then** el bloque dice «configuración inválida» con un mensaje fijo, sin valores del fichero, y el formulario no lo sobrescribe sin que el editor lo confirme

## Notas técnicas / riesgos

- Datos de conectores (verificado):
  - catálogo: `connectors/registry.py:29-43`;
  - modelos: `connectors/<name>/config.py`;
  - variables de entorno: en cada `connector.py`.
- La última recogida es el último manifiesto por `collector` (`evidence/importer.py:91-116`).
  Su artefacto se lee con las mismas defensas que `/copias`: clave canónica, fuente, sha256 y
  lectura acotada.
- Las categorías del motivo salen de patrones conocidos de los propios conectores. Ejemplos:
  `"<ENV> no está definida"`, `CredentialRejected`, el estado de sección y los códigos
  sintéticos del scheduler. Si no se reconoce, se usa «error del conector».
- Si falta la configuración o es inválida, el scheduler no importa nada
  (`scheduling/collect.py:391-398`). La página lo deduce del fichero, no de una recogida.
- `endpoint` tiene doc pero no entry point: no aparece.

## Referencias

- `docs/connectors/*.md`, `docs/decisions/0006-*`, `docs/decisions/0021-interfaz-web.md`,
  `docs/decisions/0022-roles-y-mfa-en-la-interfaz-web.md`
- `docs/specs/ui-inventario.md`, `docs/specs/ui-copias.md`
