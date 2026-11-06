# Documentación organizativa

Cómo generar y aprobar la política, la designación de responsables, la
normativa, los procedimientos y el proceso de autorización. Contrato:
[ADR 0010](../decisions/0010-documentacion-organizativa.md). Fuente legal:
RD 311/2022, anexo II (org.1–org.4) y arts. 2.3, 11, 12, 13 y 15 (texto
consolidado del BOE).

Requisito previo: el alcance aprobado (`ens-evidence scope status`).

## 1. Responde las preguntas

```bash
ens-evidence docs questions     # qué se pregunta y por qué cláusula
ens-evidence docs init          # crea org/answers.yaml con todo vacío
```

Rellena `org/answers.yaml`. Cada pregunta dice qué cláusula del RD la
motiva. Responde con lo que **de verdad** hacéis: quién, cómo, con qué
herramienta y qué queda registrado. La herramienta no redacta por ti: si una
respuesta obligatoria está vacía o es de relleno ("n/a", "por definir"), no
genera nada y te dice qué falta.

Algunas preguntas solo aparecen si hacen falta:

- Si el **responsable de la seguridad** y el **responsable del sistema** son la
  misma persona o hay dependencia jerárquica entre ellos, el art. 13.3 lo
  permite solo de forma excepcional y por ausencia justificada de recursos:
  explica la causa concreta y qué medidas compensatorias aplicas. Sin eso no
  se aprueba la designación.
- **Quién firma por la dirección** (`firmante_direccion`): aunque la dirección
  sea un órgano (p. ej. un consejo de administración), declara una persona
  concreta, con el nombre tal y como figura en `personas` (añádela ahí aunque
  no tenga cargo de seguridad). Solo esa persona puede aprobar en nombre de la
  dirección.
- Quién aprueba los procedimientos y el proceso de autorización
  (`autoridad_procedimientos`): `la dirección`, `el responsable de la
  seguridad` o `el responsable del sistema`.
- Si prestáis servicios a otras entidades (lo habitual si estáis en el ENS por
  ser proveedor de una administración), el art. 13.5 pide un punto de contacto
  de seguridad por servicio, salvo causa justificada y documentada.

Los **procedimientos de seguridad** preguntan también cómo hacéis las tareas
operativas de las medidas de BÁSICA: análisis de riesgos (op.pl.1),
inventario (op.exp.1), cuentas y credenciales (op.acc.1, op.acc.6),
actualizaciones (op.exp.4), código dañino (op.exp.6), registro de actividad
(op.exp.8), perímetro (mp.com.1) y puesta en producción (mp.sw.2); el control
de acceso y las copias citan además op.acc.2, op.acc.4 y mp.info.6. Y cómo
se trata al personal: información de sus deberes (`informacion_deberes`,
mp.per.2), concienciación (`concienciacion`, mp.per.3) y formación con la
evaluación de su eficacia (`formacion`, mp.per.4). Describir un procedimiento
no lo demuestra: la medida sigue necesitando su evidencia.

## 2. Genera los documentos

```bash
ens-evidence docs build
```

Escribe en `org/docs/` los seis documentos en Markdown, cada uno con una
tabla que dice qué cláusula del RD cubre cada sección y con qué versión de
plantilla y de catálogo se generó. Si vuelves a generar con las mismas
respuestas, el resultado es idéntico. Las versiones anteriores quedan en
`org/docs/history/`.

No edites los documentos a mano: cambia las respuestas y vuelve a generar. Si
alguien edita un documento, `docs status` lo detecta.

## 3. Apruébalos

```bash
ens-evidence docs approve politica-seguridad --by "Nombre Apellido" --role management
ens-evidence docs status
```

`--by` debe ser el nombre completo de la persona declarada para ese rol
(`firmante_direccion`, `resp_seguridad` o `resp_sistema`, sin importar tildes
ni mayúsculas); un fragmento del nombre no vale. `--role` sigue siendo
`management`, `security_officer` o `system_owner`.

| Documento | Lo aprueba |
|-----------|------------|
| Política de seguridad | La dirección (`management`): el órgano con las máximas competencias ejecutivas (art. 2.3) |
| Normativa de seguridad | La dirección (art. 15.2) |
| Designación de responsables | La dirección (decisión del producto) |
| Procedimientos y proceso de autorización | Quien la organización declare en sus respuestas (decisión del producto) |

La aprobación deja de valer si cambian el documento, las respuestas, la
plantilla, el catálogo, el texto legal empaquetado o el alcance: vuelve a
generar y a aprobar. `docs status` dice por qué.

### Al actualizar desde plantillas v1

Las plantillas pasan a v2 (secciones operativas nuevas en los procedimientos).
En un workspace generado con v1, `docs status` marca los seis documentos como
obsoletos («plantilla cambiada de versión») y `docs build` no genera nada
hasta que respondas las preguntas nuevas (`docs questions` las lista). Tras
responderlas: `docs build` (las versiones v1 quedan en `org/docs/history/`) y
vuelve a aprobar los seis documentos. Mientras tanto, los enlaces de la matriz
a esos documentos no sustentan nada y el expediente no los cita como
aprobados.

Aprobar un documento no da por cumplida ninguna medida: dice que el documento
existe y está aprobado. Que se aplica de verdad se demuestra con evidencias en
el expediente.
