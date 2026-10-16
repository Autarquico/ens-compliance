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

1. **Asistente de cumplimiento (UI web):** guía paso a paso por alcance,
   categorización, análisis de riesgos y aplicabilidad, y genera la
   documentación organizativa (política de seguridad, roles, procedimientos)
   desde plantillas y respuestas.
2. **Motor de evidencias:** collectors de solo lectura como plugins, checks
   deterministas, manifiestos con hash y storage cifrado.
3. **Agente de endpoints ligero:** osquery con un servidor TLS propio dentro de
   la caja. Wazuh queda como perfil opcional de detección.
4. **Informes y expediente:** informe diario y mensual, `assessment status`
   capaz de decir `NOT READY` y expediente de autoevaluación con las 11
   secciones de la guía.
5. **Pre-auditoría (opcional):** antes de una autoevaluación o de una
   auditoría de certificación, revisa todo el expediente en dos capas:
   - determinista (motor): evidencias ausentes o caducadas, hashes, checks en
     `FAIL`/`UNKNOWN`;
   - juicio (skill de Claude Code sobre el adaptador LLM intercambiable):
     coherencia entre política y evidencias, calidad de la documentación,
     solidez de las justificaciones de aplicabilidad.

   Devuelve hallazgos clasificados como en una auditoría (no conformidad
   mayor, no conformidad menor, observación), con referencia a medida y
   evidencia. Nunca dictamina conformidad.

**Usuario objetivo:** una pyme con alguien "apañado". Sabe seguir un asistente
y pegar un comando, pero no sabe de seguridad ni de ENS.

**Primera instalación real:** Autarqui. El producto se valida usándolo.

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
  collectors, informes) y cambian el empaquetado, la UI, los conectores y el
  osquery-edge (pasa de Worker a contenedor, con el Worker como opción).
- **Preguntas abiertas:**
  - ¿Existe un perfil de cumplimiento específico del CCN aplicable a pymes o a
    proveedores del sector público? Cambiaría el catálogo base.
  - Estado y licencia de las herramientas del CCN (PILAR/µPILAR, INES, LUCÍA)
    y viabilidad de exportar a ellas.
  - Qué conectores priorizar (Microsoft 365, Google Workspace, GitHub,
    Cloudflare…): hace falta validar qué usan de verdad las pymes objetivo.
  - Stack de la UI y si el núcleo Python también sirve el backend web.
  - Storage por defecto dentro de la caja (MinIO o similar) frente a un S3
    externo, y cómo se garantiza la copia independiente.
  - Modelo de sostenibilidad del proyecto (soporte, hosting gestionado…).
  - Qué guía CCN-STIC rige la auditoría del ENS y sus criterios de
    clasificación de hallazgos: la pre-auditoría debe seguirla, no inventarla.
  - Cómo se ofrece la capa de juicio a usuarios sin Claude Code (prompt pack
    para el adaptador LLM, servicio opcional…).
