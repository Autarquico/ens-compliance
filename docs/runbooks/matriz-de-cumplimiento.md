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

**No edites `controls/matrix.yaml` a mano.** Cada cambio (`init`, `set`, `link`,
`unlink`) deja un registro en `controls/matrix-history/<medida>/`, de solo
adición, y una revisión solo vale si desde que se registró los enlaces de la
medida no cambiaron: quitar un enlace y volver a ponerlo la caduca. Si la
entrada de una medida no coincide con el último estado registrado,
`matrix status` la bloquea con `matrix_edited:<medida>` y sale con 1.
Cualquiera de esas órdenes registra lo que hubiera y deja de bloquear, pero
las revisiones anteriores al cambio siguen caducadas. Tampoco edites a mano
`controls/reviews/`, `controls/declarations/` ni `controls/matrix-history/`:
un registro cuyo contenido no coincide con su nombre se trata como ilegible y
bloquea la medida (`review_unreadable`, `declaration_history_unreadable`,
`matrix_history_unreadable`). Para restaurar un registro ilegible, recupera el
fichero original de la copia de seguridad del workspace.

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
fuentes vinculadas. El texto de cada cláusula se lee con
`ens-evidence catalog show op.acc.5`.

Qué fuentes cuentan:

- **Evidencia manual:** solo la importada con `--source manual` y con el
  fichero íntegro. Si no indica hasta cuándo vale, solo cuenta si se recogió
  dentro de la periodicidad de la medida.
- **Documento organizativo:** solo si está aprobado y vigente
  (`ens-evidence docs status`), y solo para las cláusulas que cubre.
- **Check:** cuenta el último intento de `ens-evidence check --all`
  ([motor de checks](motor-de-checks.md)), si está completo, con todos los
  activos en PASS y evidencia de menos de 30 días. Un FAIL bloquea la medida
  aunque esté revisada.

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
- Toda revisión exige responsable y periodicidad (también la de una medida no
  aplicable). `COMPLIANT` se rechaza además si queda alguna cláusula por cubrir
  o si `--at` es anterior a una fuente que se le atribuye.
- Una revisión solo vale para el contexto en que se hizo: si una medida pasa a
  no aplicable y vuelve a aplicable, hay que revisarla de nuevo.
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

`status`, la exportación en Markdown y la vista de la Declaración muestran
cada bloqueo en castellano y con el comando del siguiente paso. Una medida que
nadie ha revisado figura como «sin revisar», no como «no conforme». Los estados
de la Declaración y de la preparación salen traducidos («vigente», «no
vigente», «no preparada»). El JSON conserva los códigos internos.

Con la Declaración no vigente, cada medida conserva el responsable, la
periodicidad y la última revisión registrados, tal como están. La medida sigue
sin implantar y sin revisión regular hasta que se vuelva a firmar la
Declaración (`applicability status` dice cómo): no hace falta repetir
`matrix set` ni `matrix review`.

Si una compensación sustituye cláusulas del anexo II, la evidencia se enlaza
con `--covers compensation`, no con las cláusulas sustituidas.

La exportación lista cada medida de la Declaración, en el orden del catálogo, con:

- sus requisitos (y, en una compensación, las medidas compensatorias);
- sus evidencias, con las fuentes, lo que cubre cada una, sus hashes y sus fechas;
- el responsable, la periodicidad y la última revisión: quién revisó, su
  conclusión, la huella del registro y la fuente que cubre cada cláusula
  según confirmó;
- sus bloqueos.

Si falta alguna medida de la Declaración, la exportación falla.

`--at` evalúa en un instante pasado. Los documentos organizativos se leen en
su estado actual: la herramienta no reconstruye su vigencia en el pasado.

Nada de esto declara conformidad: la valoración es de la persona que revisa,
y el expediente la recoge para su análisis.
