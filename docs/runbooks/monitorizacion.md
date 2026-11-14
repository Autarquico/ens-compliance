# Panel de monitorización

Qué significa cada elemento de `/monitorizacion`, qué hacer cuando está en rojo
o en gris y cómo activar los avisos por correo (spec
[kit-monitoring](../specs/kit-monitoring.md),
[ADR 0019](../decisions/0019-caja-y-herramientas-en-un-vps.md)).

El panel es operación, no cumplimiento: un verde no marca ninguna medida ENS ni
un rojo la cambia. La matriz de cumplimiento no depende de él, y la valoración
de las medidas sigue siendo humana.

## Cómo se lee

- Verde: la señal es reciente y está dentro del umbral.
- Ámbar: hay un indicio que conviene revisar (p. ej. un contenedor en marcha
  sin comprobación de salud).
- Rojo: la señal es reciente y está fuera del umbral, o la herramienta no
  responde. Se muestra desde cuándo y el enlace a este runbook.
- Gris («desconocido»): no hay dato, es inválido o ha vencido. Nunca se
  muestra como verde.
- Si el panel entero aparece como desactualizado, el bucle de monitorización
  de la caja no ha corrido en más de 2 intervalos: mira `scheduler` en el
  runbook de la caja (sección 9).

Las muestras se toman cada 15 minutos y se guardan 30 días en
`$ENS_STATE_DIR/monitoring`. No son evidencia y no entran en la copia.

## Umbrales

Los valores por defecto se pueden cambiar en `monitoring/thresholds.yaml`
(sin el fichero, rigen los de la tabla):

| Elemento | Umbral por defecto |
|---|---|
| Disco | 85 % usado |
| Memoria | 90 % usada |
| Carga por núcleo | 2.0 |
| Certificado TLS | caduca en menos de 21 días |
| Agente sin reportar | más de 3 h |
| Copia externa | última copia de hace más de 26 h |

## Por herramienta

Tres columnas separadas: disponibilidad, dato clave y seguridad.

- Disponibilidad. OpenBao: estado de sellado por HTTP. Backrest y
  Vaultwarden: su contenedor está en marcha y, si tiene comprobación de salud,
  sana. Sin comprobación de salud sale ámbar «en marcha (sin comprobación de
  salud)».
- Dato clave: el de su tarjeta en `/herramientas` (p. ej. última copia, bóveda
  sellada o no). Caduca a las 26 h si es un snapshot.
- Seguridad: una consulta anónima diaria a los avisos y releases públicos de
  GitHub de cada herramienta. Si la versión instalada cae en un aviso, rojo;
  si no se puede decidir, «revisar»; sin red o si falla, «desconocido». Que la
  versión coincida con la fijada en el paquete no implica que no tenga
  vulnerabilidades.

Qué hacer:

- Rojo en disponibilidad: abre el runbook de la herramienta (`kit-copias`,
  `kit-claves`, `kit-contrasenas`) y mira `docker compose -p ens-box logs
  <servicio>`. OpenBao sellado: sigue `kit-claves`.
- Rojo en seguridad: lee el aviso, planifica la actualización de la versión
  fijada (`deploy/box.sh update`) y apúntalo en el registro de incidentes si
  procede.
- Gris: comprueba que la caja tiene salida a GitHub (solo seguridad) o que el
  agente del VPS reporta (resto).

## Del VPS

Los datos vienen del agente osquery del propio VPS (`agentes-osquery.md`,
«Inscribir el propio VPS de la caja»). Sin ese agente, todo lo de esta sección
sale gris.

- Carga por núcleo: carga a 1, 5 y 15 minutos dividida por los núcleos. No es
  uso de CPU. Rojo sostenido: busca el proceso responsable con `top`, o
  amplía el VPS.
- Memoria: porcentaje usado. Rojo: revisa contenedores que crecen
  (`docker stats`) y reinicia el que sea.
- Disco: el montaje de `/` y el de la raíz de Docker. Rojo: `docker system df`
  y limpia imágenes sin uso con la caja sana; nunca borres volúmenes sin
  copia comprobada.
- Certificados TLS: el que ve un cliente en el borde (Cloudflare) de los
  hostnames configurados. Rojo: revisa el hostname y la configuración del
  túnel en Cloudflare.
- Actualizaciones del sistema: solo indicios (reinicio pendiente, última
  ejecución de apt, `unattended-upgrades`). El número pendiente es siempre
  «desconocido» y nunca verde. Ante un indicio: `sudo apt update && apt list
  --upgradable`, aplica y reinicia en ventana acordada.
- Contenedores: estado y salud de los del proyecto de Compose. Rojo: reinicia
  con `deploy/box.sh restart <servicio>` y revisa los registros.

## De la caja

- Últimas ejecuciones del scheduler: rojo si el último ciclo falló o no hay
  latido reciente; ver `caja.md` sección 9 y `informes-y-scheduler.md`.
- Agentes que han dejado de reportar: gris o rojo pasadas 3 h. Comprueba que
  el equipo está encendido y que `osqueryd` corre
  (`agentes-osquery.md`, sección 2, paso 4); si el equipo ya no existe,
  revócalo (sección 4).
- Copia externa al día: rojo pasadas 26 h sin copia correcta; sigue
  `kit-copias`.

## Avisos

Cada herramienta o elemento que pasa a rojo genera un aviso en la web (banner)
y, si hay SMTP, un correo; después, un recordatorio cada 24 h mientras siga
en rojo. Sin SMTP, o si el envío falla, los avisos son solo web.

### Configurar el correo

En `deploy/.env` (modo 600, nunca en Git), solo lo recibe la app, no la
interfaz web:

```bash
ENS_SMTP_HOST=smtp.example.org
ENS_SMTP_PORT=587
ENS_SMTP_USER=avisos@example.org
ENS_SMTP_PASSWORD=...
ENS_SMTP_FROM=avisos@example.org
ENS_SMTP_TO=responsable@example.org
```

Se exige STARTTLS. Reinicia la app (`deploy/box.sh restart app`) y comprueba
con `ens-evidence monitoring status` que el correo figura como configurado. Si
rotas la contraseña, edita el fichero y reinicia.

## Qué no hace

- No marca ni cambia ninguna medida de la matriz.
- No sustituye la revisión humana ni acredita conformidad con el ENS.
- No mide aplicaciones de negocio ni detecta intrusiones.
- La app no monta el socket de Docker.
