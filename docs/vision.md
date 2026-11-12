# ENS en una caja para pymes

> Brief de producto aprobado (2026-10-16), revisado con una ronda de critique
> de codex. Sustituye la orientación "repo de cumplimiento personal" de
> `docs/ens-compliance-guide.md`. El backlog se organiza por las fases de
> "Entrega por fases".

## Problema

Las empresas privadas que prestan servicios o proveen soluciones al sector
público tienen que cumplir el ENS (RD 311/2022, art. 2.3), y esa exigencia
puede extenderse a toda su cadena de suministro. Para una pyme, hoy eso
significa elegir entre consultoría cara, una herramienta GRC comercial o montar
a mano un stack open source pesado (Fleet, Wazuh…) sin saber qué evidencia
necesita.

Los sistemas de categoría BÁSICA solo necesitan una autoevaluación para
declarar la conformidad. Los de MEDIA y ALTA necesitan una auditoría de
certificación (art. 38.1). Falta una herramienta abierta que lleve a una pyme
de cero a una autoevaluación BÁSICA defendible, con evidencias reales, y que
deje el expediente preparado para que un auditor certifique MEDIA.

## Propuesta

Un único producto open source (AGPL-3.0) que se despliega con Docker Compose en
una VM, o en un portátil para evaluarlo. Integra:

1. **UI web única:** asistente de cumplimiento que guía paso a paso por
   alcance, categorización, análisis de riesgos y aplicabilidad, y genera la
   documentación organizativa (política de seguridad, roles, procedimientos)
   desde plantillas y respuestas. También es el panel de seguridad (alertas,
   agentes, incidentes), con un solo login y relacionando cada alerta con su
   medida.
2. **Motor de evidencias:** collectors de solo lectura como plugins, checks
   deterministas, manifiestos con hash y copia externa inmutable.
3. **Agente de endpoints ligero:** osquery con un servidor TLS propio dentro de
   la caja. La detección es un perfil opcional: manager de Wazuh sin indexer
   ni dashboard, más VictoriaLogs como índice de búsqueda.
4. **Informes y expediente:** informe diario y mensual, `assessment status`
   capaz de decir `NOT READY` y expediente de autoevaluación con las 11
   secciones de la guía.
5. **Pre-auditoría (opcional):** antes de una autoevaluación o de una
   auditoría de certificación, revisa todo el expediente en dos capas:
   - determinista (motor): evidencias ausentes o caducadas, hashes, checks en
     `FAIL`/`UNKNOWN`;
   - juicio (adaptador LLM agnóstico de proveedor): coherencia entre
     política y evidencias, calidad de la documentación, solidez de las
     justificaciones de aplicabilidad.

   La capa de juicio se define como un "audit pack" portable (criterios,
   prompts y esquema JSON de hallazgos versionados en el repo) que se ejecuta
   desde la propia caja con cualquier proveedor: APIs comerciales o modelos
   locales, para que los datos de la pyme no salgan de su infraestructura. Una
   skill para asistentes de código (Claude Code u otros) es solo un
   empaquetado más del mismo pack.

   Devuelve hallazgos clasificados como en una auditoría (no conformidad
   mayor, no conformidad menor, observación), con referencia a medida y
   evidencia. Nunca dictamina conformidad.
6. **Autocontenido:** cada release incluye todo lo necesario para instalar
   sin buscar software por fuera:
   - instalador de la caja con imágenes de contenedor de versión fijada y un
     bundle offline (imágenes exportadas + checksums) para instalar sin
     Internet;
   - paquetes de agente preconfigurados por sistema operativo (osquery y, en
     el perfil de detección, agente de Wazuh), generados por la caja con el
     secreto de alta de esa instalación;
   - guías paso a paso, en español y con capturas, para todo lo que no puede
     ir dentro: Google Workspace, Microsoft 365 / Entra ID (Azure), GitHub,
     Cloudflare y el proveedor S3 de la copia externa. Cada guía crea
     credenciales de solo lectura con privilegio mínimo, y la UI enlaza la
     guía y prueba la conexión al terminar.
