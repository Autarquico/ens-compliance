# Protección de las comunicaciones

Fecha: 5 de octubre de 2026. Responsable: Gonzalo Iglesias Pardo.

## mp.com.1.1 Protección perimetral

El router con cortafuegos del proveedor de conectividad separa la red de la oficina de internet y todo el tráfico de entrada y salida lo atraviesa. No hay otra salida a internet.

## mp.com.2.1 Redes privadas virtuales cifradas

La agencia no tiene redes remotas propias que unir, por lo que no usa redes privadas virtuales. Cuando la comunicación discurre por internet, fuera del dominio de seguridad de la agencia, se usa siempre HTTPS con TLS; la subida de la copia a la nube y el correo entre servidores también van cifrados con TLS.

## mp.com.3.1 Autenticidad del otro extremo

Antes de intercambiar información con el programa de reservas, el correo o el servicio de copias, el cliente comprueba el certificado TLS del servicio y no continúa si no es válido.

## mp.com.3.2 Prevención de ataques activos

El cifrado TLS impide:
a) la alteración de la información en tránsito,
b) la inyección de información espuria, y
c) el secuestro de la sesión por una tercera parte, para lo que las sesiones caducan a los treinta minutos de inactividad.
Si se detecta un ataque de este tipo se activa el procedimiento de incidentes.

## mp.com.3.3 Mecanismos de identificación y autenticación admitidos

La agencia acepta los mecanismos de identificación y autenticación previstos en el ordenamiento jurídico para tratar con los clientes y no impone otros adicionales.
