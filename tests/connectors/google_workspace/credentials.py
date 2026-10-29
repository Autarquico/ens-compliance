"""Cuenta de servicio de prueba: la clave RSA se genera en el test, nunca es una real."""

import base64
import json
from functools import cache
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

CLIENT_EMAIL = "ens-collector@proyecto-prueba.iam.gserviceaccount.com"
EVIL = "https://evil.example"


@cache
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@cache
def private_pem() -> str:
    return (
        private_key()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def pem_canary() -> str:
    """Un tramo del cuerpo base64 de la clave privada: no debe aparecer en ninguna salida."""
    return private_pem().splitlines()[1]


def key_json(**changes: Any) -> str:
    """Fichero de credenciales; sus URLs apuntan a un host hostil que nunca debe recibir nada."""
    document = {
        "type": "service_account",
        "project_id": "proyecto-prueba",
        "private_key_id": "0123456789abcdef0123456789abcdef01234567",
        "private_key": private_pem(),
        "client_email": CLIENT_EMAIL,
        "client_id": "123456789012345678901",
        "auth_uri": f"{EVIL}/auth",
        "token_uri": f"{EVIL}/token",
        "auth_provider_x509_cert_url": f"{EVIL}/certs",
        "client_x509_cert_url": f"{EVIL}/x509/{CLIENT_EMAIL}",
        "universe_domain": "evil.example",
    }
    for name, value in changes.items():
        if value is None:
            document.pop(name, None)
        else:
            document[name] = value
    return json.dumps(document)


def _decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode_assertion(assertion: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cabecera y carga de un JWT, tras verificar su firma RS256 con la clave pública."""
    head, body, signature = assertion.split(".")
    private_key().public_key().verify(
        _decode(signature), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return json.loads(_decode(head)), json.loads(_decode(body))
