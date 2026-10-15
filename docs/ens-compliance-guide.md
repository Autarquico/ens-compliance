# ENS Compliance Guide

> Guía de implementación para construir un repositorio privado `ens` que
> centralice documentación, controles, evidencias, autoevaluación y
> automatización ENS usando Claude Code.

## 1. Objetivo y arquitectura

El objetivo es construir un único repositorio privado `ens` con
documentación, infraestructura como código, recolectores,
comprobaciones, evidencias, informes y procedimientos.

La evaluación técnica será determinista. El LLM será opcional e
intercambiable: podrá interpretar hallazgos, resumir incidentes y ayudar
a redactar informes, pero nunca declarar automáticamente la conformidad.

``` text
                         GITHUB
                    Repositorios / CI
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
   CLOUDFLARE            FLEET                WAZUH
   WAF / Access         Inventario           Seguridad
   DNS / Tunnel         Endpoints            Incidentes
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                   GOOGLE WORKSPACE
                     Identidad / MFA
                            │
                            ▼
                   ENS EVIDENCE ENGINE
                 ┌────────────────────┐
                 │ Collectors         │
                 │ Normalización      │
                 │ Checks             │
                 │ Evidencias         │
                 │ Correlación        │
                 │ Informes           │
                 └─────────┬──────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
         GITHUB ENS                 S3 CIFRADO
         Documentación              Evidencias
         Controles                  Registros
         Políticas                  Artefactos
         Índices                    Histórico
             │                           │
             └─────────────┬─────────────┘
                           ▼
                    INFORME ENS
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
            AUTOEVALUACIÓN       DESVIACIONES
                                     │
                                     ▼
                              Notificaciones + IA
```

## 2. Fases

  Fase   Objetivo                              Entregable
  ------ ------------------------------------- ---------------------------------
  0      Descubrir infraestructura y alcance   Inventario y arquitectura
  1      Crear repo y entorno Claude Code      Repo `ens`
  2      Categorización y matriz ENS           Riesgos y aplicabilidad
  3      Normalizar seguridad                  GitHub, Cloudflare, Workspace
  4      Fleet y Wazuh                         Agentes, inventario y detección
  5      Motor de evidencias                   Collectors, checks y storage
  6      Automatización                        Jobs, informes y alertas
  7      Incidentes + IA                       Análisis y registro
  8      Pruebas y autoevaluación              Expediente de autoevaluación

Estimación orientativa: 6--10 semanas a tiempo parcial dependiendo de lo
que ya esté desplegado.

------------------------------------------------------------------------

# FASE 0 --- Descubrimiento

## Inventario mínimo

  Elemento         Datos
  ---------------- -------------------------------------------
  Portátiles       ID, SO, propietario, cifrado, uso
  Servidores       Hostname, función, SO, entorno, ubicación
  Aplicaciones     Servicio, dominio, repo, base de datos
  Bases de datos   Motor, ubicación, datos, backup
  GitHub           Cuenta/organización, repos, Actions
  Cloudflare       Cuenta, zonas, Tunnel, WAF, Access
  Workspace        Usuarios, admins, MFA
  Bitwarden        Modalidad, recuperación, MFA
  Storage          Buckets, ubicación, cifrado, acceso
  Backups          Origen, destino, frecuencia, restauración
  Proveedores      Servicio, función, dependencia

Nunca introducir secretos o tokens en el inventario.

## Alcance

Definir exactamente el sistema que se pretende declarar conforme al ENS.
Posible descripción inicial:

> Sistema de información que soporta el desarrollo, despliegue,
> operación y mantenimiento de las aplicaciones y servicios prestados a
> clientes.

Debe validarse. La categoría ENS depende del impacto en las dimensiones
de seguridad, no del tamaño de la empresa.

## Prompt de descubrimiento

