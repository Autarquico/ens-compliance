"""Conector del gestor de contraseñas (Vaultwarden): lee el export de usuarios, solo lectura."""

from ens_evidence.connectors.passwords.config import PasswordsConfig
from ens_evidence.connectors.passwords.connector import PasswordsConnector

__all__ = ["PasswordsConfig", "PasswordsConnector"]
