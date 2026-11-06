# Protección de claves criptográficas

Fecha: 28 de septiembre de 2026. Responsable: Íñigo Paredes Lasa.

Las claves criptográficas que usa la academia son las de cifrado de los discos de los portátiles, la clave de recuperación del gestor de contraseñas y el certificado TLS del dominio de la aplicación, gestionado por el proveedor de alojamiento.

## op.exp.10.1 Protección durante todo el ciclo de vida

1. Generación: las claves de disco las genera el sistema operativo al activar el cifrado.
2. Transporte al punto de explotación: la clave de recuperación del gestor se imprime y entrega en mano, en sobre cerrado, al responsable de la seguridad.
3. Custodia durante la explotación: el sobre se guarda en la caja de la oficina y el certificado lo custodia el proveedor de alojamiento.
4. Archivo posterior a su retirada: las claves retiradas se guardan en un sobre cerrado en la caja, separado del de las claves activas.
5. Destrucción final: al caducar se destruyen con la trituradora, delante de dos personas.

## op.exp.10.2 Medios de generación aislados

Los medios de generación son el módulo TPM de cada portátil y el servicio del gestor de contraseñas, ambos independientes de la aplicación de matrícula y del servidor del proveedor de alojamiento, que son los medios de explotación.

## op.exp.10.3 Archivo en medios aislados

Las claves retiradas que deben conservarse se archivan en un sobre de la caja de la oficina, sin conexión alguna con los medios de explotación.