``` text
Actúa como arquitecto de seguridad y especialista técnico en ENS.

Vamos a construir un repositorio de cumplimiento ENS para una
empresa unipersonal de desarrollo de software en España.

Infraestructura conocida:
- Equipos macOS y Linux.
- Servidores Linux propios.
- Aplicaciones publicadas mediante Cloudflare.
- Código fuente en GitHub.
- Google Workspace para identidad y correo.
- Bitwarden para credenciales.
- Fleet para inventario.
- Wazuh para detección y monitorización.
- Restic y almacenamiento S3 compatible para backups.

No asumas que todos los componentes están desplegados.

Tu primera tarea es:
1. Crear un cuestionario de descubrimiento.
2. Proponer un inventario de activos en YAML.
3. Definir una metodología de delimitación del alcance.
4. Identificar dependencias y terceros.
5. Señalar las decisiones que requieren intervención humana.

No declares ninguna medida ENS como cumplida.
No realices cambios en infraestructura.
```

Criterio de finalización: activos y dependencias del alcance
identificados.

------------------------------------------------------------------------

# FASE 1 --- Repositorio `ens`

## Crear repo privado

``` bash
mkdir ens
cd ens
git init -b main

gh auth login
gh repo create ens --private --source=. --remote=origin
```

Revisar MFA/passkeys, rulesets de `main`, Dependabot, seguridad
disponible, permisos de Actions y tokens autorizados.

## Claude Code

Seguir la documentación vigente de Claude Code.

``` bash
cd ens
claude
```

Después:

``` text
/init
```

No usar modos que eliminen confirmaciones de seguridad.

## Estructura

``` text
ens/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── deployment/
│   ├── operations/
│   └── runbooks/
├── scope/
│   ├── system.yaml
│   ├── assets.yaml
│   ├── services.yaml
│   ├── dependencies.yaml
│   └── categorization.yaml
├── risk/
│   ├── methodology.md
│   ├── register.yaml
│   └── treatment-plan.yaml
├── controls/
│   ├── catalog/
│   ├── applicability.yaml
│   ├── implementations/
│   └── mappings/
├── policies/
├── procedures/
├── src/
│   └── ens_evidence/
│       ├── cli.py
│       ├── config.py
│       ├── models.py
│       ├── collectors/
│       │   ├── github.py
│       │   ├── cloudflare.py
│       │   ├── fleet.py
│       │   ├── wazuh.py
│       │   ├── workspace.py
│       │   └── restic.py
│       ├── checks/
│       ├── storage/
│       ├── reports/
│       ├── incidents/
│       └── notifications/
├── config/
│   ├── sources.yaml
│   ├── schedules.yaml
│   └── retention.yaml
├── infrastructure/
│   ├── docker/
│   ├── ansible/
│   ├── terraform/
│   └── systemd/
├── evidence/
│   ├── manifests/
│   └── README.md
├── assessments/
├── reports/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── security/
└── .github/
    └── workflows/
```

Las evidencias sensibles no se almacenan directamente en Git.

## `CLAUDE.md`

``` markdown
# ENS Project — Claude Code Instructions

## Purpose

This repository implements documentation, evidence collection,
technical verification and reporting for an ENS-scoped system.

## Non-negotiable rules

1. Never mark an ENS measure compliant solely because a tool is installed.
2. Use official RD 311/2022 Annex II identifiers and requirements.
3. Never invent legal requirements, control IDs or evidence.
4. Distinguish PASS, FAIL, ERROR, UNKNOWN, REVIEW and NOT_APPLICABLE.
5. Never treat missing evidence as PASS.
6. Never store credentials, tokens, private keys or raw sensitive logs in Git.
7. Never modify production infrastructure without explicit approval.
8. Never execute destructive operations automatically.
9. All collectors must be read-only by default.
10. All checks must have automated tests.
11. All generated reports must identify sources and timestamps.
12. Human review is required for organizational controls and final assessments.

## Architecture

Python 3.12+
Typed code
Pydantic
Typer
HTTPX
Pytest
Ruff
S3-compatible evidence storage

## Development workflow

- Propose a plan before implementing each phase.
- Work in small commits.
- Write tests before or alongside implementation.
- Use fixtures for external APIs.
- Never require production credentials for unit tests.
- Document deployment, rollback and recovery.
```

