# Arquitectura de seguridad del sistema de Academia Cala Verde

Fecha: 20 de septiembre de 2026. Autora: Lucía Barrios Soto, responsable del sistema. Aprobada por Íñigo Paredes Lasa.

## op.pl.2.1 Documentación de las instalaciones

La academia ocupa un local de planta baja con recibidor, secretaría, despacho de dirección y tres aulas. Los puntos de acceso son la puerta principal, con llave y timbre, y una salida de emergencia que solo se abre desde dentro. Los portátiles se guardan en la secretaría y en el despacho de dirección, ambos con cerradura. No hay sala de servidores: el equipamiento propio es el router con cortafuegos del proveedor de conectividad, instalado en un armario cerrado de la secretaría.

## op.pl.2.2 Documentación del sistema

El sistema se compone de los dos portátiles (portatil-secretaria y portatil-direccion), la aplicación de matrícula (app-matricula) y su base de datos (bd-alumnado), ambas alojadas en la infraestructura del proveedor de alojamiento (proveedor-alojamiento), el correo (cuentas-correo), el gestor de contraseñas (gestor-claves) y las copias (copias-semanales). La red interna es una única red de la oficina detrás del router del proveedor de conectividad (proveedor-conectividad). Hacia el exterior solo existen dos conexiones: la salida a internet por la línea de fibra y los accesos HTTPS a la aplicación y al correo. Los puestos de trabajo son los dos portátiles y la consola de administración de la aplicación, a la que solo accede la responsable del sistema.

## op.pl.2.3 Líneas de defensa

1. Primera línea: el cortafuegos del router del proveedor de conectividad, que bloquea todo el tráfico entrante no solicitado.
2. Segunda línea: el cortafuegos del proveedor de alojamiento, que solo expone el puerto HTTPS de la aplicación; la base de datos no es accesible desde internet.
3. Tercera línea: el antivirus de cada portátil.
No existe DMZ propia. Las tecnologías de las líneas son distintas (router del proveedor de conectividad, cortafuegos del proveedor de alojamiento y antivirus del sistema operativo) para que una misma vulnerabilidad no perfore varias a la vez. El único punto de interconexión con otras redes es internet.

## op.pl.2.4 Identificación y autenticación

El personal se identifica con una cuenta personal de correo y la usa para entrar en la aplicación de matrícula; se autentica con contraseña más un segundo factor de un solo uso en una aplicación del móvil. Las contraseñas se guardan en el gestor de contraseñas. Los derechos de acceso se determinan por el perfil de la aplicación (secretaría, dirección, administración). No se usan tarjetas ni biometría. Las familias se autentican en el portal con correo y contraseña.
