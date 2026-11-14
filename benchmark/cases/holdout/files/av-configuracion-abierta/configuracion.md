# Configuración de seguridad de los componentes del sistema

Fecha: 2 de octubre de 2026. Autor: Gonzalo Iglesias Pardo. Aprobada por Noelia Salvatierra Mena.

## op.exp.2 Configuración previa a la entrada en operación

### op.exp.2.1 Cuentas y contraseñas estándar

Antes de poner en servicio el servidor de archivos, los portátiles o el router, se eliminan las cuentas y contraseñas de fábrica; las credenciales nuevas se guardan en el gestor de contraseñas.

### op.exp.2.2 Mínima funcionalidad

a) El servidor ofrece solo compartición de ficheros dentro de la red de la oficina y copia a la nube, que es la funcionalidad imprescindible para los objetivos de la agencia; los portátiles llevan solo navegador, ofimática, antivirus y gestor de contraseñas.
b) Se desactivan en el servidor los servicios de acceso remoto, de publicación web y de multimedia, y en los portátiles el uso compartido y el escritorio remoto.

### op.exp.2.3 Seguridad por defecto

a) El cifrado de disco, el cortafuegos del sistema y el bloqueo de pantalla vienen activados y protegen a la persona usuaria salvo que decida exponerse.
b) Para reducir la seguridad hace falta una cuenta de administración, de modo que bajar la protección exige una acción consciente.
c) El uso natural, sin leer el manual, es seguro: el portátil se bloquea solo y las descargas se analizan por defecto.

### op.exp.2.4 Máquinas virtuales

La agencia no usa máquinas virtuales. El programa de reservas lo ejecuta el proveedor en su infraestructura, donde gestiona como máquinas físicas el parcheado, las cuentas y el antivirus, incluida la máquina anfitriona.

## op.exp.3 Gestión continua de la configuración

### op.exp.3.1 Funcionalidad mínima

Cada seis meses el administrador del sistema revisa que solo siga instalado el software de la lista autorizada y retira el resto.

### op.exp.3.2 Mínimo privilegio

En la misma revisión comprueba que nadie tenga permisos de administración o de carpetas que no necesite.

### op.exp.3.3 Nuevas necesidades autorizadas

Un cambio de configuración solo se hace si la gerencia o la responsable de la seguridad lo han autorizado antes por correo.

### op.exp.3.4 Reacción a vulnerabilidades

Cuando un fabricante o el proveedor anuncian una vulnerabilidad, el administrador del sistema ajusta la configuración o aplica la actualización siguiendo el procedimiento de mantenimiento.

### op.exp.3.5 Reacción a incidentes

Si un incidente exige un cambio de configuración, se hace dentro del procedimiento de incidentes y se anota en el registro del incidente.

### op.exp.3.6 Edición solo por personal autorizado

Cualquier persona del personal puede editar la configuración de seguridad de los portátiles y del servidor cuando lo necesite.
