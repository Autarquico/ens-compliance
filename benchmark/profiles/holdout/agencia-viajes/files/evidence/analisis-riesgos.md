# Análisis de riesgos informal de Brújula Austral Viajes

Fecha: 22 de septiembre de 2026. Autora: Noelia Salvatierra Mena, responsable de la seguridad. Próxima revisión: octubre de 2027.

## op.pl.1.1 Activos más valiosos

Los activos más valiosos son el servidor de archivos (servidor-archivos), con los expedientes escaneados de los viajeros, el programa de reservas (crm-reservas), el correo (cuentas-correo) y la copia nocturna (copia-nube).

## op.pl.1.2 Amenazas más probables

1. Estafas por correo que suplantan a un mayorista o a un cliente.
2. Fallo del disco o del equipo del servidor de archivos.
3. Robo o pérdida de un portátil en un viaje comercial.
4. Cifrado malicioso de ficheros (ransomware) por un adjunto.
5. Caída del programa de reservas en temporada alta.

## op.pl.1.3 Salvaguardas

1. Segundo factor en el correo y en el programa de reservas, y recordatorio mensual sobre estafas.
2. Discos del servidor en espejo y copia nocturna cifrada fuera de la oficina.
3. Disco cifrado en los portátiles y procedimiento de aviso de pérdidas.
4. Antivirus con protección en tiempo real y copias que permiten restaurar versiones anteriores.

## op.pl.1.4 Riesgos residuales

1. La caída prolongada del proveedor del programa de reservas: se acepta y se tiene la alternativa de reservar en los portales de los mayoristas.
2. Un adjunto malicioso que burle el antivirus: el riesgo se reduce con las copias pero no desaparece.
3. La recuperación de un expediente tarda hasta un día si hay que restaurar desde la nube.
