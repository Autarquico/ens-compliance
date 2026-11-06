# Configuración de seguridad de los componentes del sistema

Fecha: 28 de septiembre de 2026. Autora: Lucía Barrios Soto. Aprobada por Íñigo Paredes Lasa.

## op.exp.2 Configuración previa a la entrada en operación

### op.exp.2.1 Cuentas y contraseñas estándar

Antes de usar un portátil, el router o la aplicación, se retiran las cuentas y contraseñas de fábrica y se sustituyen por credenciales propias guardadas en el gestor de contraseñas.

### op.exp.2.2 Mínima funcionalidad

a) Cada portátil lleva instalado solo el navegador, el paquete ofimático, el antivirus y el cliente del gestor de contraseñas, que es la funcionalidad imprescindible para los objetivos de la academia.
b) Se desactivan en los portátiles el uso compartido de ficheros, el escritorio remoto y el servidor web local, y en el router el acceso de administración desde internet y los servicios sin uso.

### op.exp.2.3 Seguridad por defecto

a) El cifrado del disco, el cortafuegos del sistema operativo y el bloqueo de pantalla vienen activados y protegen a la persona usuaria salvo que decida exponerse.
b) Para desactivar cualquiera de ellos hace falta una cuenta administradora, de modo que reducir la seguridad exige una acción consciente.
c) Un uso natural, sin consultar el manual, es seguro: el equipo se bloquea solo y las descargas se analizan por defecto.

### op.exp.2.4 Máquinas virtuales

La academia no usa máquinas virtuales propias. La aplicación se ejecuta en un contenedor gestionado por el proveedor de alojamiento, que aplica a su máquina anfitriona el mismo parcheado, control de cuentas y antivirus que a una máquina física.

## op.exp.3 Gestión continua de la configuración

### op.exp.3.1 Funcionalidad mínima

Cada seis meses la responsable del sistema revisa que sigue instalado solo el software de la lista autorizada y retira lo demás.

### op.exp.3.2 Mínimo privilegio

En la misma revisión se comprueba que nadie tenga permisos de administración innecesarios en los portátiles ni en la aplicación.

### op.exp.3.3 Nuevas necesidades autorizadas

Un cambio de configuración solo se aplica si la gerencia o el responsable de la seguridad lo ha autorizado antes por correo, según el proceso de gestión de derechos de acceso.

### op.exp.3.4 Reacción a vulnerabilidades

Cuando el fabricante o el proveedor anuncian una vulnerabilidad, la responsable del sistema ajusta la configuración o aplica la actualización según el procedimiento de mantenimiento y actualizaciones.

### op.exp.3.5 Reacción a incidentes

Si un incidente exige cambiar la configuración, se hace dentro del procedimiento de incidentes y queda anotado en el registro del incidente.

### op.exp.3.6 Edición solo por personal autorizado

Solo la responsable del sistema, con su cuenta administradora, y el soporte del proveedor de alojamiento, bajo su solicitud, pueden editar la configuración de seguridad.
