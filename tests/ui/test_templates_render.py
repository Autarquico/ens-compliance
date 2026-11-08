"""Renderiza las plantillas con el contexto de docs/ui/contrato-de-plantillas.md."""

import re
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "ens_evidence" / "ui" / "templates"
STATIC = TEMPLATES.parent / "static"

HOSTILE = "<script>alert(1)</script>"

STEPS = [
    ("sistema", "Sistema", "alcance"),
    ("activos", "Activos", "alcance"),
    ("servicios", "Servicios", "alcance"),
    ("dependencias", "Dependencias", "alcance"),
    ("aprobacion-alcance", "Aprobar alcance", "alcance"),
    ("informacion", "Información", "categorizacion"),
    ("valoraciones", "Valoraciones", "categorizacion"),
    ("aprobacion-categorizacion", "Aprobar categorización", "categorizacion"),
]
WIZARD_TEMPLATES = {
    "sistema": "wizard/sistema.html",
    "activos": "wizard/activos.html",
    "servicios": "wizard/servicios.html",
    "dependencias": "wizard/dependencias.html",
    "aprobacion-alcance": "wizard/aprobacion_alcance.html",
    "informacion": "wizard/informacion.html",
    "valoraciones": "wizard/valoraciones.html",
    "aprobacion-categorizacion": "wizard/aprobacion_categorizacion.html",
}
VIEW_TEMPLATES = [
    "views/estado.html",
    "views/matriz.html",
    "views/evidencias.html",
    "views/expediente.html",
    "views/preauditoria.html",
]
# Solo se importa desde otras plantillas.
INDIRECT = {"_macros.html"}

FORBIDDEN = re.compile(
    r"certifica|certificad|certificaci[oó]n|garantiza el cumplimiento|cumple el ens", re.IGNORECASE
)
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
INLINE_STYLE = re.compile(r"<style|\sstyle\s*=", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=|\shx-on[:\-]|javascript:", re.IGNORECASE)


def _wizard(step: str, **extra):
    index = [s[0] for s in STEPS].index(step)
    return {
        "page_title": "Asistente",
        "nav_active": "asistente",
        "steps": [
            {
                "slug": s,
                "label": label,
                "group": group,
                "state": "done" if i < index else "current" if i == index else "todo",
            }
            for i, (s, label, group) in enumerate(STEPS)
        ],
        "step": step,
        "step_number": index + 1,
        "step_total": len(STEPS),
        "step_title": STEPS[index][1],
        "form_action": f"/asistente/{step}",
        "prev_step": STEPS[index - 1][0] if index else None,
        "next_step": STEPS[index + 1][0] if index + 1 < len(STEPS) else None,
        "hx_request": False,
        "errors": {},
        "form_errors": [],
        "saved": False,
        "values": {},
        "approver_default": "ana@example.org",
        **extra,
    }


ASSET_CHOICES = [{"id": "portatil-ana", "label": "Portátil"}, {"id": "db-main", "label": None}]
ELEMENTS = [
    {
        "kind": "information",
        "id": "datos-clientes",
        "label": HOSTILE,
        "role_label": "su responsable",
        "ratings": {"C": {"level": "ALTO", "justification": "Datos personales"}},
        "valuation": {"valued_by": "Ana", "valued_at": "2026-11-08 10:00"},
    },
    {
        "kind": "service",
        "id": "facturacion",
        "label": "Facturación",
        "role_label": "su responsable",
        "ratings": {},
        "valuation": None,
    },
]
WIZARD_EXTRA = {
    "sistema": {"values": {"name": HOSTILE, "description": "Plataforma"}},
    "activos": {
        "assets": [
            {
                "id": "portatil-ana",
                "type": "laptop",
                "type_label": "Portátil",
                "summary": HOSTILE,
                "deployment": "planned",
                "included": True,
            },
        ],
        "asset_types": [{"value": "laptop", "label": "Portátil"}],
        "new_asset": {
            "type": "laptop",
            "type_label": "Portátil",
            "fields": [
                {"name": "os", "label": "Sistema operativo", "kind": "text"},
                {"name": "encrypted", "label": "Cifrado", "kind": "bool"},
            ],
        },
    },
    "servicios": {
        "services": [
            {"id": "facturacion", "name": "Facturación", "assets": ["db-main"], "included": False}
        ],
        "asset_choices": ASSET_CHOICES,
    },
    "dependencias": {
        "dependencies": [
            {"id": "app-db", "asset": "app", "depends_on": "db-main", "purpose": None}
        ],
        "asset_choices": ASSET_CHOICES,
    },
    "aprobacion-alcance": {
        "scope_state": "NOT_APPROVED",
        "scope_errors": ["Falta una descripción"],
        "scope_summary": {"name": "Plataforma", "assets_count": 2, "services_count": 1},
        "approval": None,
    },
    "informacion": {"information": [{"id": "datos-clientes", "description": "Clientes"}]},
    "valoraciones": {"elements": ELEMENTS},
    "aprobacion-categorizacion": {
        "categorization_state": "PENDING",
        "category": "MEDIA",
        "dimension_levels": [
            {"code": "C", "level": "MEDIO"},
            {"code": "D", "level": "not_affected"},
        ],
        "pending": ["service:facturacion"],
        "issues": [],
        "approval": {"approved_by": "Ana", "approved_at": "2026-11-08 10:00"},
    },
}

CHROME = {
    "product_name": "Producto",
    "identity_email": "ana@example.org",
    "nav_active": "estado",
    "page_title": "Página",
}
VIEW_CONTEXT = {
    "views/estado.html": {
        "readiness": {"state": "NOT_READY", "summary": "Faltan evidencias"},
        "scope_state": "APPROVED",
        "categorization_state": "CURRENT",
        "category": "BASICA",
        "next_step_url": "/asistente/sistema",
        "next_step_label": "Seguir con el asistente",
        "blockers": [
            {"kind": "MISSING_EVIDENCE", "measure_id": "op.pl.1", "text": "Sin evidencia"},
            {"kind": "MISSING_REVIEW", "measure_id": None, "text": HOSTILE},
        ],
        "generated_at": "2026-11-08 10:00",
    },
    "views/matriz.html": {
        "rows": [
            {
                "measure_id": "op.pl.1",
                "title": "Análisis de riesgos",
                "applicability": "APPLICABLE",
                "assessment": "NOT_ASSESSED",
                "evidence_count": 0,
            }
        ],
        "generated_at": "2026-11-08 10:00",
    },
    "views/evidencias.html": {
        "manifests": [
            {
                "id": "ev-1",
                "source": "github",
                "collected_at": "2026-11-08 10:00",
                "status": "SUCCESS",
            }
        ],
    },
    "views/expediente.html": {
        "dossier_state": "GENERATED",
        "generated_at": "2026-11-08 10:00",
        "declared": None,
        "sections": [
            {
                "title": "Alcance",
                "file": "alcance.md",
                "state": "GENERATED",
                "generated_at": "2026-11-08 10:00",
                "fingerprint": "abcdef0123456789",
            }
        ],
    },
    "views/preauditoria.html": {
        "generated_at": "2026-11-08 10:00",
        "layer_status": {"Determinista": "DONE", "Juicio": "NOT_RUN"},
        "findings": [
            {
                "id": "f1",
                "measure_id": "op.pl.1",
                "classification": "NC_MENOR",
                "disposition": "OPEN",
                "text": "Falta el análisis",
                "criterion": "C1",
                "layer_label": "Determinista",
            }
        ],
    },
}


def _environment():
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        autoescape=jinja2.select_autoescape(["html"], default=True),
        undefined=jinja2.StrictUndefined,
    )