7. **Landing pública en el propio repo:** página estática con el estilo
   minimalista de Autarqui. Explica el producto, el camino BÁSICA/MEDIA y la
   instalación. Optimizada para búsquedas en español (metadatos, datos
   estructurados, contenido por medida o dimensión ENS). Usa iconografía
   propia por dimensión y tipo de medida, nunca logotipos ni distintivos
   oficiales del CCN.

**Usuario objetivo:** una pyme con alguien "apañado". Sabe seguir un asistente
y pegar un comando, pero no sabe de seguridad ni de ENS.

**Primera instalación real:** Autarqui. El producto se valida usándolo.

## Arquitectura propuesta

Un VPS (o un portátil para evaluar, sin evidencia continua ni kit; ADR 0019) con `docker compose up`:

```
                  Internet
                     │
          (opcional) Cloudflare Tunnel   ← sin abrir puertos
                     │
┌────────────────────┼──────────────── VM / portátil (Docker) ──────────┐
│                    ▼                                                   │
│   caddy (TLS automático) ──► /agent/*  público (agentes osquery)       │
│                          └─► /         solo LAN/VPN/Access (UI)        │
│                    ▼                                                   │
│   agent (misma imagen, red interna; ADR 0020)                          │
│    · servidor TLS de osquery (enroll, config, log, distributed r/w)    │
│   app (Python, núcleo ens_evidence)                                    │
│    · UI web (plantillas en servidor + htmx): asistente + seguridad     │
│    · scheduler: collectors → checks → informes                         │
│    · pre-auditoría (capa determinista) + adaptador LLM                 │
│         │                         │                                    │
│   SQLite (estado,          /data/evidence (ficheros + manifiestos)     │
│   usuarios, respuestas)           │                                    │
│                                   │ restic cifrado, diario             │
│  Perfil "deteccion" (opcional):   ▼                                    │
│   · wazuh-manager (sin indexer ni dashboard) → alerts.json             │
│   · victorialogs (índice de alertas, ingesta /insert/jsonline)         │
│  Perfil "ia-local" (opcional):                                         │
│   · ollama                                                             │
└───────────────────────────────────┼────────────────────────────────────┘
                                    ▼
                    S3 externo con bloqueo de objetos (R2 u otro)
                    = copia independiente e inmutable
```

| Pieza | Decisión | Motivo |
|-------|----------|--------|
| Proxy | Caddy | TLS automático; separa la ruta pública de agentes de la UI privada. |
| App | Contenedor `app`: UI, API, scheduler | Un proceso que operar, actualizar y respaldar. |
| Agentes | Servicio `agent` aparte, de la misma imagen, en red interna sin salida y sin los secretos de los conectores (ADR 0020) | Aísla el proceso expuesto a Internet; desvío del «un contenedor» original. |
| UI | Renderizada en servidor con htmx; sin SPA ni build de Node | Lo más ligero; misma imagen que la app. |
| Estado | SQLite | Sin servidor de base de datos; backup = un fichero. |
| Evidencias | Ficheros locales + manifiestos sha256 | Simple; la inmutabilidad la aporta la copia externa. |
| Copia independiente | Restic cifrado a S3 externo con bloqueo de objetos | Copia independiente (guía, línea 727) y protección contra borrado. |
| Agentes | osquery; la UI genera el comando de instalación | Sin consola de Fleet ni servicios extra. |
| Exposición | Cloudflare Tunnel (recomendado) o 443 | Los portátiles reportan desde fuera; la UI nunca es pública. |
| Acceso UI | Usuarios locales con MFA obligatorio u OIDC (M365/Google) | La caja cumple lo que exige. |
| Detección | Perfil opcional: wazuh-manager + VictoriaLogs | Se elimina lo pesado de Wazuh (indexer, dashboard, Filebeat); el collector lee `alerts.json` y reenvía a VictoriaLogs. |
| IA | Adaptador agnóstico: API externa u Ollama | Datos que no salen de la pyme si se usa modelo local. |
| Landing | Estática en el repo (GitHub Pages) | Sin servidor; versionada con el producto. |

