# Mecanismos de autenticación de usuarios externos y de la organización

Fecha: 30 de septiembre de 2026. Autor: Gonzalo Iglesias Pardo. Aprobado por Noelia Salvatierra Mena.

## Usuarios externos (clientes del portal de reservas)

### op.acc.5.1 Registro fidedigno previo

Un cliente solo obtiene credenciales tras identificarse en el mostrador o por videollamada con su documento de identidad, y el agente comprueba su identidad contra el contrato de viaje.

### op.acc.5.2 Reconocimiento de la recepción de las credenciales

Antes de activar el acceso, el cliente acepta en pantalla que ha recibido las credenciales y que acepta el deber de custodia diligente, la confidencialidad y el aviso inmediato de pérdida.

### op.acc.5.3 Credenciales bajo el control del usuario

El cliente elige su contraseña en la activación. La agencia no la conoce y la cuenta solo se activa cuando el cliente la ha fijado.

### op.acc.5.4 Cambio periódico

El portal solicita cambiar la contraseña cada doce meses, según la política de seguridad.

### op.acc.5.5 Inhabilitación por pérdida o compromiso

Si el cliente pierde la contraseña o se sospecha que está comprometida, un agente inhabilita la cuenta el mismo día y el cliente puede regenerar la credencial desde su correo verificado.

### op.acc.5.6 Inhabilitación al terminar la relación

La cuenta se inhabilita doce meses después del fin del último viaje contratado.

### op.acc.5.7 Información mínima antes de autorizar

La pantalla inicial solo muestra usuario y contraseña, no revela si el usuario existe y el rechazo de credenciales no indica el motivo.

### op.acc.5.8 Intentos limitados

Tras cinco intentos fallidos el portal bloquea la cuenta. Para reactivarla el cliente llama a la agencia y un agente verifica su identidad con tres datos de la reserva, procedimiento descrito en la ayuda del portal.

### op.acc.5.9 Información de derechos y obligaciones tras el acceso

Al entrar, el portal muestra un aviso con los derechos y las obligaciones del cliente y un enlace a las condiciones de uso.

### op.acc.5.r2.1 Contraseña de un solo uso (refuerzo R2)

Además de la contraseña, el portal exige un código de un solo uso (OTP) que envía al correo verificado del cliente en cada acceso.

## Usuarios de la organización

### op.acc.6.3 Credenciales bajo el control del usuario

Cada persona del personal elige su contraseña al activar su cuenta; el administrador del sistema no la conoce y la cuenta solo se activa tras fijarla y configurar el segundo factor.

### op.acc.6.7 Información mínima antes de autorizar

La página de acceso al correo y al programa de reservas no ofrece información del sistema ni de la cuenta y el rechazo no indica el motivo.

### op.acc.6.8 Número limitado de intentos

Tras seis intentos fallidos se bloquea el acceso. Para reactivarlo, la persona avisa al administrador del sistema, que comprueba su identidad en persona y desbloquea la cuenta; el procedimiento está descrito en la guía interna.

### op.acc.6.9 Información de derechos y obligaciones tras el acceso

Tras iniciar sesión en el programa de reservas se muestra un recordatorio de las obligaciones del personal con un enlace a la normativa de seguridad.

### op.acc.6.r2.1 Segundo factor (refuerzo R2)

Además de la contraseña, cada persona usa una contraseña de un solo uso (OTP) generada por una aplicación de su móvil, «algo que se tiene».

### op.acc.6.r8.1 Doble factor desde zonas no controladas (refuerzo R8)

El acceso desde fuera de la oficina, zona no controlada por incluir internet, exige siempre contraseña y OTP; dentro de la oficina también se exige, por lo que no hay excepción.

### op.acc.6.r9.1 ITS de interconexión (refuerzo R9)

En el acceso remoto se aplica la Instrucción Técnica de Seguridad de Interconexión de sistemas de información en lo que corresponde a un acceso desde internet por HTTPS con doble factor.

### op.acc.6.r9.2 Aspectos del acceso remoto (refuerzo R9)

a) Cada acceso remoto lo autoriza por escrito la responsable de la seguridad.
b) Todo el tráfico remoto va cifrado por HTTPS.
c) No existe acceso remoto al servidor; el acceso a la administración del correo se habilita solo cuando el administrador del sistema lo necesita y se inhabilita después.
d) El proveedor del programa de reservas y el del correo guardan registros de auditoría de cada conexión remota.
