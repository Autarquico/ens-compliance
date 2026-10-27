# Cuestionario de descubrimiento

Responde por elemento. Con las respuestas se rellenan los ficheros de
`scope/` (`assets.yaml`, `services.yaml`, `dependencies.yaml`, `system.yaml`).

**No escribas aquí ni en ningún fichero del workspace contraseñas, tokens,
claves privadas ni URLs con credenciales.** Basta con indicar dónde se
guardan (por ejemplo, "en el gestor de contraseñas"). Si algo parece un
secreto, la validación lo rechaza.

Si un elemento todavía no está desplegado, inclúyelo con
`deployment: planned`. No se asume que exista.

## 1. Portátiles (`type: laptop`)

Por cada equipo:

- ¿Qué identificador interno le das (`id`)?
- ¿Qué sistema operativo usa (`os`)?
- ¿Quién es el propietario (`owner`)?
- ¿Tiene el disco cifrado (`encrypted`: sí/no)?
- ¿Para qué se usa (`usage`)?
- Opcional: nombre de host (`hostname`), útil para reconciliar con herramientas de inventario.

## 2. Servidores (`type: server`)

- Nombre de host (`hostname`).
- Función (`function`).
- Sistema operativo (`os`).
- Entorno (`environment`: producción, pruebas...).
- Ubicación (`location`: oficina, centro de datos, proveedor).

## 3. Aplicaciones (`type: application`)

- Servicio al que pertenece (`service`).
- Dominio público (`domain`).
- Repositorio de código (`repo`).
- Base de datos que usa (`database`).

## 4. Bases de datos (`type: database`)

- Motor (`engine`).
- Ubicación (`location`).
- Qué datos contiene (`data`), sin copiar su contenido.
- Cómo se hace la copia de seguridad (`backup`).

## 5. GitHub (`type: code_hosting`)

- Cuenta u organización (`account`).
- Repositorios en el alcance (`repos`).
- Uso de Actions (`actions`).

## 6. Cloudflare (`type: edge_network`)

- Cuenta (`account`).
- Zonas (`zones`).
- Uso de Tunnel (`tunnel`), WAF (`waf`) y Access (`access`).

## 7. Workspace de identidad y correo (`type: identity`)

- Usuarios (`users`) y administradores (`admins`).
- ¿MFA activado para todos (`mfa`: sí/no)?

## 8. Gestor de contraseñas (`type: password_manager`)

- Modalidad (`mode`: alojado, autoalojado...).
- Mecanismo de recuperación (`recovery`), sin indicar claves de recuperación.
- ¿MFA activado (`mfa`: sí/no)?

## 9. Almacenamiento (`type: storage`)

- Buckets (`buckets`).
- Ubicación (`location`).
- Cifrado (`encryption`).
- Quién tiene acceso (`access`).

## 10. Copias de seguridad (`type: backup`)

- Origen (`source`) y destino (`destination`).
- Frecuencia (`frequency`).
- Última prueba de restauración o cómo se prueba (`restoration`).

## 11. Proveedores (`type: provider`)

- Servicio que prestan (`service`).
- Función para tu sistema (`function`).
- De qué dependes exactamente (`dependency`).

## Servicios, dependencias y alcance

- **Servicios** (`services.yaml`): qué servicios prestas y qué activos los soportan.
- **Dependencias** (`dependencies.yaml`): qué activo depende de qué otro activo o proveedor.
- **Alcance** (`system.yaml`): nombre y descripción del sistema que se pretende
  declarar conforme al ENS, qué activos y servicios incluye y cuáles excluye
  con su justificación. Todo activo y servicio debe estar incluido o excluido.

## Decisiones que requieren a una persona

- Aprobar el alcance: lo hace el responsable del sistema
  (`ens-evidence scope approve --by <nombre> --role system_owner`).
  El nombre es declarado, no se autentica.
- La categoría ENS depende del impacto en las dimensiones de seguridad, no del
  tamaño de la empresa; se decide en una fase posterior.
- Cualquier cambio posterior en `scope/` invalida la aprobación y hay que aprobar de nuevo.
