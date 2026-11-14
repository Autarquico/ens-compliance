"""`agent install`: genera el script que instala y configura osquery en un equipo."""

from ens_evidence.agent.install.release import InstallError
from ens_evidence.agent.install.render import render_install_script

__all__ = ["InstallError", "render_install_script"]
