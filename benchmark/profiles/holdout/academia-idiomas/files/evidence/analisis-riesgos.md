# Análisis de riesgos informal de Academia Cala Verde

Fecha: 15 de septiembre de 2026. Autor: Íñigo Paredes Lasa, responsable de la seguridad. Revisión prevista: septiembre de 2027.

## op.pl.1.1 Activos más valiosos

Los activos más valiosos son la base de datos del alumnado (bd-alumnado), la aplicación de matrícula (app-matricula) que la utiliza, las cuentas de correo (cuentas-correo) y la copia semanal (copias-semanales). Siguen la numeración del inventario de activos vigente.

## op.pl.1.2 Amenazas más probables

1. Suplantación de identidad por correo (phishing) contra el personal de secretaría.
2. Pérdida o robo de un portátil fuera de la oficina.
3. Caída del proveedor de alojamiento durante los periodos de matrícula.
4. Borrado accidental de fichas de alumnado.
5. Código dañino que llega por ficheros adjuntos.

## op.pl.1.3 Salvaguardas

1. Segundo factor en el correo y en la aplicación de matrícula, y formación trimestral sobre phishing.
2. Disco cifrado en los dos portátiles y bloqueo automático por inactividad.
3. Copia diaria en el proveedor y copia semanal en un disco externo cifrado.
4. Antivirus con protección en tiempo real y permisos mínimos por perfil en la aplicación.

## op.pl.1.4 Riesgos residuales

1. Una caída prolongada del proveedor en pleno periodo de matrícula: se aceptó, con matrícula en papel como alternativa.
2. Un fallo humano al abrir un adjunto malicioso: se reduce con la formación pero no desaparece.
3. La pérdida de la clave de recuperación del gestor de contraseñas, custodiada en un único sobre.
