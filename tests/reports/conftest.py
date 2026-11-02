"""Fixtures compartidas de los tests de informes: las mismas que usa la suite del ciclo."""

from checks.test_no_network import offline  # noqa: F401 (fixture reexportada)
from scheduling.conftest import fake, plugins, settings_path  # noqa: F401 (fixtures reexportadas)
