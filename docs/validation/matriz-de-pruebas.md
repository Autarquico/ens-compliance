# Matriz de pruebas de validación

Relaciona cada fila de la tabla de validación de la guía
(`docs/ens-compliance-guide.md:965-975`, más la línea 977) con:

1. los tests automáticos que la cubren (`fichero::test`, con datos ficticios, fixtures y sin red);
2. la prueba real que ejecuta una persona (procedimiento completo en
   [validación y cierre](../runbooks/validacion-y-cierre.md), sección 2);
3. dónde se registra el resultado real.

Valida la herramienta; no declara conformidad con el ENS. Los resultados reales
se guardan **solo en el workspace de la organización, nunca en este repositorio**:
con `ens-evidence evidence import` (evidencia manual) o, para la restauración,
con `ens-evidence backup restore-test record`.

Los tests de `tests/guide_table/test_guide_table.py` cierran los huecos que no tenían
test de extremo a extremo por la CLI. Los demás son preexistentes.

| Fila de la guía | Tests automáticos | Prueba real (resumen) | Dónde se registra |
|---|---|---|---|
| `:967` Revocar API test: `ERROR`, nunca `PASS` | `tests/connectors/github/test_cli_collect.py::test_credencial_revocada_sale_con_1_y_guarda_la_evidencia_de_error`; `tests/connectors/github/test_checks_scenarios.py::test_credencial_revocada_al_inicio_no_da_pass_en_ningun_check`; `tests/reports/test_acceptance.py::test_una_recogida_caida_no_recupera_el_pass_anterior` | Revocar un token de prueba, ejecutar `collect` y `check --all`: debe salir `ERROR` en la recogida y ningún `PASS` | `evidence import` manual (salida de `collect` y `check`) |
| `:968` Desconectar agente: detectar cobertura perdida | `tests/endpoint/test_checks_coverage.py::test_host_001_falla_para_el_activo_del_alcance_sin_evidencia`; `tests/endpoint/test_checks_laptop_e2e.py::test_la_evidencia_caducada_deja_todos_los_checks_unknown_y_bloquea_la_preparacion`; `tests/endpoint/test_endpoint_check_command.py::test_con_evidencia_caducada_lo_indica_y_sale_con_4` | Dejar caducar la evidencia de un endpoint de prueba y ejecutar `endpoint check` y `assessment status`: los checks HOST no dan `PASS` y la preparación queda bloqueada. Con agente: parar `osqueryd` más de 3 h y ver que `agent hosts` deja de avanzar y el panel marca «Agente sin reportar» | `evidence import` manual |
| `:969` Quitar protección test: detectar desviación | `tests/guide_table/test_guide_table.py::test_la_proteccion_quitada_da_fail_en_check`; `::test_el_cambio_de_pass_a_fail_aparece_en_el_informe_diario`; `::test_la_proteccion_quitada_deja_la_preparacion_bloqueada`; `tests/connectors/github/test_checks_branch_protection.py::test_sin_ninguna_regla_ni_proteccion_clasica_da_fail` | Quitar la protección de rama de un repositorio de prueba, recoger y ejecutar `check --all`, `report daily` y `assessment status` | `evidence import` manual |
| `:970` Backup fallido: hallazgo | `tests/backup/test_failed_commands_leave_a_failed_record.py::test_backup_run_con_el_repositorio_inaccesible_queda_failed_y_con_su_error`; `tests/reports/test_acceptance.py::test_la_copia_activada_por_defecto_sin_credenciales_termina_con_backup_failed` | Hacer fallar la copia a un repositorio de prueba (credencial inválida) y ejecutar `backup run`: queda registro `failed` y el siguiente informe lo muestra | `evidence import` manual |
| `:971` Restore: evidencia | `tests/backup/test_restore_records.py::test_restore_registra_repositorio_snapshot_destino_e_inventario_verificado`; `tests/backup/test_restore_test_record.py::test_una_declaracion_completa_se_guarda_una_vez_y_queda_como_evidencia_manual` | Restaurar en una máquina limpia, comprobar el inventario y registrar la prueba | `backup restore-test record` |
| `:972` Alterar evidencia: el hash falla | `tests/evidence/test_verify.py::test_evidencia_alterada_falla_mostrando_hash_esperado_y_obtenido`; `tests/evidence/test_cli_evidence.py::test_verify_de_una_evidencia_alterada_sale_con_1_mostrando_ambos_hashes`; `tests/checks/test_acceptance.py::test_retocar_el_manifiesto_del_fail_nunca_devuelve_pass_ni_preparada` | Alterar un byte de una evidencia en una copia restaurada y ejecutar `evidence verify`: debe fallar el hash | `evidence import` manual (salida de `verify`) |
| `:973` LLM caído: los checks siguen | `tests/guide_table/test_guide_table.py::test_con_el_proveedor_caido_la_capa_determinista_termina_y_la_de_juicio_da_error` (`ConnectError` y `ReadTimeout`); `tests/preaudit/test_acceptance.py::test_without_a_provider_the_deterministic_layer_completes_and_review_is_pending` | Ejecutar `preaudit run` con el proveedor inalcanzable: la capa determinista termina, la de juicio da `ERROR` y queda «revisión humana pendiente» | `evidence import` manual |
| `:974` Storage caído: la recogida no es OK | `tests/guide_table/test_guide_table.py::test_con_el_storage_caido_la_recogida_sale_con_error_y_no_registra_ninguna_evidencia`; `::test_tras_una_recogida_con_el_storage_caido_check_no_da_pass`; `tests/backup/test_restic_flow.py::test_storage_caido_registra_failed_sin_secretos_y_nunca_completed` | Cortar el acceso al almacenamiento de un workspace de prueba y ejecutar `collect`: sale con error y no hay evidencia nueva | `evidence import` manual |
| `:975` Reejecución: idempotencia | `tests/checks/test_acceptance.py::test_repetir_check_all_con_la_misma_evidencia_no_cambia_ni_duplica`; `tests/checks/test_cli_check.py::test_repetir_sin_cambios_no_escribe_y_dice_desde_que_ejecucion` | Repetir `check --all` sin recoger de nuevo: no se añade ninguna ejecución | `evidence import` manual |
| `:977` Recuperación de `ens-evidence` | `tests/backup/test_collector_commands_with_real_restic.py::test_restauracion_y_restore_test_se_encadenan_de_extremo_a_extremo` (requiere `restic`) | Restaurar el workspace en una máquina limpia y ejecutar `evidence verify --all` | `backup restore-test record` |

## Límites conocidos

- La fila de `:968` se prueba con la evidencia de endpoint caducada. El bloqueo de la
  preparación se comprueba sobre `evaluate_readiness` (test de `tests/endpoint`), no a través
  de `assessment status`, porque el mundo de aceptación no contiene evidencia de endpoint.
- Un test automático demuestra que la herramienta se comporta así con datos ficticios; no
  demuestra que tu entorno real esté configurado: para eso está la prueba real.
