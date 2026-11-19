# Protección de la información, del correo, de los servicios web y de la navegación

Fecha: 5 de octubre de 2026. Responsable: Noelia Salvatierra Mena.

## mp.info.1.1 Datos personales

La responsable de la seguridad ha recogido los requisitos de protección de datos fijados por la gerencia, como responsable del tratamiento, con el asesoramiento del delegado de protección de datos externo, teniendo en cuenta la naturaleza, el alcance, el contexto y los fines del tratamiento y los riesgos para los derechos y libertades conforme a los artículos 24 y 32 del RGPD. No se ha llevado a cabo una evaluación de impacto, por lo que no hay requisitos derivados de ella.

## mp.info.3.1 Firma electrónica

Los contratos de viaje con empresas se firman con firma electrónica avanzada de la plataforma de firma contratada, de las previstas en el ordenamiento jurídico.

## mp.info.5.1 Limpieza de documentos

Antes de enviar o publicar un documento, el agente lo exporta a PDF y retira metadatos, comentarios y revisiones anteriores con la función de inspección del documento, salvo que esa información sea pertinente para el receptor.

## mp.s.1.1 Protección del cuerpo y anexos del correo

Los mensajes y los adjuntos viajan cifrados con TLS entre servidores y se analizan en busca de código dañino; la documentación de identidad se envía en adjuntos protegidos con contraseña y nunca en el cuerpo.

## mp.s.1.2 Protección del encaminamiento

El dominio de la agencia tiene configurados SPF, DKIM y DMARC y el correo establece las conexiones con TLS, lo que protege el encaminamiento.

## mp.s.1.3 Correo no solicitado

El servicio de correo filtra el spam y pone en cuarentena los mensajes sospechosos.

## mp.s.1.4 Código dañino en el correo

El servicio de correo analiza los adjuntos y bloquea los ejecutables; el antivirus de los portátiles revisa lo descargado.

## mp.s.1.5 Código móvil de tipo micro-aplicación

El correo se muestra sin ejecutar código activo y el navegador bloquea las micro-aplicaciones.

## mp.s.1.6 Limitaciones al uso para comunicaciones privadas

La normativa de seguridad prohíbe usar el correo de la agencia para comunicaciones privadas.

## mp.s.1.7 Concienciación y formación sobre el correo

La responsable de la seguridad trata el uso del correo y las estafas en la reunión mensual de equipo y en la formación anual.

## mp.s.2 Protección del portal de reservas

### mp.s.2.1 Imposibilidad de eludir la autenticación

a) El portal solo ofrece los documentos por HTTPS, con redirección obligatoria desde HTTP, y no por vías alternativas.
b) El portal valida el identificador de cada reserva contra el cliente autenticado, lo que previene la manipulación del localizador.
c) Las cookies de sesión son «Secure», «HttpOnly» y «SameSite» y van firmadas, lo que previene su manipulación.
d) El portal usa consultas parametrizadas, que previenen la inyección de código.

### mp.s.2.2 Escalado de privilegios

El proveedor comprueba el perfil en el servidor en cada petición, de modo que no se puede ampliar el privilegio cambiando una petición.

### mp.s.2.3 Cross site scripting

El portal escapa toda salida y aplica una política de seguridad de contenido que previene los ataques de cross site scripting.

### mp.s.2.r1.1 Auditorías de caja negra (refuerzo R1)

El proveedor del programa de reservas realiza auditorías de caja negra sobre el portal antes de pasar a producción cada versión y entrega un resumen a la agencia.

### mp.s.2.r1.2 Frecuencia de las auditorías (refuerzo R1)

El procedimiento de auditoría del proveedor fija una auditoría de caja negra antes de cada versión mayor y, como mínimo, una al año.

## mp.s.3 Protección de la navegación web

### mp.s.3.1 Normativa de utilización

La normativa de seguridad fija el uso autorizado de internet, limita el uso personal a pausas y exige conexiones cifradas HTTPS.

### mp.s.3.2 Concienciación sobre la navegación

Cada mes, en la reunión de equipo, se recuerdan las buenas prácticas de navegación segura.

### mp.s.3.3 Formación del personal de administración

El administrador del sistema recibe cada año formación en monitorización del servicio y respuesta a incidentes.

### mp.s.3.4 Información de resolución de direcciones

Los portátiles usan un servicio de resolución de nombres con cifrado DNS sobre HTTPS y conexiones TLS.

### mp.s.3.5 Protección general del puesto

El navegador bloquea sitios maliciosos con su lista de protección y el antivirus revisa las descargas.

### mp.s.3.6 Programas dañinos

El navegador y el antivirus impiden descargar ejecutables no firmados y bloquean las páginas activas peligrosas, lo que reduce la exposición a spyware y ransomware.

### mp.s.3.7 Control de cookies

Los portátiles tienen perfiles separados del navegador para el uso organizativo y el personal, con cookies independientes.
