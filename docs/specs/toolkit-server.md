---
codename: ens-compliance
title: "Kit de herramientas: una herramienta open source para cada carencia"
type: epic
status: active
priority: P1
size: XL
issue: null
prs: []
related_issues: [65]
depends_on: [box-packaging, installer, web-ui-flujo]
supersedes: []
related_specs: [detection-profile, collector-restic, osquery-agents, setup-guides]
created: 2026-11-09
updated: 2026-11-09
---

# Kit de herramientas: una herramienta open source para cada carencia

## Contexto / problema

La herramienta prepara la autoevaluación y recoge evidencias, pero no da a la pyme lo que le falta para cumplir. Si no tiene copias de seguridad, almacén de claves o doble factor, la matriz se lo dice y ahí se queda. Lo pidió el usuario el 2026-11-09: «No tienes copias de seguridad: aquí tienes un sistema para gestionarlas. No tienes almacén de claves: aquí lo tienes. Y que se despliegue con la misma app».

Las herramientas tienen que estar siempre encendidas, igual que el scheduler, el servidor osquery y la detección. Por eso la caja y el kit viven en el mismo servidor: un VPS que contrata la pyme (OVH, Hetzner, Scaleway u otro) o un servidor propio. Cada herramienta es un perfil de Compose más, y el ordenador de la pyme solo abre la web (ADR 0019).

## Scope

**Entra:**
- **Recomendación desde la matriz.** Cada medida sin cubrir que tenga herramienta en el kit dice cuál, qué resuelve y qué no, y cuánto cuesta operarla.
- **Despliegue.** Cada herramienta es un perfil de Compose de la caja. La app genera la configuración de las elegidas y el comando para activarlas, y el instalador (`installer`) prepara el VPS: cortafuegos, actualizaciones automáticas de seguridad y copia propia. La app no guarda credenciales del servidor y no lo modifica sin una acción explícita del usuario (reglas 6 a 8).
- **Contrato por herramienta**, igual para todas: (1) servicio en el Compose, (2) conector de solo lectura que recoge evidencia de uso, (3) checks que comprueban uso real, no instalación, (4) runbook de operación, actualización, copia y restauración, (5) aviso en la web cuando deja de funcionar.
- **Primera ola de herramientas:**

  | Carencia | Herramienta | Medidas que apoya | Evidencia que recoge |
  |---|---|---|---|
  | Copias de seguridad | restic + Backrest (panel) | mp.info.6 (base; R1 desde MEDIO: prueba de restauración) | última copia correcta, retención, última prueba de restauración |
  | Claves y secretos de sistemas | OpenBao | op.exp.10 | inventario de claves, rotaciones, política de acceso, auditoría activada |
  | Contraseñas del equipo | Vaultwarden | apoyo parcial a op.acc.6 (op.acc.6.4 y R1): no es el mecanismo de autenticación del sistema | usuarios con doble factor, cuentas sin usar |
  | Identidad y doble factor | Authentik o Keycloak | op.acc.6 (refuerzos de doble factor), y op.acc.5 solo si hay usuarios externos; vale solo para los servicios que federan contra él | MFA obligatorio, usuarios activos, altas y bajas, servicios federados |
  | Código dañino | ClamAV en servidores Linux; en Windows y macOS, el antimalware nativo evidenciado por osquery | op.exp.6 (ClamAV sin análisis en acceso no cubre la protección en tiempo real de op.exp.6.5) | firmas al día y últimos análisis por equipo |

  osquery (op.exp.1, op.exp.2) ya existe y no cambia. La detección (wazuh-manager y VictoriaLogs, op.exp.8, op.mon.1) sigue en `detection-profile` (v0.3) y se desplegará en este servidor.

**No entra:**
- Crear la cuenta del proveedor, contratar el VPS o pagarlo: lo hace la pyme, con una guía.
- Operar las herramientas por la pyme (ni hosting gestionado).
- Migrar datos desde herramientas que la pyme ya tenga. Si ya tiene una, se conecta su evidencia; no se sustituye.
- Detección y SIEM (`detection-profile`).

## Criterios de aceptación

### Scenario: la matriz propone la herramienta
- **Given** una medida aplicable sin evidencia que tiene herramienta en el kit (p. ej. op.exp.10)
- **When** se abre su ficha en la matriz
- **Then** se ve la herramienta propuesta, qué cláusulas cubre y cuáles no, la RAM medida que necesita y un enlace para añadirla al servidor

### Scenario: instalar no es cumplir
- **Given** una herramienta del kit recién desplegada y sin uso (p. ej. OpenBao sin claves)
- **When** corren los checks
- **Then** la medida no pasa a cumplida, y el check dice qué uso falta (regla 1)