------------------------------------------------------------------------

# FASE 2 --- Matriz ENS

Fuentes primarias:

-   Real Decreto 311/2022.
-   Anexo II.
-   Guías oficiales CCN-STIC de la serie 800.

No usar requisitos generados por un LLM sin contrastarlos con fuentes
oficiales.

## Ficha de medida

``` yaml
id: "op.pl.1"
name: "Análisis de riesgos"

source:
  regulation: "RD 311/2022"
  annex: "II"

applicability:
  status: "REVIEW"
  justification: null

implementation:
  description: null
  owner: null

evidence:
  required:
    - "risk-analysis-document"
    - "risk-review-record"

verification:
  type: "manual"

assessment:
  status: "NOT_ASSESSED"
  reviewed_at: null
```

Los requisitos exactos deben extraerse del texto oficial.

## Estados

Aplicabilidad:

``` text
APPLICABLE
NOT_APPLICABLE
UNDETERMINED
```

Check técnico:

``` text
PASS
FAIL
ERROR
UNKNOWN
```

Evaluación:

``` text
COMPLIANT
NON_COMPLIANT
REQUIRES_REVIEW
NOT_ASSESSED
```

Un `PASS` técnico no implica automáticamente `COMPLIANT`.

## Riesgos

Documentar activos, servicios, dependencias, amenazas, impactos,
dimensiones ENS, riesgo inherente, medidas, riesgo residual, tratamiento
y aceptación.

Entregables:

``` text
scope/categorization.yaml
risk/register.yaml
controls/catalog/*.yaml
controls/applicability.yaml
```

------------------------------------------------------------------------

# FASE 3 --- GitHub

Revisar:

1.  MFA/passkeys.
2.  Visibilidad de repos.
3.  Colaboradores.
4.  Rulesets.
5.  GitHub Actions.
6.  Dependabot.
7.  Secret scanning disponible.
8.  Dependencias.
9.  Entornos de despliegue.
10. Tokens/apps.

Ejemplo normalizado:

``` json
{
  "repository": "example/app",
  "visibility": "private",
  "default_branch": "main",
  "branch_rules": {"configured": true},
  "security_features": {
    "dependabot": "enabled",
    "secret_scanning": "unknown"
  },
  "collected_at": "2026-10-15T10:00:00Z"
}
```

Si algo no puede comprobarse: `UNKNOWN`.

Prompt:

``` text
Implementa el collector de GitHub.

Requisitos:
- GitHub App o token read-only.
- Paginación.
- Visibilidad, ramas, rulesets y configuración de seguridad.
- Permisos/colaboradores cuando la API lo permita.
- Diferenciar permission denied de configuración inexistente.
- Modelos Pydantic.
- Nunca registrar tokens.
- Tests con fixtures/mocks.
- Documentar permisos mínimos.

No implementes cambios de configuración.
```

------------------------------------------------------------------------

# FASE 4 --- Cloudflare

Usar Cloudflare como capa perimetral.

Revisar por servicio:

-   DNS.
-   TLS.
-   WAF.
-   DDoS.
-   Tunnel.
-   Access.
-   Audit logs.
-   Recuperación de cuenta.

Cloudflare Tunnel no implica por sí solo autenticación mediante Access.

Collector:

``` text
Cloudflare
├── Zones
├── DNS
├── WAF
├── Access applications
├── Access policies
├── Tunnels
├── TLS
└── Audit logs
```

Usar API tokens con privilegio mínimo/read-only. No Global API Key.

Si se usa Terraform, migrar gradualmente. Antes de `terraform apply`:

1.  Inventariar/importar.
2.  `terraform plan`.
3.  Revisar deletes.
4.  Backup.
5.  Aprobación humana.
6.  Aplicar.

------------------------------------------------------------------------

# FASE 5 --- Google Workspace

Revisar:

-   Usuarios.
-   Suspendidos.
-   Administradores.
-   MFA/2SV.
-   Recuperación.
-   OAuth apps.
-   Seguridad.
-   Eventos administrativos.

