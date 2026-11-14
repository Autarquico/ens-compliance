# Protección de las comunicaciones

Fecha: 30 de septiembre de 2026. Responsable: Lucía Barrios Soto.

## mp.com.1.1 Protección perimetral

El router con cortafuegos del proveedor de conectividad separa la red de la oficina de internet y todo el tráfico de entrada y salida atraviesa ese sistema. No hay otra salida a internet: los portátiles no usan puntos de acceso móviles en la oficina.

## mp.com.2.1 Redes privadas virtuales cifradas

La academia no necesita redes privadas virtuales porque no hay comunicaciones entre redes propias. Cuando la comunicación discurre por internet, fuera del dominio de seguridad de la academia, se usa siempre HTTPS con TLS, que cifra el canal, y el correo se envía con TLS entre servidores.

## mp.com.3.1 Autenticidad del otro extremo

Antes de intercambiar información con el servicio de alojamiento o el correo, el navegador comprueba el certificado TLS del dominio y no continúa si no es válido.

## mp.com.3.2 Prevención de ataques activos

El cifrado TLS impide:
a) la alteración de la información en tránsito,
b) la inyección de información espuria, y
c) el secuestro de la sesión por una tercera parte, para lo que las sesiones caducan a las dos horas de inactividad.
Si el proveedor o el antivirus detectan un ataque de este tipo, se activa el procedimiento de incidentes.

## mp.com.3.3 Mecanismos de identificación y autenticación admitidos

La academia acepta los mecanismos de identificación y autenticación previstos en el ordenamiento jurídico para sus comunicaciones con las familias, y no impone otros adicionales.
