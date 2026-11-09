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
    ("riesgos-metodologia", "Metodología", "riesgos"),
    ("riesgos", "Riesgos", "riesgos"),
    ("aprobacion-riesgos", "Aprobar riesgos", "riesgos"),
    ("aplicabilidad", "Medidas", "aplicabilidad"),
    ("aprobacion-aplicabilidad", "Firmar la Declaración", "aplicabilidad"),
    ("documentos-organizacion", "Tu organización", "documentos"),
    ("documentos-generar", "Generar", "documentos"),
    ("documentos-aprobar", "Aprobar", "documentos"),
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
    "riesgos-metodologia": "wizard/riesgos_metodologia.html",
    "riesgos": "wizard/riesgos_registro.html",
    "aprobacion-riesgos": "wizard/riesgos_aprobacion.html",
    "aplicabilidad": "wizard/aplicabilidad_medidas.html",
    "aprobacion-aplicabilidad": "wizard/aplicabilidad_aprobacion.html",
    "documentos-organizacion": "wizard/documentos_bloque.html",
    "documentos-generar": "wizard/documentos_generar.html",
    "documentos-aprobar": "wizard/documentos_aprobar.html",
}
VIEW_TEMPLATES = [
    "views/estado.html",
    "views/matriz.html",
    "views/matriz_medida.html",
    "views/evidencias.html",
    "views/evidencias_nueva.html",
    "views/expediente.html",
    "views/preauditoria.html",
    "views/incidentes.html",
    "views/incidente_nuevo.html",
    "views/incidente.html",
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

SCALE = [{"id": "baja", "description": HOSTILE}, {"id": "media", "description": "Posible"}]
IMPACT = [{"id": "bajo", "description": "Limitado"}, {"id": "alto", "description": HOSTILE}]
MATRIX_ROWS = [
    {"likelihood": "baja", "levels": ["bajo", "bajo"]},
    {"likelihood": "media", "levels": ["bajo", "alto"]},
]
LEVELS = [{"id": "bajo", "description": "Asumible"}, {"id": "alto", "description": "Seguimiento"}]
ACTION = {
    "id": "A-001",
    "description": HOSTILE,
    "owner": HOSTILE,
    "due": "2026-12-31",
    "status": "Pendiente",
}
RISK = {
    "id": "R-001",
    "threat": HOSTILE,
    "targets": ["app", "facturacion"],
    "dimensions": ["C"],
    "likelihood": "media",
    "impact": "alto",
    "inherent": "alto",
    "residual": "bajo",
    "treatment": "Reducirlo con medidas",
    "owner": HOSTILE,
    "safeguards": ["op.acc.1"],
    "accepted_by": HOSTILE,
    "actions": [ACTION],
}
MEASURE = {
    "id": "op.pl.5",
    "name": HOSTILE,
    "state": "undecided",
    "reason": HOSTILE,
    "required": False,
    "required_by": None,
    "required_reinforcements": [{"id": "R1", "title": HOSTILE}],
    "groups": [
        {"options": [{"id": "R1", "title": HOSTILE}, {"id": "R2", "title": "Otro"}], "chosen": "R1"}
    ],
    "choice": "",
    "justification": HOSTILE,
    "absent_types": ["laptop"],
    "absent_type": "",
    "obligations": [{"id": "base", "title": "Parte general"}],
    "compensation": {"measures": HOSTILE, "replaces": ["base"], "justification": HOSTILE},
}
QUESTION = {
    "id": "organizacion",
    "name": "a~organizacion",
    "text": HOSTILE,
    "help": HOSTILE,
    "type": "text",
    "choices": [],
    "multiline": False,
    "people_list": False,
    "optional": False,
    "clause": {"reference": "RD 311/2022, anexo II, org.1.1", "text": HOSTILE},
    "condition_note": HOSTILE,
    "allow_na": True,
    "na_label": "No aplica",
    "na": False,
    "reason": HOSTILE,
    "value": HOSTILE,
    "rows": [],
}
LIST_ROWS = [
    {"n": 1, "value": HOSTILE, "filled": True, "removed": True},
    {"n": 2, "value": "", "filled": False, "removed": False},
]
RECORD_ROWS = [
    {
        "n": 1,
        "filled": True,
        "removed": False,
        "cells": [
            {"id": "tarea", "label": "Tarea", "name": "r~x~tarea", "value": HOSTILE, "long": False},
            {
                "id": "detalle",
                "label": "Detalle",
                "name": "r~x~detalle",
                "value": HOSTILE,
                "long": True,
            },
        ],
    }
]
DOCUMENT = {
    "name": "politica-seguridad",
    "title": HOSTILE,
    "state": "GENERATED",
    "state_label": "Generado, pendiente de aprobar",
    "tone": "warn",
    "reasons": [HOSTILE],
}
SIGNED = {"approved_by": HOSTILE, "approved_at": "2026-11-08 10:00"}
WIZARD_FLOW_EXTRA = {
    "riesgos-metodologia": {
        "adopted": False,
        "likelihood": SCALE,
        "impact": IMPACT,
        "risk_levels": LEVELS,
        "impact_ids": ["bajo", "alto"],
        "matrix_rows": MATRIX_ROWS,
        "values": {"name": HOSTILE, "reference": HOSTILE, "adaptation": HOSTILE},
    },
    "riesgos": {
        "adopted": True,
        "likelihood": SCALE,
        "impact": IMPACT,
        "risk_levels": LEVELS,
        "minimum_level": "bajo",
        "matrix_rows": MATRIX_ROWS,
        "impact_ids": ["bajo", "alto"],
        "risks": [RISK],
        "actions": [ACTION],
        "asset_choices": ASSET_CHOICES,
        "service_choices": [{"id": "facturacion", "label": HOSTILE}],
        "safeguard_choices": [{"id": "op.acc.1", "label": HOSTILE}],
        "levels": ["BAJO", "ALTO"],
        "treatments": [{"value": "mitigate", "label": "Reducirlo"}],
        "roles": [{"value": "system_owner", "label": "Responsable del sistema"}],
        "action_statuses": [{"value": "pending", "label": "Pendiente"}],
        "values": {"threat": HOSTILE},
    },
    "aprobacion-riesgos": {
        "risk_state": "NOT_APPROVED",
        "stale_reasons": [HOSTILE],
        "issues": [HOSTILE],
        "issues_more": 2,
        "risks_count": 1,
        "actions_count": 1,
        "approval": {**SIGNED, "valid_until": "2027-11-08"},
    },
    "aplicabilidad": {
        "blocked": None,
        "needs_preparing": False,
        "category": "BASICA",
        "pending": [
            MEASURE,
            {**MEASURE, "id": "op.acc.5", "state": "reinforcement", "choice": "applies"},
        ],
        "required": [
            {
                **MEASURE,
                "id": "org.1",
                "state": "decided",
                "required": True,
                "required_by": "la categoría",
                "choice": "applies",
            }
        ],
        "own": [{**MEASURE, "id": "op.acc.3", "state": "decided", "choice": "not_applicable"}],
        "values": {},
    },
    "aprobacion-aplicabilidad": {
        "declaration_state": "NOT_APPROVED",
        "stale_reasons": [HOSTILE],
        "issues": [HOSTILE],
        "issues_more": 3,
        "exclusions": [
            {"id": "op.acc.3", "name": HOSTILE, "asset_type": HOSTILE, "justification": HOSTILE}
        ],
        "declaration": {**SIGNED, "selected_count": 12},
    },
    "documentos-organizacion": {
        "block_intro": HOSTILE,
        "questions": [
            QUESTION,
            {**QUESTION, "id": "listado", "type": "list", "rows": LIST_ROWS, "na": True},
            {**QUESTION, "id": "tabla", "type": "records", "rows": RECORD_ROWS},
            {**QUESTION, "id": "eleccion", "choices": [HOSTILE, "sí"], "people_list": True},
        ],
        "people": [HOSTILE],
        "values": {"a~organizacion": HOSTILE},
    },
    "documentos-generar": {
        "scope_ok": True,
        "answers_started": True,
        "issue_groups": [{"slug": "documentos-organizacion", "label": HOSTILE, "items": [HOSTILE]}],
        "warnings": [HOSTILE],
        "documents": [DOCUMENT],
        "can_generate": True,
        "values": {},
    },
    "documentos-aprobar": {
        "documents": [
            {
                **DOCUMENT,
                "role_label": "la dirección",
                "signatory": HOSTILE,
                "by": HOSTILE,
                "can_approve": True,
                "approval": None,
                "error_key": "approved_by~politica-seguridad",
            },
            {
                **DOCUMENT,
                "role_label": "la dirección",
                "signatory": "Ana",
                "by": "ana",
                "can_approve": False,
                "approval": SIGNED,
                "error_key": "approved_by~normativa",
                "state": "APPROVED",
            },
        ],
        "viewing": {"name": "politica-seguridad", "title": "Política", "text": HOSTILE},
        "values": {},
    },
}

WIZARD_EXTRA |= WIZARD_FLOW_EXTRA

CHROME = {
    "product_name": "Producto",
    "identity_email": "ana@example.org",
    "nav_active": "estado",
    "page_title": "Página",
}
FINDING = {
    "id": "a" * 64,
    "measure_id": "op.pl.1",
    "classification": "NC_MENOR",
    "disposition": "OPEN",
    "text": HOSTILE,
    "criterion": HOSTILE,
    "source": HOSTILE,
    "classification_source": HOSTILE,
    "layer_label": "Juicio",
    "decidable": True,
    "closed": False,
    "decisions": [("dismissed", "Descartarlo"), ("resolved", "Resuelto")],
    "evidence_options": [{"id": "EV-20261108-abcd1234", "label": HOSTILE}],
}
INCIDENT_ROW = {
    "id": "INC-2026-001",
    "simulated": False,
    "broken": False,
    "readable": True,
    "status": "abierto",
    "closed": False,
    "severity": "Sin severidad",
    "detected_at": "2026-11-08 10:00 UTC",
    "title": "Phishing",
}
INCIDENT_FORM = {
    "asset_choices": ASSET_CHOICES,
    "levels": ["bajo", HOSTILE],
    "personal_data_options": [
        {"value": "yes", "label": "sí"},
        {"value": "no", "label": "no"},
        {"value": "unknown", "label": "sin determinar"},
    ],
    "action_kinds": [{"value": "emergency", "label": "emergencia"}],
    "review_results": [{"value": "changed", "label": "se cambiaron los eventos auditables"}],
}
VIEW_CONTEXT = {
    "views/estado.html": {
        "readiness": {"state": "NOT_READY", "summary": "Faltan evidencias"},
        "scope_state": "APPROVED",
        "categorization_state": "CURRENT",
        "category": "BASICA",
        "next_step_url": "/asistente/sistema",
        "next_step_label": "Seguir con el asistente",
        "phases": [
            {"key": "alcance", "label": "Alcance", "state": "done"},
            {"key": "riesgos", "label": "Riesgos", "state": "next"},
            {"key": "matriz", "label": "Matriz", "state": "todo"},
        ],
        "errors": [HOSTILE],
        "earlier_phase_pending": True,
        "blockers": [
            {"measure_id": "op.pl.1", "texts": ["Sin evidencia.", "Sin revisar."]},
            {"measure_id": None, "texts": [HOSTILE]},
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
        "wizard_pending": True,
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
        "uploaded": "EV-20261108-abcd1234",
    },
    "views/expediente.html": {
        "dossier_id": "20261108T100000000000Z-abcdef012345",
        "category": "BÁSICA",
        "dossier_state": "GENERATED",
        "generated_at": "2026-11-08 10:00",
        "declared": None,
        "transitions": [
            {"state": "SELF_ASSESSED", "actor": HOSTILE, "role": "Rol", "at": "2026-11-08 10:00"}
        ],
        "stale": True,
        "drift_lines": [HOSTILE],
        "integrity": [HOSTILE],
        "blocked": [HOSTILE],
        "incomplete": ["4. Riesgos"],
        "readiness_notice": HOSTILE,
        "action": "analyze",
        "next_text": "Toca el análisis.",
        "can_download": True,
        "self_assess_roles": {"system_administrator": "Administrador del sistema"},
        "form_errors": [HOSTILE],
        "errors": {"by": [HOSTILE]},
        "values": {"by": HOSTILE},
        "saved": "Hecho.",
        "sections": [
            {
                "title": "Alcance",
                "file": "alcance.md",
                "state": "GENERATED",
                "state_label": "Registrada",
                "generated_at": "2026-11-08 10:00",
                "fingerprint": "abcdef0123456789",
            }
        ],
    },
    "views/preauditoria.html": {
        "dossiers": ["20261108T100000000000Z-abcdef012345"],
        "dossier_id": "20261108T100000000000Z-abcdef012345",
        "requests": [
            {
                "kind_label": "Preparación",
                "state": "DONE",
                "state_label": "Hecha",
                "tone": "ok",
                "at": "2026-11-08 10:00",
                "by": HOSTILE,
                "message": HOSTILE,
            }
        ],
        "has_live_request": False,
        "can_consent": True,
        "prepared": {
            "key": "evidence/preaudits/x.json",
            "provider": "anthropic",
            "model": HOSTILE,
            "destination": HOSTILE,
            "recipient": HOSTILE,
            "shell": True,
            "pack_version": "v1",
            "prepared_at": "2026-11-08 10:00",
            "sha256": "ab" * 32,
            "intact": True,
            "measures": 3,
            "documents": 2,
            "evidence": 1,
        },
        "redaction_limit": HOSTILE,
        "shell_risk": HOSTILE,
        "generated_at": "2026-11-08 10:00",
        "layers": {"Determinista": "DONE", "Juicio": "NOT_RUN"},
        "judgment": [FINDING],
        "deterministic": [{"measure_id": "op.pl.1", "findings": [{**FINDING, "decidable": False}]}],
        "form_errors": [HOSTILE],
        "error_finding": FINDING["id"],
        "values": {"by": HOSTILE, "reason": HOSTILE},
        "saved": HOSTILE,
    },
    "views/incidentes.html": {
        "rows": [
            {
                "id": "INC-2026-001",
                "simulated": True,
                "broken": True,
                "readable": True,
                "status": "cerrado",
                "closed": True,
                "severity": HOSTILE,
                "detected_at": "2026-11-08 10:00 UTC",
                "title": HOSTILE,
            },
            {**INCIDENT_ROW, "id": "INC-2026-002"},
        ],
        "generated_at": "2026-11-08 10:00",
    },
    "views/incidente_nuevo.html": {
        **INCIDENT_FORM,
        "simulation_note": HOSTILE,
        "form_errors": [HOSTILE],
        "errors": {},
        "values": {"title": HOSTILE, "by": HOSTILE, "simulated": "1", "severity": "alto"},
        "selected_assets": ["lap-ana"],
    },
    "views/incidente.html": {
        **INCIDENT_FORM,
        "incident_id": "SIM-2026-001",
        "readable": True,
        "simulated": True,
        "simulation_note": HOSTILE,
        "title": HOSTILE,
        "incident_status": "closed",
        "status_label": "cerrado",
        "closed": True,
        "severity": HOSTILE,
        "detected_at": "2026-11-08 10:00 UTC",
        "personal_data": "no",
        "personal_data_label": "no",
        "assets": [HOSTILE],
        "source": {"system": HOSTILE, "alert_ids": [HOSTILE]},
        "procedure": {"name": "procedimiento-incidentes", "approved_at": "2026-11-01 10:00 UTC"},
        "summary": HOSTILE,
        "actions": [{"at": "2026-11-08 11:00 UTC", "kind": "emergencia", "description": HOSTILE}],
        "evidence_ids": ["EV-20261108-abcd1234"],
        "closure": {
            "at": "2026-11-08 12:00 UTC",
            "conclusion": HOSTILE,
            "legal_relevance": "sí",
            "legal_advice": HOSTILE,
            "preserved": ["EV-20261108-abcd1234"],
            "events_review": "sin cambios en los eventos auditables",
            "events_detail": HOSTILE,
        },
        "records": [
            {
                "seq": 1,
                "kind": "reporte inicial",
                "at": "2026-11-08 10:00 UTC",
                "evidence_id": None,
            },
            {
                "seq": 2,
                "kind": "reporte final",
                "at": "2026-11-08 12:00 UTC",
                "evidence_id": "EV-1",
            },
        ],
        "problems": [HOSTILE],
        "history_id": "EV-20261108-abcd1234",
        "history_missing": False,
        "can_write": True,
        "saved": HOSTILE,
        "op_exp_9": "op.exp.9",
        "op_exp_7": "op.exp.7",
        "generated_at": "2026-11-08 10:00",
        "form_errors": [HOSTILE],
        "errors": {},
        "values": {"by": HOSTILE, "action": HOSTILE, "summary": HOSTILE},
        "selected_assets": [],
    },
    "views/matriz_medida.html": {
        "measure_id": "op.pl.1",
        "title": HOSTILE,
        "applicability": "APPLICABLE",
        "assessment": "NOT_ASSESSED",
        "selected": True,
        "owner": {"name": HOSTILE, "role": "Dirección"},
        "every_days": 90,
        "obligations": [
            {
                "id": "op.pl.1.1",
                "title": "op.pl.1.1",
                "compensating": [HOSTILE],
                "clauses": [
                    {
                        "unit": "op.pl.1.1",
                        "label": "a)",
                        "text": HOSTILE,
                        "sources": [HOSTILE],
                    }
                ],
            }
        ],
        "links": [
            {
                "kind": "org_document",
                "target": "politica-seguridad",
                "label": HOSTILE,
                "covers": ["op.pl.1.1"],
                "counts": True,
            }
        ],
        "document_offers": [{"name": HOSTILE, "covers": ["op.pl.1.1"]}],
        "evidence_offers": [
            {
                "id": "EV-20261108-abcd1234",
                "asset_id": HOSTILE,
                "collected_at": "2026-11-08",
                "expired": True,
            }
        ],
        "units": ["op.pl.1.1"],
        "blockers": [HOSTILE],
        "last_review": {
            "by": HOSTILE,
            "role": "Dirección",
            "at": "2026-11-08 10:00",
            "valid_until": "2027-02-06 10:00",
            "conclusion": HOSTILE,
        },
        "owner_roles": [{"value": "management", "label": "Dirección"}],
        "review_roles": [{"value": "system_administrator", "label": "Administrador del sistema"}],
        "assessments": [{"value": "COMPLIANT", "label": "Favorable"}],
        "max_days": 365,
        "generated_at": "2026-11-08 10:00",
        "by_default": HOSTILE,
        "values": {"conclusion": HOSTILE},
        "form_errors": [HOSTILE],
        "uncovered": [HOSTILE],
        "saved": True,
    },
    "views/evidencias_nueva.html": {
        "assets": [HOSTILE, "lap-ana"],
        "max_megabytes": 5,
        "by_default": HOSTILE,
        "values": {"asset_id": HOSTILE, "by": HOSTILE, "collected_on": HOSTILE},
        "form_errors": [HOSTILE],
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
    assert f"Paso 5 de {len(STEPS)}" in html
    assert 'name="approved_by"' in html and 'value="ana@example.org"' not in html
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
