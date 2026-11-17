"""Escrituras de un dominio, una a la vez dentro de este proceso."""

import threading
from collections.abc import Callable
from functools import wraps


def serializer(lock: threading.Lock) -> Callable[[Callable], Callable]:
    """Decorador: la operación (leer, validar, escribir) corre entera bajo `lock`."""

    def decorate[**P, R](operation: Callable[P, R]) -> Callable[P, R]:
        @wraps(operation)
        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            with lock:
                return operation(*args, **kwargs)

        return run

    return decorate