El collector debe distinguir:

``` text
PASS
FAIL
UNKNOWN
PERMISSION_DENIED
NOT_SUPPORTED
```

No conceder privilegios administrativos generales al collector cuando
pueda funcionar con scopes de lectura limitados.

------------------------------------------------------------------------

# FASE 6 --- Fleet

Fleet: inventario, visibilidad y compliance de endpoints.

Arquitectura típica:

``` text
Fleet
├── Database
├── Redis/Valkey compatible
├── TLS
└── fleetd/osquery
```

Pasos:

1.  Preparar VM/host.
2.  Persistencia.
3.  Base de datos.
4.  Redis/compatible.
5.  Fleet.
6.  TLS.
7.  Proteger administración.
8.  Backup.
9.  Endpoint piloto.
10. Despliegue general.

Checks:

``` text
HOST-001  Inventariado
HOST-002  Agente activo
HOST-003  SO soportado
HOST-004  Cifrado
HOST-005  Firewall
HOST-006  Updates
HOST-007  Software inventory
```

Validar cada check contra capacidades reales de la edición instalada.

------------------------------------------------------------------------

# FASE 7 --- Wazuh

Wazuh será el núcleo de monitorización/detección.

``` text
macOS/Linux/Servers
        │
        ▼
   Wazuh Manager
        │
        ▼
      Indexer
        │
        ▼
     Dashboard
```

Pasos:

1.  Preparar VM Linux.
2.  Dimensionar.
3.  Instalar método de despliegue elegido.
4.  Versión estable soportada.
5.  Certificados.
6.  Credenciales.
7.  Firewall.
8.  Desplegar.
9.  Health checks.
10. Backup.
11. Proteger dashboard.
12. No exponer APIs administrativas a Internet.

Agentes en:

-   macOS.
-   Linux.
-   App servers.
-   DB servers.
-   Hosts de gestión según alcance.

No instalar necesariamente un agente dentro de cada contenedor.

Activar gradualmente:

-   Auth events.
-   File integrity.
-   Security configuration assessment.
-   Vulnerabilidades/inventario disponibles.
-   Logs de aplicación.
-   Eventos administrativos.
-   Agentes desconectados.

Pruebas:

1.  Alta de agente.
2.  Desconexión detectada.
3.  Evento controlado.
4.  Alerta generada.
5.  Incidente registrado.
6.  Notificación.

------------------------------------------------------------------------

# FASE 8 --- `ens-evidence`

Stack:

  Componente   Tecnología
  ------------ --------------------
  Lenguaje     Python 3.12+
  Modelos      Pydantic
  CLI          Typer
  HTTP         HTTPX
  Config       YAML
  Tests        Pytest
  Calidad      Ruff
  Storage      S3 compatible
  Informes     HTML + JSON + PDF
  Scheduler    systemd
  IA           Adaptador opcional

Sin DB inicialmente.

## Collector contract

``` json
{
  "schema_version": "1.0",
  "source": "cloudflare",
  "collector": "cloudflare_access",
  "collected_at": "2026-10-15T10:00:00Z",
  "asset_id": "APP-001",
  "data": {
    "hostname": "admin.example.com",
    "access_application_found": true,
    "policies": 2
  },
  "collection_status": "SUCCESS"
}
```

## Check contract

``` json
{
  "check_id": "CF-ACCESS-001",
  "asset_id": "APP-001",
  "status": "PASS",
  "evaluated_at": "2026-10-15T10:01:00Z",
  "evidence_ids": ["EV-20261015-0001"],
  "reason": "Existe una aplicación Access con una política restrictiva"
}
```

## Evidence manifest

``` yaml
id: EV-20261015-0001
source: cloudflare
asset_id: APP-001
collected_at: "2026-10-15T10:00:00Z"

artifact:
  storage: s3
  key: evidence/2026/10/15/EV-20261015-0001.json.enc
  sha256: "<hash-real>"

collector:
  name: cloudflare_access
  version: "0.1.0"

review:
  status: pending
  reviewer: null
```