Fuera a propósito: Kubernetes, Fleet, MySQL/Postgres, Redis, MinIO, colas,
indexer y dashboard de Wazuh, Grafana.

**Para Autarqui (primera instalación):** VM pequeña en un servidor Linux
propio, publicada con Cloudflare Tunnel; osquery en el Mac y los servidores;
conectores GitHub, Cloudflare y Workspace; copia externa en R2; el perfil de
detección se decide con datos.

## Entrega por fases

Cada fase es usable por sí sola.

- **v0.1 — Autarqui hace su autoevaluación con la herramienta.**
  - Puerta inicial: alcance y categorización aprobados. Si el sistema de
    Autarqui resulta MEDIA, el resultado comprometido de v0.1 pasa a ser
    "preparado para certificación", no autoevaluación.
  - CLI; caja mínima (caddy + app en Compose); catálogo del Anexo II;
    plantillas organizativas.
  - **Matriz de cumplimiento:** por cada medida aplicable, requisito, evidencia
    técnica o manual, responsable y fecha de la última revisión. Es lo que
    exige la autoevaluación BÁSICA: "indicando si cada medida de seguridad
    está implantada y sujeta a revisión regular" (RD 311/2022, anexo III,
    2.1). Una medida sin evidencia o sin revisión vigente bloquea.
  - Gestión de incidentes: procedimiento, responsables y registro manual
    verificable (op.exp.7 y op.exp.9 aplican en BÁSICA). La automatización y
    la correlación quedan para v0.3.
  - Evidencias locales con copia restic cifrada a R2; conectores GitHub,
    Cloudflare, Workspace y Restic. Endpoints: script guiado que ejecuta
    consultas osquery en local y guarda el resultado como evidencia con hash.
  - Checks, informes, expediente, y pre-auditoría (capa determinista + audit
    pack con clientes Anthropic y OpenAI).
  - Sin UI web, sin servidor osquery y sin detección.
- **v0.2 — la usa una pyme que no es Autarqui.** UI web (htmx) con asistente y
  generador documental; login con MFA u OIDC; instalador de un comando;
  servidor osquery embebido y agentes; conector Microsoft 365; guías de
  servicios externos; revisión de las plantillas por un auditor ENS; landing
  mínima; kit de herramientas en el VPS con la primera ola (copias,
  claves, contraseñas y antimalware) y la guía de Access con el proveedor de
  identidad de la pyme).
- **v0.3 y siguientes.** Perfil de detección (wazuh-manager sin cabeza +
  VictoriaLogs + panel de seguridad); incidentes; bundle offline y releases
  firmadas; paquetes de agente por sistema operativo; landing completa; más
  proveedores LLM, incluido modelo local.

## Validación del auditor

La pre-auditoría se valida contra la legislación, no contra nuestra opinión:

1. **Autoauditoría:** los primeros expedientes son los de Autarqui y el de la
   propia caja, que también es un sistema con datos sensibles.
2. **Banco de defectos sembrados:**
   - Casos: copias del expediente de Autarqui con incumplimientos conocidos
     introducidos a propósito, expedientes correctos (sin defectos) y
     expedientes sintéticos de pymes distintas de Autarqui.
   - Cada defecto cita el texto literal del requisito del Anexo II que
     incumple. Esa cita es la respuesta correcta, no nuestra opinión.
   - Dos particiones: una de desarrollo y otra reservada que no se usa al
     ajustar prompts ni criterios. El umbral se mide solo en la reservada.
   - Se versiona en el repo como test de regresión del audit pack y para
     comparar proveedores LLM.
3. **Puntuación:**
   - Un defecto cuenta como detectado si un hallazgo cita su medida y su
     evidencia o documento. Los duplicados cuentan una vez.
   - Falsas alarmas: hallazgos emitidos que no corresponden a ningún defecto
     sembrado, sobre el total de hallazgos emitidos. Se mide también aparte
     sobre los expedientes correctos.
   - Cada ejecución registra la versión del audit pack, el proveedor, el
     modelo y la temperatura. Cada caso se repite 3 veces y cuenta la mediana.
