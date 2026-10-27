# Workspace de ejemplo

Datos ficticios de "Taller Norte Software". Es un workspace válido y aprobable.
Cópialo fuera del repo del producto (la CLI rechaza destinos dentro de él) y
apunta unos ajustes a la copia:

```sh
cp -R examples/workspace ~/ens-demo
ens-evidence init --settings ~/ens-demo-settings.yaml --path ~/ens-demo
ens-evidence --settings ~/ens-demo-settings.yaml inventory validate
ens-evidence --settings ~/ens-demo-settings.yaml scope approve --by "Ana Ruiz" --role system_owner
```