Un hash aporta verificación de integridad; no sustituye controles contra
borrado/modificación del almacenamiento.

------------------------------------------------------------------------

# FASE 9 --- Storage

``` text
ens-evidence/
├── raw/
├── normalized/
├── manifests/
├── reports/
└── assessments/
```

Controles:

-   Privado.
-   Cifrado.
-   Privilegio mínimo.
-   Credenciales separadas.
-   Versionado si existe.
-   Retención.
-   Copia independiente.
-   Restore tests.

No asumir que todos los proveedores S3 soportan Object Lock/WORM
equivalente.

------------------------------------------------------------------------

# FASE 10 --- CI y scheduling

GitHub Actions:

``` text
Tests
Lint
YAML/schema validation
Docs
Build de informes sin secretos
```

Ejecutor interno:

``` text
Fleet API
Wazuh API
Restic
Internal services
Evidence collection
S3 upload
```

## systemd

``` ini
[Unit]
Description=ENS evidence collection

[Service]
Type=oneshot
User=ens-evidence
Group=ens-evidence
WorkingDirectory=/opt/ens
ExecStart=/opt/ens/.venv/bin/ens-evidence collect --all
NoNewPrivileges=true
```

``` ini
[Unit]
Description=Run ENS evidence collection daily

[Timer]
OnCalendar=daily
Persistent=true
Unit=ens-evidence.service

[Install]
WantedBy=timers.target
```

Desplegar con Ansible o procedimiento versionado.

CLI:

``` bash
ens-evidence inventory
ens-evidence collect github
ens-evidence collect cloudflare
ens-evidence collect fleet
ens-evidence collect wazuh
ens-evidence collect workspace
ens-evidence collect restic
ens-evidence collect --all
ens-evidence check --all
ens-evidence report daily
ens-evidence report monthly
ens-evidence assessment status
ens-evidence evidence verify EV-20261015-0001
```

------------------------------------------------------------------------

# FASE 11 --- Restic / recuperación

Comprobar:

``` text
BACKUP-001  Backup reciente
BACKUP-002  Sin errores
BACKUP-003  Repository accessible
BACKUP-004  Integrity check
BACKUP-005  Restore test vigente
BACKUP-006  Copia externa
```

Ejemplo:

``` yaml
test_id: RESTORE-2026-001
system: APP-001
performed_at: "2026-10-15"

backup:
  snapshot_id: "<id>"

result:
  restored: true
  integrity_verified: true
  application_started: true

review:
  approved: true
```

Restaurar siempre en destino aislado.

------------------------------------------------------------------------

# FASE 12 --- Incidentes + IA

``` text
Wazuh
  ↓
Alert
  ↓
Normalization
  ↓
Deterministic correlation
  ↓
Incident
  ├── Notification
  ├── Evidence
  └── Optional LLM analysis
           ↓
    Recommendations
           ↓
      Human review
```

Modelo:

``` yaml
id: INC-2026-001
status: open
severity: high
detected_at: "2026-10-15T12:00:00Z"

assets:
  - APP-001

source:
  system: wazuh
  alert_ids:
    - "<alert-id>"

analysis:
  summary: null
  recommendations: []

actions: []

closure:
  closed_at: null
  conclusion: null
```

LLM:

-   Intercambiable.
-   Logs = input no confiable.
-   Minimizar datos.
-   Redactar secretos.
-   Salida estructurada.
-   Referencias a evidencia.
-   Sin acciones destructivas automáticas.
-   El sistema funciona sin LLM.

------------------------------------------------------------------------

# FASE 13 --- Informes

## Diario

``` text
ENS EVIDENCE — DAILY

Assets: 12
Assets without agent: 1

Checks:
PASS: 42
FAIL: 2
ERROR: 0
UNKNOWN: 3

Critical alerts: 0
Open incidents: 1
```

Valores ilustrativos.

## Mensual

Incluir:

-   Inventario.
-   Cambios.
-   Desviaciones.
-   Evidencias.
-   Incidentes.
-   Vulnerabilidades.
-   Backups.
-   Restore tests.
-   Access reviews.
-   Pendientes.
-   Corrective actions.

