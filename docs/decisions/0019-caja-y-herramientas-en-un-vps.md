# 0019 — La caja y el kit de herramientas viven en un VPS

- Estado: aceptada
- Fecha: 2026-11-09

## Contexto

La visión colocaba la caja en «una VM (o un portátil para evaluar)», y ya
anotaba como riesgo que un portátil no da evidencia continua. El dueño quiere
que la herramienta también provea una herramienta open source para cada
carencia: copias, almacén de claves, contraseñas, identidad y antimalware
(`docs/specs/toolkit-server.md`). Esas herramientas tienen que estar siempre
encendidas, igual que el scheduler, el servidor osquery y la detección de la
caja. Repartirlas entre un ordenador y un servidor dejaría dos sitios que
mantener, y la parte del ordenador seguiría recogiendo solo a ratos.

## Decisión

- **Producción en un VPS.** La caja (caddy + app) y el kit de herramientas
  viven en el mismo servidor siempre encendido: un VPS que contrata la pyme
  (OVH, Hetzner, Scaleway…) o un servidor propio. Cada herramienta es un
  perfil de Compose más, como `deteccion` (ADR 0016).
- **El ordenador es cliente.** Desde él se abre la web por Cloudflare Access o
  VPN. Instalar la caja en un portátil sigue siendo válido para evaluar la
  herramienta, sin evidencia continua y sin el kit.
- **El servidor no guarda sus propios rescates.** Las copias salen a S3 con
  bloqueo de objetos (ADR 0008). Las partes de desbloqueo de OpenBao quedan en
  manos de personas, registradas como revisión humana.
- **La app no administra el servidor.** Genera el paquete y el comando de
  instalación o actualización, y el usuario los ejecuta. Para recoger
  evidencia usa solo credenciales de lectura (reglas 7 a 9).

## Consecuencias

- El servidor concentra el expediente, las claves y las copias. Necesita un
  bastionado mínimo y una review de seguridad en cada perfil nuevo.
- El tamaño del VPS recomendado se fija midiendo la RAM del conjunto.
- El servidor es un activo del alcance, y se autoevalúa como cualquier otro.
- Cambia `docs/vision.md` (Arquitectura y Servidor de herramientas) y la spec
  `toolkit-server`. `installer` instala en un VPS como caso principal.
