"""Planificación del ciclo diario: lock, calendario, recogida y ejecución.

Sin reexportaciones: `scheduling.lock` lo importan `backup/` y la CLI, y cargar aquí
`scheduling.collect` (que depende de los conectores, y estos de `backup/`) cerraría un ciclo.
Cada módulo se importa por su ruta completa.
"""
