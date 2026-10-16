# ENS en una caja para pymes

> Brief de producto para refinar con codex (Fase 1 del ciclo refine→ship).
> Sustituye la orientación "repo de cumplimiento personal" de
> `docs/ens-compliance-guide.md`. Mientras se refina, el backlog actual
> (#1-#18) queda congelado.

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
6. **Landing pública en el propio repo:** página estática con el estilo
   minimalista de Autarqui. Explica el producto, el camino BÁSICA/MEDIA y la
   instalación. Optimizada para búsquedas en español (metadatos, datos
   estructurados, contenido por medida o dimensión ENS). Usa iconografía
   propia por dimensión y tipo de medida, nunca logotipos ni distintivos
   oficiales del CCN.

**Usuario objetivo:** una pyme con alguien "apañado". Sabe seguir un asistente
y pegar un comando, pero no sabe de seguridad ni de ENS.

**Primera instalación real:** Autarqui. El producto se valida usándolo.

## Arquitectura propuesta

Una VM (o un portátil para evaluar) con `docker compose up`:

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
│   app (Python, núcleo ens_evidence)                                    │
│    · UI web (plantillas en servidor + htmx): asistente + seguridad     │
│    · servidor TLS de osquery (enroll, config, log, distributed r/w)    │
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
| App | Un contenedor: UI, API, servidor osquery, scheduler | Un proceso que operar, actualizar y respaldar. |
| UI | Renderizada en servidor con htmx; sin SPA ni build de Node | Lo más ligero; misma app y mismo contenedor. |
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

## Restricciones / no-goals

- **Restricciones:**
  - Perfil base dentro de un presupuesto acotado de recursos (objetivo a
    validar: ≤ 2 vCPU / 4 GB para la caja completa sin Wazuh).
  - Una sola organización por instalación.
  - Nunca declara conformidad automáticamente: la aprobación es humana y
    queda registrada.
  - Requisitos ENS solo desde fuentes oficiales: RD 311/2022, guías CCN-STIC y
    perfiles de cumplimiento específicos publicados por el CCN (art. 30).
  - Las reglas de seguridad de `docs/ens-compliance-guide.md` (líneas 269-282)
    siguen vigentes.
- **No-goals:**
  - Sustituir al auditor en MEDIA o ALTA.
  - Cubrir ALTA en la primera versión.
  - Multi-cliente para MSP o consultores.
  - Instalador gráfico o appliance precompilado (posterior).
  - Competir con las herramientas del CCN (PILAR, INES, LUCÍA…): integrarse o
    exportar donde tenga sentido.

## Criterios de aceptación (Given/When/Then)

### Scenario: instalación por un usuario apañado
- **Given** una VM Linux limpia con Docker
- **When** el usuario sigue la guía de instalación (un comando + asistente web)
- **Then** la caja queda operativa en menos de 1 hora, sin editar ficheros a mano

### Scenario: autoevaluación BÁSICA defendible
- **Given** una pyme con un sistema categorizado como BÁSICA
- **When** completa el asistente y conecta sus fuentes
- **Then** obtiene la declaración de aplicabilidad, la documentación organizativa, las evidencias técnicas trazables y el expediente de autoevaluación pendiente de su aprobación

### Scenario: preparación para auditoría MEDIA
- **Given** un sistema categorizado como MEDIA
- **When** se genera el expediente
- **Then** el producto indica explícitamente que hace falta una auditoría de certificación y entrega el paquete de evidencias organizado por medida

### Scenario: nunca un falso cumplimiento
- **Given** una medida aplicable sin evidencia o con checks en `FAIL`/`UNKNOWN`
- **When** se consulta el estado
- **Then** el estado global es `NOT READY` y la medida aparece como bloqueante

### Scenario: conectores enchufables
- **Given** una pyme que usa Microsoft 365 en lugar de Google Workspace
- **When** activa su conector de identidad
- **Then** los checks de identidad y MFA se evalúan sin cambiar el núcleo

### Scenario: pre-auditoría
- **Given** un expediente generado con una medida cuya política contradice la evidencia técnica
- **When** se ejecuta la pre-auditoría
- **Then** aparece un hallazgo clasificado que cita la medida, el documento y la evidencia, y el informe dice explícitamente que no sustituye a la auditoría

### Scenario: pre-auditoría sin LLM
- **Given** el adaptador LLM desactivado
- **When** se ejecuta la pre-auditoría
- **Then** la capa determinista se completa y la de juicio aparece como no ejecutada, sin bloquear nada

### Scenario: perfil ligero
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
- **Archivos afectados:** las specs de `docs/specs/` se replanifican tras el
  refine. Se mantienen en esencia el núcleo (modelos, catálogo, evidencias,
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
  - La landing en GitHub Pages exige repo público (o un plan de pago con repo
    privado): decidir cuándo se publica el código.
  - Uso de la marca ENS: qué se puede decir y mostrar sin usar logotipos ni
    distintivos del CCN ni sugerir certificación.
  - Modelo de sostenibilidad del proyecto (soporte, hosting gestionado…).
  - Qué guía CCN-STIC rige la auditoría del ENS y sus criterios de
    clasificación de hallazgos: la pre-auditoría debe seguirla, no inventarla.
  - Qué proveedores soporta el adaptador LLM en la primera versión y qué
    calidad mínima de modelo local hace fiable la capa de juicio (habrá que
    medirlo con expedientes de prueba).