4. **Umbral de cierre de v0.1** (cobertura del banco, no garantía universal):
   100 % de los defectos deterministas detectados; ≥ 80 % de los defectos de
   juicio con ≤ 20 % de falsas alarmas, por proveedor.
5. **Superar el banco no aprueba un expediente real.** Son dos cosas
   distintas: el banco valida la herramienta; el expediente lo aprueban las
   personas (ver "Regla de preparación").

## Regla de preparación

Una única regla decide el estado de un expediente:

- `READY` solo si toda medida aplicable tiene requisito satisfecho, evidencia
  vigente y revisión registrada, y no hay no conformidades abiertas de la
  pre-auditoría.
- Cualquier otra cosa es `NOT READY`, con la lista de bloqueos. Si la capa de
  juicio no se ejecutó, el estado indica "revisión humana pendiente" en lugar
  de omitirla en silencio.

Estados del expediente, en orden, cada uno con fecha y persona:
1. **Expediente generado:** lo produce la herramienta.
2. **Autoevaluación realizada:** por quien administra el sistema o en quien
   delegue (RD 311/2022, anexo III, 2.1).
3. **Autoevaluación analizada:** por el responsable de la seguridad, que
   eleva las conclusiones y las medidas correctoras al responsable del
   sistema.
4. **Declaración de conformidad:** solo BÁSICA y solo tras el paso 3.

## Copia independiente: qué garantiza y qué no

"Independiente e inmutable" solo se afirma cuando se ha probado:
- Credenciales de la copia distintas de las de cualquier fuente vigilada, y
  administración separada. Si el destino es R2, Cloudflare queda como un
  punto único de fallo; el modelo de amenaza lo debe recoger.
- Prueba de borrado: con las credenciales de la caja no se puede borrar ni
  sobrescribir dentro del periodo de retención.
- Prueba de restauración completa: evidencias, manifiestos y estado de la
  aplicación.
- Sin verificar: que restic funcione sobre un bucket con bloqueo de objetos de
  R2.

## Kit de herramientas

La herramienta no se limita a decir qué falta. Para cada carencia habitual
ofrece una herramienta open source y la despliega: si la pyme no tiene
copias de seguridad, le da un sistema para gestionarlas, y si no tiene
almacén de claves, se lo da también. Spec: `docs/specs/toolkit-server.md`.

- **Dónde vive (ADR 0019).** En el mismo VPS que la caja, siempre encendido:
  uno que contrata la pyme (OVH, Hetzner, Scaleway…) o un servidor propio.
  Cada herramienta es un perfil de Compose. El ordenador de la pyme solo abre
  la web; un portátil sirve para evaluar la herramienta, sin el kit.
- **Cómo se despliega.** La app genera la configuración y el comando, y el
  usuario los ejecuta. Nunca guarda credenciales del servidor ni lo cambia
  sin una acción explícita.
- **Contrato de cada herramienta:** servicio, conector de solo lectura, checks
  de uso real, runbook y aviso cuando falla. Instalar no es cumplir (regla 1).
- **Dos paneles en la web de la caja:** uno de acceso, desde el que se abre
  cada herramienta tras la misma protección que la web, y otro de
  monitorización con la salud de las herramientas, del VPS y de la caja,
  con avisos. La monitorización es operación: un verde no cumple ninguna
  medida.
- **Primera ola (v0.2):** restic + Backrest (mp.info.6), OpenBao (op.exp.10),
  Vaultwarden (apoyo parcial a op.acc.6) y
  antimalware (op.exp.6: ClamAV en Linux, nativo evidenciado por osquery en
  Windows y macOS).
- **El servidor no guarda sus propios rescates:** las copias van a S3 con
  bloqueo de objetos y las claves de desbloqueo quedan en manos de personas.

## Restricciones / no-goals

