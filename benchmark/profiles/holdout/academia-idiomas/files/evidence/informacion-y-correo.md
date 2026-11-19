# Protección de la información, del correo, de los servicios web y de la navegación

Fecha: 30 de septiembre de 2026. Responsable: Íñigo Paredes Lasa.

## mp.info.1.1 Datos personales

El responsable de la seguridad ha recogido los requisitos de protección de datos fijados por la gerencia, como responsable del tratamiento, con el asesoramiento del delegado de protección de datos externo, teniendo en cuenta la naturaleza, el alcance, el contexto y los fines del tratamiento y los riesgos para los derechos y libertades conforme a los artículos 24 y 32 del RGPD. No se ha llevado a cabo una evaluación de impacto, por lo que no hay requisitos derivados de ella.

## mp.info.3.1 Firma electrónica

Los contratos de matrícula de los alumnos adultos se firman con firma electrónica avanzada de la plataforma de firma contratada, de las previstas en el ordenamiento jurídico.

## mp.info.5.1 Limpieza de documentos

Antes de publicar o enviar un documento a un tercero, la secretaría lo exporta a PDF y retira los metadatos, comentarios y revisiones anteriores con la función de inspección de documentos de la ofimática, salvo que esa información interese al destinatario.

## mp.s.1.1 Protección del cuerpo y anexos del correo

Los mensajes y adjuntos viajan cifrados con TLS entre servidores y se analizan en busca de código dañino; los datos personales sensibles no se envían en el cuerpo ni en adjuntos sin contraseña.

## mp.s.1.2 Protección del encaminamiento

El dominio de la academia tiene configurados los registros SPF, DKIM y DMARC, y el correo establece las conexiones con TLS, lo que protege el encaminamiento.

## mp.s.1.3 Correo no solicitado

El servicio de correo filtra el spam y envía a cuarentena los mensajes sospechosos.

## mp.s.1.4 Código dañino en el correo

El servicio de correo analiza los adjuntos y bloquea los ejecutables; el antivirus de los portátiles revisa lo descargado.

## mp.s.1.5 Código móvil de tipo micro-aplicación

El correo se muestra sin ejecutar código activo y el navegador bloquea las micro-aplicaciones.

## mp.s.1.6 Limitaciones al uso para comunicaciones privadas

La normativa de seguridad prohíbe usar el correo de la academia para comunicaciones privadas.

## mp.s.1.7 Concienciación y formación sobre el correo

El responsable de la seguridad incluye el uso del correo y el phishing en la formación anual y en la concienciación trimestral de la plantilla.

## mp.s.2 Protección de la aplicación de matrícula

### mp.s.2.1 Imposibilidad de eludir la autenticación

a) La aplicación solo ofrece los documentos por HTTPS, con redirección obligatoria desde HTTP, y no por vías alternativas.
b) La aplicación valida cada identificador de la URL contra el usuario autenticado, lo que previene la manipulación del localizador.
c) Las cookies de sesión son «Secure», «HttpOnly» y «SameSite», y se firman, para prevenir su manipulación.
d) Los accesos a la base de datos usan consultas parametrizadas, que previenen la inyección de código.

### mp.s.2.2 Escalado de privilegios

Los perfiles se comprueban en el servidor en cada petición, de forma que nadie puede ampliar sus privilegios cambiando una petición.

### mp.s.2.3 Cross site scripting

La aplicación escapa toda salida y aplica una política de seguridad de contenido que previene los ataques de cross site scripting.

### mp.s.2.r1.1 Auditorías de caja negra (refuerzo R1)

Antes de pasar a producción y en cada versión mayor, el proveedor de alojamiento realiza una auditoría de caja negra sobre la aplicación web y entrega un informe.

### mp.s.2.r1.2 Frecuencia de las auditorías (refuerzo R1)

El procedimiento de auditoría fija una auditoría de caja negra antes de cada paso a producción y, como mínimo, una al año.

## mp.s.3 Protección de la navegación web

### mp.s.3.1 Normativa de utilización

La normativa de seguridad fija el uso autorizado de internet, limita el uso personal a pausas y exige conexiones cifradas HTTPS.

### mp.s.3.2 Concienciación sobre la navegación

Cada trimestre se recuerdan al personal las buenas prácticas de navegación segura.

### mp.s.3.3 Formación del personal de administración

La responsable del sistema recibe cada año formación en monitorización del servicio y respuesta a incidentes.

### mp.s.3.4 Información de resolución de direcciones

Los portátiles usan un servicio de resolución de nombres con cifrado DNS sobre HTTPS y conexiones TLS.

### mp.s.3.5 Protección general del puesto

El navegador bloquea sitios maliciosos con su lista de protección y el antivirus revisa las descargas.

### mp.s.3.6 Programas dañinos

El navegador y el antivirus impiden las descargas de ejecutables no firmados y bloquean las páginas activas peligrosas, lo que reduce la exposición a spyware y ransomware.

### mp.s.3.7 Control de cookies

Los portátiles tienen perfiles distintos del navegador para el uso organizativo y el personal, con cookies separadas.

## mp.s.4 Protección frente a la denegación de servicio (medida adoptada de forma voluntaria)

### mp.s.4.1 Capacidad suficiente

El plan del proveedor de alojamiento admite 150 usuarios simultáneos frente a los 40 previstos en los periodos de matrícula, según el estudio de dimensionamiento.

### mp.s.4.2 Tecnologías contra ataques conocidos

El proveedor de alojamiento aplica en su red filtrado de tráfico anómalo y limitación de peticiones por dirección, que previenen los ataques de denegación de servicio conocidos.
