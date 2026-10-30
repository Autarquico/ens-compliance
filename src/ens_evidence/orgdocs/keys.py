"""Claves del workspace que usa la documentación organizativa."""

from importlib import resources

ANSWERS_KEY = "org/answers.yaml"
DOCS_PREFIX = "org/docs"
HISTORY_PREFIX = f"{DOCS_PREFIX}/history"
TEMPLATES_VERSION = 1
TEMPLATES = resources.files("ens_evidence.orgdocs") / "templates" / f"v{TEMPLATES_VERSION}"


def document_key(name: str) -> str:
    return f"{DOCS_PREFIX}/{name}.md"


def meta_key(name: str) -> str:
    return f"{DOCS_PREFIX}/{name}.meta.yaml"


def document_names() -> list[str]:
    return sorted(
        f.name.removesuffix(".yaml") for f in TEMPLATES.iterdir() if f.name.endswith(".yaml")
    )


def document_keys() -> tuple[str, ...]:
    return tuple(key for name in document_names() for key in (document_key(name), meta_key(name)))
