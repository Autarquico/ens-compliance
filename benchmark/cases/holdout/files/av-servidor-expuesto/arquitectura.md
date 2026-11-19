# Arquitectura de seguridad del sistema de Brújula Austral Viajes

Fecha: 28 de septiembre de 2026. Autor: Gonzalo Iglesias Pardo, administrador del sistema. Aprobada por Noelia Salvatierra Mena.

## op.pl.2.1 Documentación de las instalaciones

La agencia ocupa un local en la primera planta de un edificio, con mostrador de atención, despacho de gerencia y un cuarto técnico. Hay una única puerta de acceso desde el rellano, con llave y vídeoportero. El cuarto técnico es un cuarto pequeño con cerradura donde están el servidor de archivos y el router del proveedor de conectividad. La caja fuerte está en el despacho de gerencia.

## op.pl.2.2 Documentación del sistema

El sistema se compone de los tres portátiles (portatil-gerencia, portatil-mostrador-1 y portatil-mostrador-2), el servidor de archivos (servidor-archivos), el programa de reservas contratado como servicio (crm-reservas, a cargo de proveedor-crm), el correo (cuentas-correo), el gestor de contraseñas (gestor-claves) y la copia nocturna (copia-nube). La red interna es una sola red de la oficina detrás del router del proveedor de conectividad (proveedor-conectividad). Las conexiones al exterior son la salida a internet, el acceso HTTPS al programa de reservas y al correo, y la subida cifrada de la copia nocturna. Los puestos de trabajo son los tres portátiles y la consola de administración del servidor, a la que solo se accede desde el portátil del mostrador 1 del administrador.

## op.pl.2.3 Líneas de defensa

El servidor de archivos está publicado en internet mediante una redirección de puertos del router, para que los agentes lo usen desde fuera, y no tiene cortafuegos propio. Las líneas de defensa son el router y el antivirus.

## op.pl.2.4 Identificación y autenticación

El personal se identifica con su cuenta personal de correo y la usa en el programa de reservas; se autentica con contraseña y una contraseña de un solo uso generada en el móvil. En el servidor de archivos cada persona tiene un usuario nominal con contraseña y los derechos de acceso a carpetas se determinan por grupos de usuarios. Las contraseñas se guardan en el gestor de contraseñas. No se usan tarjetas ni biometría. Los clientes del portal de reservas se autentican con contraseña y un código de un solo uso enviado a su correo.
