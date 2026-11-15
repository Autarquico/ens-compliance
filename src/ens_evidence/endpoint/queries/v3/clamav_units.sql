SELECT id, active_state, sub_state, unit_file_state FROM systemd_units WHERE id LIKE 'clamav-%';