def _cases():
    for name in VIEW_TEMPLATES:
        yield name, {**CHROME, **VIEW_CONTEXT[name]}
    for step, name in WIZARD_TEMPLATES.items():
        for hx in (False, True):
            yield name, {**CHROME, **_wizard(step, hx_request=hx, **WIZARD_EXTRA[step])}
    for status in (401, 403, 409, 500):
        yield "error.html", {**CHROME, "status": status, "message": None}
    yield "error.html", {**CHROME, "status": 403, "message": HOSTILE}


def _with_errors(name, context):
    if not name.startswith("wizard/"):
        return context
    bad = {
        k: ["Campo no válido"]
        for k in (
            "name",
            "description",
            "asset_id",
            "service_id",
            "service_name",
            "dependency_id",
            "info_id",
            "approved_by",
            "asset",
            "depends_on",
            "included_assets",
            "service_assets",
        )
    }
    return {**context, "errors": bad, "form_errors": ["No se ha podido guardar"], "saved": True}


def _assert_clean(name, html):
    assert not FORBIDDEN.search(html), name
    assert not INLINE_SCRIPT.search(html), name
    assert not INLINE_STYLE.search(html), name
    assert not INLINE_HANDLER.search(html), name
    assert HOSTILE not in html, f"{name}: texto sin escapar"


@pytest.mark.parametrize(
    "name,context", list(_cases()), ids=lambda v: v if isinstance(v, str) else ""
)
def test_template_renders_clean(name, context):
    env = _environment()
    for ctx in (context, _with_errors(name, context)):
        _assert_clean(name, env.get_template(name).render(**ctx))


def test_every_template_is_covered():
    covered = {name for name, _ in _cases()}
    existing = {p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.html")}
    # parciales y shell se renderizan a través de los pasos; base a través de todo.
    indirect = (
        INDIRECT
        | {"base.html", "wizard/shell.html"}
        | {p for p in existing if p.startswith("partials/")}
    )
    assert existing - covered - indirect == set()


def test_wizard_exposes_progress_and_editable_approver():
    env = _environment()
    context = {**CHROME, **_wizard("aprobacion-alcance", **WIZARD_EXTRA["aprobacion-alcance"])}
    html = env.get_template(WIZARD_TEMPLATES["aprobacion-alcance"]).render(**context)
    assert "Paso 5 de 8" in html
    assert 'name="approved_by"' in html and 'value="ana@example.org"' in html
    assert "Guardar" not in html
    assert "Aprobar alcance" in html


def test_htmx_response_is_a_fragment():
    env = _environment()
    context = {**CHROME, **_wizard("sistema", hx_request=True, **WIZARD_EXTRA["sistema"])}
    html = env.get_template(WIZARD_TEMPLATES["sistema"]).render(**context)
    assert "<html" not in html and 'hx-swap-oob="true"' in html


def test_states_are_not_colour_only():
    env = _environment()
    html = env.get_template("views/matriz.html").render(
        **CHROME, **VIEW_CONTEXT["views/matriz.html"]
    )
    assert "Sin evaluar" in html and "<svg" in html


def test_base_carries_safe_htmx_config_and_no_cdn():
    env = _environment()
    html = env.get_template("views/matriz.html").render(
        **CHROME, **VIEW_CONTEXT["views/matriz.html"]
    )
    for flag in ('"allowEval": false', '"selfRequestsOnly": true', '"allowScriptTags": false'):
        assert flag in html
    assert not re.search(r'(?:src|href)="https?://', html)
    for ref in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
        assert (STATIC / ref).is_file(), ref


def test_css_has_no_remote_references():
    css = (STATIC / "ui.css").read_text()
    assert "http" not in css and "@import" not in css