- **Restricciones:**
  - Perfil base dentro de un presupuesto acotado de recursos (objetivo a
    validar: ≤ 2 vCPU / 4 GB para la caja completa sin Wazuh).
  - Una sola organización por instalación.
  - Nunca declara conformidad automáticamente: la aprobación es humana y
    queda registrada (ver "Regla de preparación").
  - En v0.1 la capa de juicio envía datos del expediente a Anthropic u
    OpenAI: requiere consentimiento explícito, minimización y redacción de
    secretos. El modelo local llega en v0.3.
  - Requisitos ENS solo desde fuentes oficiales: RD 311/2022, guías CCN-STIC y
    perfiles de cumplimiento específicos publicados por el CCN (art. 30).
  - Las reglas de seguridad de `docs/ens-compliance-guide.md` (líneas 269-282)
    siguen vigentes.
- **No-goals:**
  - Sustituir al auditor en MEDIA o ALTA.
  - Cubrir ALTA en la primera versión.
  - Multi-cliente para MSP o consultores.
  - Instalador gráfico o imagen de VM precompilada (posterior; la primera
    versión instala con un comando, con o sin Internet).
  - Competir con las herramientas del CCN (PILAR, INES, LUCÍA…): integrarse o
    exportar donde tenga sentido.

## Criterios de aceptación (Given/When/Then)

Cada escenario indica la versión en la que debe cumplirse.

### Scenario: instalación por un usuario apañado (v0.2)
- **Given** una VM Linux limpia con Docker
- **When** el usuario sigue la guía de instalación (un comando + asistente web)
- **Then** la caja queda operativa en menos de 1 hora, sin editar ficheros a mano

### Scenario: autoevaluación BÁSICA defendible (v0.1)
- **Given** una pyme con un sistema categorizado como BÁSICA
- **When** completa alcance, aplicabilidad, documentación y conecta sus fuentes (CLI en v0.1, asistente en v0.2)
- **Then** obtiene la matriz de cumplimiento con cada medida aplicable implantada, revisada y con evidencia, y el expediente en estado "generado", a la espera de la autoevaluación, el análisis del responsable de la seguridad y la declaración

### Scenario: preparación para auditoría MEDIA (v0.1)
- **Given** un sistema categorizado como MEDIA
- **When** se genera el expediente
- **Then** el producto indica explícitamente que hace falta una auditoría de certificación y entrega el paquete de evidencias organizado por medida

### Scenario: nunca un falso cumplimiento (v0.1)
- **Given** una medida aplicable sin evidencia o con checks en `FAIL`/`UNKNOWN`
- **When** se consulta el estado
- **Then** el estado global es `NOT READY` y la medida aparece como bloqueante

### Scenario: conectores enchufables (v0.2)
- **Given** una pyme que usa Microsoft 365 en lugar de Google Workspace
- **When** activa su conector de identidad
- **Then** los checks de identidad y MFA se evalúan sin cambiar el núcleo

### Scenario: instalación sin Internet (v0.3)
- **Given** una VM sin salida a Internet y el bundle offline de una release
- **When** se ejecuta el instalador
- **Then** la caja arranca con las mismas versiones y checksums que la release publicada

### Scenario: configurar un servicio externo con la guía (v0.2)
- **Given** una pyme con Microsoft 365 y nadie que conozca Entra ID
- **When** sigue la guía enlazada desde la UI y pega las credenciales
- **Then** la UI prueba la conexión, confirma que los permisos son de solo lectura y avisa si sobran privilegios

### Scenario: pre-auditoría (v0.1)
- **Given** un expediente generado con una medida cuya política contradice la evidencia técnica
- **When** se ejecuta la pre-auditoría
- **Then** aparece un hallazgo clasificado que cita la medida, el documento y la evidencia, y el informe dice explícitamente que no sustituye a la auditoría

### Scenario: pre-auditoría sin LLM (v0.1)
- **Given** el adaptador LLM desactivado
- **When** se ejecuta la pre-auditoría
- **Then** la capa determinista se completa y el estado indica "revisión humana pendiente" para lo que cubría la capa de juicio

### Scenario: la caja pasa su propia pre-auditoría (v0.1)
- **Given** la caja instalada como sistema dentro del alcance de Autarqui
- **When** se ejecuta la pre-auditoría sobre su expediente
- **Then** su expediente cumple la regla de preparación (`READY`); si no, v0.1 no se cierra