### Scenario: el servidor se despliega con un comando
- **Given** la caja instalada en un VPS y una selección de herramientas del kit
- **When** el usuario ejecuta el comando que da la app
- **Then** las herramientas elegidas arrancan con TLS, la app las ve y cada una deja su primera evidencia (en CI: el paquete generado pasa `docker compose config` y un arranque en contenedor; en VPS real: prueba manual registrada, como en `audit-benchmark`)

### Scenario: la app no toca el servidor sin permiso
- **Given** el kit en marcha
- **When** la app recoge evidencias
- **Then** solo usa credenciales de lectura, y cualquier cambio (añadir, actualizar o quitar una herramienta) necesita una acción explícita del usuario

### Scenario: el servidor no guarda sus propios rescates
- **Given** el VPS con la caja, copias y almacén de claves
- **When** el servidor se pierde entero
- **Then** las copias están fuera, en S3 con bloqueo de objetos; el runbook restaura el servidor en una máquina nueva; y la web registra quiénes custodian las partes de desbloqueo de OpenBao y desde cuándo, como revisión humana (nunca se guardan en el servidor ni en la app)

### Scenario: una herramienta caída se ve
- **Given** una herramienta que deja de responder, o una copia que no se hace en la periodicidad fijada en la política de copias
- **When** la app recoge evidencias
- **Then** la web lo muestra como problema con el enlace a su runbook, y la medida pierde la evidencia vigente

### Scenario: el paquete no deja secretos en la app
- **Given** la configuración generada de una herramienta (con secretos de alta, claves S3 y contraseñas iniciales)
- **When** el usuario lo descarga
- **Then** se descarga una sola vez, la app no conserva sus secretos y no entran en evidencias, expediente ni logs

## Notas técnicas / riesgos

- **Decidido (ADR 0019):** la caja y el kit en el mismo VPS; el portátil, solo para evaluar, sin kit.
- **Concentración.** El servidor guarda las claves y las copias de la pyme, así que es un objetivo valioso. Necesita un bastionado mínimo (SSH solo con clave, cortafuegos, actualizaciones automáticas, sin paneles públicos: acceso por Cloudflare Access o VPN), y la review de seguridad es obligatoria en cada herramienta.
- **Carga de operación.** Cada herramienta añade actualizaciones, copias y usuarios. El kit solo vale si cada una trae runbook y vigilancia. Se empieza con las imprescindibles para BÁSICA.
- **Licencias.** Verificar la licencia y las condiciones de redistribución de cada herramienta antes de incluirla (Backrest, OpenBao, Vaultwarden, Authentik o Keycloak, ClamAV).
- **RAM.** Medir el consumo real del conjunto en un VPS pequeño (2-4 GiB) antes de fijar el tamaño recomendado.
- Medidas y cláusulas de la tabla comprobadas contra el texto de `src/ens_evidence/catalog/data/{op,mp}.yaml` (op.exp.6.5 en op.yaml:1742, mp.info.6 R1 en mp.yaml:2195). op.cont.3 no aplica: es la prueba del plan de continuidad y es n.a. en BAJO y MEDIO.
- **Licencias e imágenes:** el paquete referencia imágenes upstream fijadas por digest, no las redistribuye.
- **Descargo:** la UI dice que el paquete es una propuesta que la pyme revisa y opera; el servidor entra en el alcance como un activo más, y entonces se autoevalúa como cualquier otro.
- **OpenBao:** sin auto-unseal; el root token se revoca tras la instalación, y la app lee con un token de política de solo lectura y vida corta, que se renueva.

## Tasks (no autoritativo — el board manda)

- [ ] Perfiles del kit en el Compose de la caja; bastionado del VPS en el instalador; runbook de restauración completa del VPS
- [ ] Registro del kit: medida → herramienta, y recomendación en la matriz
- [ ] Copias: restic + Backrest, conector y checks (copia vigente y prueba de restauración)
- [ ] Claves: OpenBao con desbloqueo Shamir en manos humanas, conector y checks
- [ ] Contraseñas: Vaultwarden, conector y checks
- [ ] Identidad: elegir entre Authentik y Keycloak; conector y checks; login de la propia app por OIDC contra ella
- [ ] Código dañino: ClamAV en el instalador de agentes y su evidencia por osquery
- [ ] Guías para contratar un VPS (OVH, Hetzner, Scaleway)

## Referencias

- docs/decisions/0019-caja-y-herramientas-en-un-vps.md
- docs/vision.md, secciones «Arquitectura propuesta» y «Kit de herramientas»
- docs/specs/detection-profile.md, installer.md, collector-restic.md, osquery-agents.md