## Autoevaluación

``` text
1. Identificación
2. Alcance
3. Categorización
4. Riesgos
5. Declaración de aplicabilidad
6. Metodología
7. Resultado por medida
8. Evidencias
9. Incumplimientos/tratamiento
10. Conclusiones
11. Aprobación/fecha
```

Revisión humana obligatoria antes de concluir conformidad.

------------------------------------------------------------------------

# FASE 14 --- Validación

  Prueba                   Resultado
  ------------------------ ----------------------------
  Revocar API test         `ERROR`, nunca `PASS`
  Desconectar agente       Detectar cobertura perdida
  Quitar protección test   Detectar desviación
  Backup fallido           Hallazgo
  Restore                  Evidencia
  Alterar evidencia        Hash falla
  LLM caído                Checks siguen
  Storage caído            Collection no OK
  Reejecución              Idempotencia

Probar también recuperación de `ens-evidence`.

------------------------------------------------------------------------

# Prompt maestro de Claude Code

``` text
Vamos a implementar un proyecto llamado "ens".

OBJETIVO

Construir un repositorio privado que contenga la documentación
y la automatización de evidencias para la autoevaluación del
Esquema Nacional de Seguridad (ENS) de un sistema cuya categoría
todavía debe validarse.

CONTEXTO

Empresa unipersonal de desarrollo de software en España.

Tecnologías:
- macOS y Linux
- GitHub
- Cloudflare
- Google Workspace
- Bitwarden
- Fleet self-hosted
- Wazuh self-hosted
- Restic
- S3 compatible

REQUISITOS

1. Python 3.12 o superior.
2. Código tipado y modular.
3. Pydantic para modelos.
4. Typer para CLI.
5. HTTPX para API.
6. Pytest para pruebas.
7. Ruff para calidad.
8. YAML para configuración.
9. S3 para evidencias sensibles.
10. Git para documentación, código e índices.

REGLAS DE SEGURIDAD

- Nunca almacenar secretos en el repositorio.
- Nunca inventar evidencias.
- Nunca declarar conformidad automáticamente.
- Nunca realizar cambios en producción sin aprobación.
- Recolectores de solo lectura.
- Manejar explícitamente errores y permisos insuficientes.
- No convertir ausencia de datos en cumplimiento.
- No enviar datos sensibles a modelos de IA sin autorización.
- No asumir disponibilidad de funciones premium.
- No inventar requisitos ENS.

REQUISITOS NORMATIVOS

Usar RD 311/2022 y fuentes oficiales del CCN.
Conservar referencias exactas a las medidas.
Separar aplicabilidad, comprobación técnica y evaluación humana.

PRIMERA ITERACIÓN

1. Proponer arquitectura.
2. Crear estructura del repositorio.
3. Crear pyproject.toml.
4. Crear CLAUDE.md.
5. Crear modelos de datos.
6. Crear CLI básica.
7. Crear tests.
8. Crear README.
9. Crear documentación de decisiones.
10. Crear un plan de implementación por fases.

NO IMPLEMENTAR TODAVÍA

- Integraciones reales con API.
- Despliegues de infraestructura.
- Cambios en Cloudflare.
- Cambios en GitHub.
- Cambios en servidores.
- Automatizaciones con credenciales.

Antes de escribir archivos, muestra el plan y espera aprobación.

Al finalizar:
- Ejecuta tests y lint.
- Muestra los archivos creados.
- Documenta las decisiones.
- Identifica riesgos y pendientes.
- Propón el siguiente incremento.
```

------------------------------------------------------------------------

# Orden recomendado

  Semana   Trabajo                        Resultado
  -------- ------------------------------ --------------------
  1        Repo + inventario              Activos
  2        Alcance + riesgos + matriz     Aplicabilidad
  3        GitHub + Cloudflare            Evidencias
  4        Fleet + Workspace              Inventario/accesos
  5        Wazuh                          Detección
  6        Restic + S3                    Recuperación
  7        Checks + reports + scheduler   Automatización
  8        Incidentes + IA                Operación
  9--10    Correcciones                   Expediente