### Scenario: defectos sembrados por encima del umbral (v0.1)
- **Given** el banco de defectos sembrados de la versión actual del audit pack
- **When** se ejecuta la pre-auditoría con cada proveedor soportado
- **Then** detecta el 100 % de los deterministas y ≥ 80 % de los de juicio con ≤ 20 % de falsas alarmas, y el resultado queda registrado por proveedor

### Scenario: perfil ligero (v0.2)
- **Given** la instalación base sin el perfil de detección
- **When** recoge datos de hasta 25 endpoints
- **Then** se mantiene dentro del presupuesto de recursos objetivo

## Riesgos / archivos afectados / preguntas abiertas

- **Riesgos:**
  - Responsabilidad: los usuarios pueden leer "cumplo" donde el producto dice
    "preparado". Hacen falta avisos legales y un lenguaje inequívoco.
  - Las plantillas organizativas mal adaptadas pueden producir documentación
    de cumplimiento "de papel".
  - Mantenimiento: cambios normativos, nuevas guías CCN y APIs de terceros.
  - Seguridad del propio producto: concentra datos sensibles de la pyme, así
    que es un objetivo valioso.
  - Un portátil como host de producción no da evidencia continua.
- **Archivos afectados:** las specs de `docs/specs/` se replanifican por
  fases. Se mantienen en esencia el núcleo (modelos, catálogo, evidencias,
  collectors, informes) y cambian el empaquetado, la UI, los conectores, el
  osquery-edge (pasa de Worker a módulo de la app, con el Worker como
  opción), el storage (R2 pasa a destino de la copia externa) y Wazuh (sin
  indexer ni dashboard). Se añaden la landing y el brief de diseño
  (`.claude/design-briefs/`).
- **Preguntas abiertas:**
  - ¿Existe un perfil de cumplimiento específico del CCN aplicable a pymes o a
    proveedores del sector público? Cambiaría el catálogo base.
  - Estado y licencia de las herramientas del CCN (PILAR/µPILAR, INES, LUCÍA)
    y viabilidad de exportar a ellas.
  - Qué conectores priorizar (Microsoft 365, Google Workspace, GitHub,
    Cloudflare…): hace falta validar qué usan de verdad las pymes objetivo.
  - Stack de la UI y si el núcleo Python también sirve el backend web.
  - RAM real del manager de Wazuh sin indexer y de VictoriaLogs con el
    volumen de alertas de una pyme (el quickstart de 8 GiB incluye indexer y
    dashboard).
  - Licencia de la edición libre de VictoriaLogs y su compatibilidad con
    AGPL-3.0.
  - Nombre del producto y dominio de la landing.
  - Firma de las releases y de los paquetes de agente (origen verificable de
    los instalables) y política de actualizaciones de los componentes
    incluidos.
  - Licencias de redistribución de cada componente que va dentro del bundle
    (osquery, Wazuh, VictoriaLogs, Caddy…).
  - Mantenimiento de las guías externas: las consolas de Google, Microsoft y
    Cloudflare cambian; hace falta un proceso para detectar guías
    desactualizadas.
  - La landing en GitHub Pages exige repo público (o un plan de pago con repo
    privado): decidir cuándo se publica el código.
  - Uso de la marca ENS: qué se puede decir y mostrar sin usar logotipos ni
    distintivos del CCN ni sugerir certificación.
  - Modelo de sostenibilidad del proyecto (soporte, hosting gestionado…).
  - Qué guía CCN-STIC rige la auditoría del ENS y sus criterios de
    clasificación de hallazgos: la pre-auditoría debe seguirla, no inventarla.
  - Qué calidad mínima de modelo local hace fiable la capa de juicio: se mide
    con el banco de defectos sembrados. Los proveedores de v0.1 están
    decididos (Anthropic y OpenAI).
  - Ollama u otro servidor local mediante el `base_url` del cliente
    OpenAI-compatible: sin verificar.
