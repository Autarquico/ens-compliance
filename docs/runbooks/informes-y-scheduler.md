# Informes y scheduler

Cómo programar el ciclo diario (recogida → checks → informe → copia) y leer los
informes. Contrato: [ADR 0014](../decisions/0014-informes-y-scheduler.md).

## 1. Configura

```bash
ens-evidence scheduler init-config      # crea reports/schedule.yaml con los valores por defecto
```

Valores por defecto:

- ciclo diario a las 06:00, hora de Europe/Madrid;
- informe mensual el día 1;
- copia a R2 al final de cada ciclo diario;
- avisos de caducidad: 30 días para revisiones, aprobaciones, pruebas de
  restauración y copia externa; 14 días para evidencia manual; 7 días para
  evidencia técnica. Se cambian en `expiry_warnings` de `reports/schedule.yaml`
  y rigen los informes diario y mensual; las acciones del plan de tratamiento
  avisan siempre al vencer.

El fichero no admite credenciales. Las de los conectores y las de la copia van
en variables de entorno del contenedor.

## 2. Arranca el scheduler

En la caja, el contenedor de la app ejecuta:

```bash
ens-evidence scheduler run
```

- `ENS_STATE_DIR` debe apuntar a un directorio persistente, por ejemplo
  `/data/state`. Ahí vive el lock que impide solapar dos ciclos.
- En Compose: `init: true` y `stop_grace_period: 120s`. Con SIGTERM, el
  scheduler termina el conector en curso y sale.
- Si la configuración no es válida al arrancar, sale con código 2. Si se
  estropea con el scheduler en marcha, mantiene la última válida y lo avisa
  en el siguiente informe.

```bash
ens-evidence scheduler status   # próximos disparos, últimos informes, ciclos interrumpidos, días sin informe
```

## 3. Ejecuta a mano

```bash
ens-evidence report daily --cycle        # el ciclo completo ahora (toma el lock)
ens-evidence report daily                # solo el informe del estado actual, sin red
ens-evidence report monthly --stdout --format html
ens-evidence collect --all
```

Los informes sin `--cycle` son consultas: no cierran el trabajo del scheduler, no
cuentan como día cubierto en el mensual y no sirven de base para «Cambios», aunque
`scheduler status` los liste como último informe. En el mensual, el «periodo del ciclo»
es el mes en que se disparó, no el mes del informe.

Códigos de salida:

| Código | Significado |
|---|---|
| 0 | Informe escrito |
| 3 | Informe escrito, pero algún conector falló o no tiene ejecución |
| 2 | No se escribió, o hay un ciclo en curso |

## 4. Lee los informes

Los informes están en `evidence/reports/daily/<fecha>/` y
`evidence/reports/monthly/<mes>/`, en JSON y HTML. Entran en la copia.

- Cada dato cita su fuente y su fecha. «No disponible» significa que no hay
  de dónde sacarlo; nunca es un cero.
- Un conector que falla aparece en ERROR. Si falla el orquestador (excepción,
  timeout o carga), el HTML añade la fase: «ERROR · fase: recogida (excepción
  RuntimeError)». Si el propio conector informa ERROR, no hay fase: el detalle
  está en la recogida citada como fuente. Nunca se usa una recogida anterior
  en su lugar. Del error solo se guarda el tipo de
  excepción, nunca su mensaje.
- El HTML está en castellano llano («no preparada», «copia fallida: sin
  ajustes de copia», «revisión de la medida»). Los códigos internos
  (`NOT_READY`, `backup_failed`, `measure_review`) quedan en el JSON. Los
  estados oficiales PASS, FAIL, ERROR y UNKNOWN se muestran tal cual.
- En el HTML las fuentes van una vez por sección, al pie de su tabla.
- En el mensual, un recuento de 0 solo aparece si el registro se pudo
  listar, y cita lo consultado. Si en el mes no hay ninguna prueba de
  restauración, sale en Pendientes: no es un dato que falte, es trabajo por
  hacer. Un conector configurado sin ejecución al cierre del mes se lista
  como «checks no disponibles»; nunca se omite.
- Si un collector devuelve algo que parece un secreto, el resultado no se
  guarda y queda un ERROR que indica dónde apareció.
- El informe no declara conformidad. La preparación es la de
  `assessment status`, con su aviso de revisión humana pendiente.

## 5. Recupera

- **Ciclo interrumpido** (caída, reinicio): el siguiente arranque lo
  reanuda en las etapas pendientes, y los informes lo listan. Los días
  perdidos no se rellenan.
- **Copia fallida (ERROR):** revisa las credenciales de R2 en el entorno y lanza
  `ens-evidence backup run`.
- **Reloj desajustado:** ver el [runbook del motor de checks](motor-de-checks.md).
- **Borrar informes:** la herramienta no los borra nunca. Si una política
  interna lo exige, hazlo a mano sobre `evidence/reports/` y documenta el
  borrado. Recuerda que la copia inmutable los conserva.

## Despliegue y rollback

- **Desplegar** es arrancar el contenedor con `scheduler run`.
- **Volver atrás** es arrancar la imagen anterior. Los informes y los
  marcadores ya escritos se conservan, y la versión anterior sigue leyéndolos.
- **Instalar, actualizar y volver atrás la caja:** ver el [runbook de la caja](caja.md).