------------------------------------------------------------------------

# No implementar inicialmente

-   Kubernetes.
-   SOAR completo.
-   DB adicional.
-   Dashboard propio.
-   GRC comercial.
-   Migración masiva Cloudflare → Terraform.
-   Respuesta destructiva automática.
-   Dependencia crítica de un LLM.

Primero:

``` text
evidencias reales
        +
checks reproducibles
        +
informes
        +
revisión humana
```

------------------------------------------------------------------------

# Estado final esperado

``` bash
ens-evidence collect --all
ens-evidence check --all
ens-evidence report monthly
ens-evidence assessment status
```

Ejemplo:

``` text
ENS EVIDENCE — SYSTEM STATUS

Assets:                12
Applicable measures:   58
Measures assessed:     41
Pending review:        12
Non-compliant:          3
Not assessed:           2

Evidence collection:   OK
Evidence integrity:    OK
Last backup test:      OK

ASSESSMENT STATUS:
NOT READY

Blocking findings:
- Missing evidence for access review
- One server without Wazuh coverage
- Incident procedure not tested
```

Los números son ilustrativos.

El sistema debe ser capaz de decir `NOT READY`.

------------------------------------------------------------------------

# Política inicial de evidencias

No existe un número fijo de evidencias que deba acumularse antes de la
primera autoevaluación. La evidencia debe demostrar que las medidas
aplicables están implantadas y operativas.

  Evidencia          Recogida         Retención inicial propuesta
  ------------------ ---------------- -----------------------------
  Inventario         Diaria           24 meses de consolidados
  Fleet              Diaria           24 meses de consolidados
  Wazuh              Diaria           24 meses de consolidados
  Cloudflare         Diaria/cambios   24 meses
  GitHub             Diaria/cambios   24 meses
  Workspace          Diaria/cambios   24 meses
  Backups            Cada ejecución   Política específica
  Restauraciones     Periódica        24 meses inicialmente
  Incidentes         Por incidente    Política/obligaciones
  Autoevaluaciones   Por evaluación   Conservar expediente

Son recomendaciones iniciales, no mínimos legales generales.

------------------------------------------------------------------------

# Principios

1.  Privilegio mínimo.
2.  Collectors read-only.
3.  Secretos fuera de Git.
4.  Evidencias cifradas.
5.  Copia independiente.
6.  Trazabilidad.
7.  Hashes.
8.  Restore tests.
9.  Separar detección de IA.
10. Revisión humana.
11. Ausencia de evidencia != cumplimiento.
12. Validar capacidades reales de cada producto/plan.

------------------------------------------------------------------------

# Fuentes

-   BOE --- RD 311/2022: https://www.boe.es/eli/es/rd/2022/05/03/311/con
-   CCN-CERT --- Guías ENS serie 800:
    https://www.ccn-cert.cni.es/es/guias/guias-series-ccn-stic/800-guia-esquema-nacional-de-seguridad.html
-   GitHub Docs: https://docs.github.com/
-   Cloudflare Developers: https://developers.cloudflare.com/
-   Fleet Docs: https://fleetdm.com/docs
-   Wazuh Docs: https://documentation.wazuh.com/
-   Restic Docs: https://restic.readthedocs.io/
-   Claude Code Docs: https://code.claude.com/docs/

Comprobar siempre la documentación vigente y las fuentes oficiales antes
de implantar configuraciones concretas.

------------------------------------------------------------------------

# Primer paso

1.  Crear repo privado `ens`.
2.  Inicializar Claude Code.
3.  Añadir `CLAUDE.md`.
4.  Ejecutar el prompt maestro.
5.  Completar inventario.
6.  Delimitar alcance.
7.  Categorizar.
8.  Construir matriz oficial.
9.  Revisar manualmente.
10. Empezar collectors/checks.

Este orden hace que cada automatización tenga una justificación concreta
dentro de la autoevaluación ENS.
