# Gestión de identidades y de derechos de acceso

Fecha: 30 de septiembre de 2026. Autor: Gonzalo Iglesias Pardo. Aprobado por Noelia Salvatierra Mena.

## op.acc.1.1 Identificador único

Cada persona se identifica con su dirección de correo de la agencia, que es su identificador único en el correo y en el programa de reservas, y con un usuario nominal en el servidor de archivos. No se usan sistemas de clave concertada de la Administración.

## op.acc.1.2 Identificadores singulares por perfil

La gerencia tiene una cuenta de uso diario y otra de administración del programa de reservas. Cada cuenta tiene sus privilegios delimitados y su actividad se registra por separado.

## op.acc.1.5 Identificación en comunicaciones electrónicas

La agencia no tramita electrónicamente con la Administración mediante este sistema. Los clientes del portal de reservas se identifican con un correo verificado, que corresponde al nivel bajo. Si se tramitara con la Administración se aplicarían los mecanismos del Reglamento (UE) 910/2014 del nivel exigido por la dimensión de autenticidad.

## op.acc.2.1 Protección de los recursos

Todos los recursos están protegidos con autenticación: los portátiles con contraseña y disco cifrado, el servidor con usuario y contraseña por persona, y el programa de reservas y el correo con contraseña y segundo factor.

## op.acc.2.3 Control del acceso a los componentes del sistema operativo

El acceso al sistema operativo del servidor y a sus ficheros de configuración está limitado a la cuenta de administración del administrador del sistema, con contraseña propia guardada en el gestor de contraseñas. Los usuarios de los portátiles no son administradores locales.

## op.acc.4.1 Todo acceso prohibido salvo autorización

Las carpetas del servidor se crean sin permisos y cada acceso lo autoriza por correo la gerencia antes de que el administrador del sistema lo conceda.

## op.acc.4.2 Mínimo privilegio

Cada persona recibe solo los permisos de su función: los agentes leen y escriben en las carpetas de sus reservas, la gerencia lee todo y solo el administrador del sistema administra.

## op.acc.4.3 Necesidad de conocer y responsabilidad de compartir

Los expedientes de un viaje solo se abren por los agentes que lo gestionan; la información necesaria para el trabajo de cada agente está en una carpeta común de consulta.

## op.acc.4.5 Política de acceso remoto

El acceso remoto requiere la autorización expresa de la responsable de la seguridad y se limita al programa de reservas y al correo por HTTPS. El servidor de archivos no admite acceso remoto.
