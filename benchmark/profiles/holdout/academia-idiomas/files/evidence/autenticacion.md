# Mecanismos de autenticación de usuarios externos y de la organización

Fecha: 25 de septiembre de 2026. Autora: Lucía Barrios Soto. Aprobado por Íñigo Paredes Lasa.

## Usuarios externos (familias y alumnado adulto que acceden al portal)

### op.acc.5.1 Registro fidedigno previo

Antes de recibir credenciales, la familia se registra en la secretaría con documento de identidad o por el formulario de matrícula, y la secretaría verifica sus datos con el contrato de matrícula firmado.

### op.acc.5.2 Reconocimiento de la recepción de credenciales

Antes de activar la cuenta, la persona acepta en pantalla que ha recibido las credenciales y que conoce el deber de custodia diligente, la confidencialidad y el deber de notificar de inmediato la pérdida.

### op.acc.5.3 Credenciales bajo control exclusivo del usuario

La persona elige su propia contraseña al activar la cuenta. La academia nunca conoce la contraseña y la cuenta solo se activa cuando la persona la ha fijado.

### op.acc.5.4 Cambio periódico

El portal pide cambiar la contraseña cada doce meses, periodicidad marcada por la política de seguridad.

### op.acc.5.5 Inhabilitación por pérdida o compromiso

Si se pierde o se sospecha el compromiso de la contraseña, la secretaría inhabilita la cuenta el mismo día y la persona puede regenerar una nueva credencial por un enlace al correo verificado.

### op.acc.5.6 Inhabilitación al terminar la relación

Al darse de baja el alumno, la cuenta se inhabilita al cierre del curso.

### op.acc.5.7 Información mínima antes de autorizar

La pantalla de acceso solo muestra los campos de usuario y contraseña. No revela si el usuario existe y, si las credenciales se rechazan, muestra un mensaje genérico sin indicar el motivo.

### op.acc.5.8 Intentos limitados

Tras cinco intentos fallidos, el portal bloquea la cuenta. La intervención específica para reactivarla es la petición a la secretaría, que verifica la identidad por teléfono y desbloquea la cuenta. Esto se describe en la guía de acceso del portal.

### op.acc.5.9 Información de derechos y obligaciones tras el acceso

Tras entrar, el portal muestra un aviso con los derechos y obligaciones de la persona y un enlace a las condiciones de uso.

### op.acc.5.r1.1 Contraseña como mecanismo (refuerzo R1)

El mecanismo de autenticación de las familias es una contraseña.

### op.acc.5.r1.2 Complejidad y robustez (refuerzo R1)

El portal exige contraseñas de al menos doce caracteres, con letras y números, rechaza las más comunes y limita los intentos.

## Usuarios de la organización

### op.acc.6.3 Credenciales bajo control exclusivo del usuario

Cada persona del personal crea su contraseña al recibir la cuenta; la responsable del sistema no la conoce. La cuenta solo se activa cuando la persona la ha cambiado y ha enrolado su segundo factor.

### op.acc.6.7 Información mínima antes de autorizar

La página de acceso al correo y a la aplicación no muestra información sobre el sistema o la cuenta y, si se rechaza el acceso, no informa del motivo.

### op.acc.6.8 Número limitado de intentos

Tras diez intentos fallidos el correo bloquea el acceso. Para reactivarla, la persona avisa a la responsable del sistema, que verifica su identidad en persona y reactiva la cuenta desde el panel de administración, procedimiento descrito en la guía del personal.

### op.acc.6.9 Información de derechos y obligaciones tras el acceso

Tras iniciar sesión en la aplicación, se muestra un recordatorio de las obligaciones del personal con un enlace a la normativa de seguridad.

### op.acc.6.r2.1 Segundo factor (refuerzo R2)

Además de la contraseña, el personal se autentica con una contraseña de un solo uso (OTP) generada por una aplicación en su móvil, «algo que se tiene».

### op.acc.6.r8.1 Doble factor desde zonas no controladas (refuerzo R8)

El acceso desde fuera de la oficina, zona no controlada porque incluye internet, exige siempre contraseña y OTP. Dentro de la oficina también se pide, por lo que no hay excepción.

### op.acc.6.r9.1 ITS de interconexión (refuerzo R9)

Para el acceso remoto se aplica la Instrucción Técnica de Seguridad de Interconexión de sistemas de información en lo que corresponde a un acceso desde internet por HTTPS con doble factor.

### op.acc.6.r9.2 Aspectos del acceso remoto (refuerzo R9)

a) Cada acceso remoto lo autoriza el responsable de la seguridad por escrito.
b) Todo el tráfico remoto va cifrado por HTTPS.
c) El acceso remoto a la consola de administración se mantiene inhabilitado y la responsable del sistema solo lo habilita cuando hace falta, durante la intervención.
d) El proveedor de alojamiento y el correo registran en sus registros de auditoría cada conexión remota.
