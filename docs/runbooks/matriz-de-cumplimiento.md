# Matriz de cumplimiento

Cómo documentar, medida a medida, si cada medida de la Declaración está
implantada, si está sujeta a revisión regular y qué evidencias lo sustentan.
Lo exige el anexo III, 2.1.a del RD 311/2022.
Contrato: [ADR 0011](../decisions/0011-matriz-de-cumplimiento.md).

Requisito previo: la Declaración de Aplicabilidad firmada y vigente
(`ens-evidence applicability status`; ver
[categorización, riesgos y aplicabilidad](categorizacion-riesgos-y-aplicabilidad.md)).
Si la Declaración deja de estar vigente, todo queda bloqueado hasta que se
vuelva a firmar.

## 1. Crea la matriz

```bash
ens-evidence matrix init
```

Crea una entrada por cada medida de la Declaración, incluidas las no
aplicables. Si la vuelves a ejecutar, conserva lo que ya hay.

La matriz vive en `controls/matrix.yaml` y las revisiones en
`controls/reviews/`. Usa siempre estos comandos contra el workspace:
`workspace push` no publica la matriz.

## 2. Responsable y periodicidad

```bash
ens-evidence matrix set op.acc.5 --owner "Hugo Serrano Ortiz" --owner-role system_administrator --every 180
```

Cada medida necesita un responsable y una periodicidad de revisión, de 1 a 365
días. Sin ellos no está «sujeta a revisión regular» y aparece como bloqueo.

## 3. Vincula las evidencias

```bash
ens-evidence evidence import --source manual ...       # acta, captura, contrato firmado
ens-evidence matrix link org.1 --document politica-seguridad --covers base
ens-evidence matrix link op.acc.5 --evidence EV-… --covers base,R1
ens-evidence matrix link op.exp.1 --check <id-del-check> --covers op.exp.1.1
```

`--covers` dice qué cubre la fuente. Puede ser `base`, un refuerzo (`R1`), la
compensación (`compensation`) o ids de cláusula concretos. Una obligación solo
queda sustentada si **todas** sus cláusulas lo están, sumando todas las
fuentes vinculadas.

Qué fuentes cuentan:

- **Evidencia manual:** solo la importada con `--source manual` y con el
  fichero íntegro. Si no indica hasta cuándo vale, solo cuenta si se recogió
  dentro de la periodicidad de la medida.
- **Documento organizativo:** solo si está aprobado y vigente
  (`ens-evidence docs status`), y solo para las cláusulas que cubre.
- **Check:** cuenta la última ejecución completa, con todos los activos en
  PASS y evidencia de menos de 30 días. Un FAIL bloquea la medida aunque esté
  revisada. Hasta que exista el motor de checks no cuenta ningún check;
  `matrix status` lo indica.

Que un check esté en verde o que un documento esté aprobado no da la medida
por implantada: eso solo lo hace una revisión.

## 4. Revisa cada medida

```bash
ens-evidence matrix review op.acc.5 --by "Hugo Serrano Ortiz" --role system_administrator \
  --assessment COMPLIANT --conclusion "Revisadas las cuentas con acceso remoto y su MFA."
```

Revisa el administrador del sistema o su delegado (anexo III, 2.1.a). El
responsable de la seguridad analiza después el informe (2.1.b); no revisa
medidas.

- Antes de registrar, se muestra qué cubre cada fuente.
- `COMPLIANT` se rechaza si falta el responsable, la periodicidad o alguna
  cláusula por cubrir.
- En las medidas no aplicables no se pone `--assessment`: se revisa que la
  decisión sigue valiendo.
- El nombre es declarado; la herramienta no lo autentica.

La revisión deja de valer en cualquiera de estos casos:

- vence la periodicidad;
- cambian las fuentes vinculadas o su contenido;
- cambia lo que la Declaración dice de esa medida;
- cambia su ficha en el catálogo.

Cambiar otra medida no la afecta.

## 5. Consulta y exporta

```bash
ens-evidence matrix status                 # todas; sale con 1 si hay bloqueos
ens-evidence matrix status op.acc.5        # una
ens-evidence matrix export --format md     # evidence/reports/matrix/<fecha>.md
ens-evidence applicability show --annotated --save
```

La exportación lista cada medida de la Declaración con:

- sus requisitos;
- sus evidencias, con las fuentes, sus hashes y sus fechas;
- el responsable, la periodicidad y la última revisión;
- sus bloqueos.

Si falta alguna medida de la Declaración, la exportación falla.

`--at` evalúa en un instante pasado. Los documentos organizativos se leen en
su estado actual: la herramienta no reconstruye su vigencia en el pasado.

Nada de esto declara conformidad: la valoración es de la persona que revisa,
y el expediente la recoge para su análisis.
