"""Renderiza las plantillas con el contexto de docs/ui/contrato-de-plantillas.md."""

import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ens_evidence.ui.pages import STATUS_TEXT, install_template_helpers
from ens_evidence.ui.views import check_history as ch
from ens_evidence.ui.views import conectores as con
from ens_evidence.ui.views import conectores_peticion as pet
from ens_evidence.ui.views import conectores_recogida as rec
from ens_evidence.ui.views import copias as cop
from ens_evidence.ui.views import inventory as inv
from ens_evidence.ui.views.agents import (
    AgentDetail,
    AgentRow,
    CheckRow,
    ContactRow,
    QueryRow,
)
from ens_evidence.ui.views.conectores_catalogo import Choice

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
    "views/herramientas.html",
    "views/monitorizacion.html",
    "views/herramientas_copias.html",
    "views/herramientas_claves.html",
    "views/herramientas_contrasenas.html",
    "views/herramientas_claves_custodios.html",
    "views/ayuda.html",
    "agents.html",
    "agents_detail.html",
    "views/inventario.html",
    "views/inventario_activo.html",
    "views/inventario_form.html",
    "views/copias.html",
    "views/conectores.html",
    "views/conectores_form.html",
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
    "valoraciones": {"elements": ELEMENTS, "missing_valuation_roles": {}},
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
    "locked": False,
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
            {**QUESTION, "id": "fija", "choices": ["sí"], "locked": True},
        ],
        "management_option": [{"value": "management", "label": HOSTILE}],
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
                "role_missing": False,
                "signatory": HOSTILE,
                "by": HOSTILE,
                "can_approve": True,
                "approval": None,
                "error_key": "approved_by~politica-seguridad",
            },
            {
                **DOCUMENT,
                "role_label": "la dirección",
                "role_missing": False,
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
    "missing_role": None,
    "needs_init": False,
    "global_notices": [{"kind": "warn", "title": HOSTILE, "how": HOSTILE}],
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
_AGENT_CHECK = CheckRow("HOST-002", "PASS", HOSTILE, "AGENT_OK", ("e" * 8,))
_AGENT_CONTACT = ContactRow(2, "2026-11-08 10:00 UTC", "ev-1")
_AGENT_ROW = AgentRow(
    HOSTILE, "admitted", "Admitido", 2, "2026-11-01 09:00 UTC", _AGENT_CONTACT, _AGENT_CHECK
)
_AGENT_BARE = AgentRow("pc-2", "never", "Sin alta", None, "—", None, None)

EDITOR = SimpleNamespace(edit=True)
READER = SimpleNamespace(edit=False)
_CHOICES = (inv.Choice("laptop", "Portátil"), inv.Choice("server", "Servidor"))
_EVIDENCE_FAIL = inv.EvidenceSummary(
    "FAIL",
    "FAIL",
    False,
    "completa",
    (),
    HOSTILE,
    "2026-11-08 10:00 UTC",
    2,
    ("ev-1",),
    f"Último resultado de 2 checks de {HOSTILE} el 2026-11-08 10:00 UTC",
)
_EVIDENCE_PARTIAL = inv.EvidenceSummary(
    "PASS",
    "PASS parcial",
    False,
    "parcial",
    (HOSTILE,),
    "osquery",
    "2026-11-08 10:00 UTC",
    1,
    ("ev-2",),
    "Último resultado de 1 checks de osquery el 2026-11-08 10:00 UTC",
)
_EVIDENCE_PASS = inv.EvidenceSummary(
    "PASS",
    "PASS",
    True,
    "completa",
    (),
    "osquery",
    "2026-11-08 10:00 UTC",
    1,
    ("ev-3",),
    "Último resultado de 1 checks de osquery el 2026-11-08 10:00 UTC",
)


def _asset_row(asset_id, evidence, scope="included", **extra):
    return inv.AssetRow(
        id=asset_id,
        href=f"/inventario/activos/{asset_id}",
        type="laptop",
        type_label="Portátil",
        deployment="deployed",
        deployment_label="Desplegado",
        scope=scope,
        scope_label="Excluido" if scope == "excluded" else "En el alcance",
        justification=HOSTILE if scope == "excluded" else None,
        summary=HOSTILE,
        evidence=evidence,
        agent_href="/agentes/lap-ana",
        **extra,
    )


_ASSET_ROWS = (
    _asset_row(HOSTILE, _EVIDENCE_FAIL),
    _asset_row("lap-ana", _EVIDENCE_PARTIAL),
    _asset_row("lap-bea", _EVIDENCE_PASS),
    _asset_row("lap-sin", None, scope="excluded"),
)
_RECONCILE = inv.Reconciliation(
    rows=(
        inv.ReconcileRow(
            "not_inventoried",
            "Reporta y no está inventariado",
            HOSTILE,
            HOSTILE,
            None,
            False,
            HOSTILE,
            "/inventario/alta?tipo=laptop&id=x",
            None,
        ),
        inv.ReconcileRow(
            "hostname_mismatch",
            "El nombre del equipo no coincide",
            "lap-ana",
            "pc-a",
            HOSTILE,
            True,
            None,
            None,
            "/inventario/activos/lap-ana",
        ),
        inv.ReconcileRow(
            "not_enrolled",
            "Desplegado y sin alta en agentes",
            "srv-1",
            None,
            None,
            False,
            None,
            None,
            "/inventario/activos/srv-1",
        ),
    ),
    notes=(HOSTILE,),
)
_FILTERS = inv.Filters(tipo="laptop")
_OPTIONS = inv.FilterOptions(
    tipo=_CHOICES,
    despliegue=(inv.Choice("deployed", "Desplegado"),),
    alcance=(inv.Choice("included", "En el alcance"),),
    estado=(inv.Choice("FAIL", "Falla (FAIL)"), inv.Choice(inv.NO_EVIDENCE, "Sin evidencia")),
)
_PAGE = inv.InventoryPage(
    rows=_ASSET_ROWS,
    total=7,
    filters=_FILTERS,
    filter_options=_OPTIONS,
    file_errors=(HOSTILE,),
    integrity_warnings=(HOSTILE,),
    writes_blocked=True,
    reconciliation=_RECONCILE,
    history_notes=(HOSTILE,),
    generated_at="2026-11-08 10:00 UTC",
)
_WARNING = inv.ApprovalWarning(HOSTILE, "2026-11-01 09:00 UTC")
_FORM_FIELDS = (
    inv.FormField("asset_os", "Sistema operativo", "text", True),
    inv.FormField("asset_hostname", "Nombre del equipo", "text", False),
    inv.FormField("asset_encrypted", "Cifrado", "bool", True),
)
_FORM_VALUES = {
    "asset_id": HOSTILE,
    "asset_os": HOSTILE,
    "asset_hostname": HOSTILE,
    "justification": HOSTILE,
    "scope": "excluded",
    "acepto_revocacion": "1",
}


def _asset_form(mode, **extra):
    new = mode == "nuevo"
    base = {
        "mode": mode,
        "action": "/inventario/alta" if new else "/inventario/activos/x/editar",
        "cancel_href": "/inventario" if new else "/inventario/activos/x",
        "asset_id": "" if new else "x",
        "type": "laptop",
        "type_label": "Portátil",
        "fields": _FORM_FIELDS,
        "asset_types": _CHOICES,
        "deployment": "planned",
        "scope": "excluded",
        "justification": HOSTILE,
        "approval_warning": _WARNING,
    }
    return inv.AssetForm(**{**base, **extra})


_BLOCKED = {
    "writes_blocked": False,
    "file_errors": [HOSTILE],
    "integrity_warnings": [HOSTILE],
    "history_notes": [HOSTILE],
    "generated_at": "2026-11-08 10:00 UTC",
}
_DETAIL = inv.AssetDetail(
    row=_asset_row("x", _EVIDENCE_PARTIAL),
    fields=(("Sistema operativo", HOSTILE), ("Nombre del equipo", HOSTILE)),
    services=(inv.RefRow("service", "svc-1", HOSTILE),),
    dependencies=(inv.RefRow("dependency", "dep-1", HOSTILE),),
    results=(
        ch.ConnectorResults(
            HOSTILE,
            "2026-11-08 10:00 UTC",
            False,
            (
                ch.CheckRow("HOST-001", "PASS", "SUCCESS", None, ("ev-2",)),
                ch.CheckRow("HOST-002", "FAIL", "PERMISSION_DENIED", HOSTILE, ()),
            ),
        ),
    ),
    referenced=True,
    references_known=True,
    removal_blockers=(HOSTILE,),
    removable=False,
    connectors=(inv.ConnectorLink(HOSTILE, HOSTILE, "/conectores/github"),),
)
_DETAIL_BARE = inv.AssetDetail(
    row=_asset_row("y", None),
    fields=(),
    services=(),
    dependencies=(),
    results=(),
    referenced=False,
    references_known=False,
    removal_blockers=(),
    removable=True,
)
_REMOVAL = inv.Removal(
    "x",
    "/inventario/activos/x/baja",
    "/inventario/activos/x",
    (),
    (inv.RefRow("service", "svc-1", HOSTILE),),
    _WARNING,
)
_ACTIVO = {
    "form": None,
    "removal": None,
    "values": {},
    "errors": {},
    "form_errors": [],
    "saved": None,
    "revoked_notice": None,
    **_BLOCKED,
}

_COPIAS_SUMMARY = inv.EvidenceSummary(
    status="PASS",
    label="Sin fallos",
    favorable=False,
    coverage="parcial",
    partial_reasons=(HOSTILE,),
    connector="backups",
    finished_at="2026-11-08 10:00 UTC",
    checks=2,
    evidence_ids=("ev-1",),
    sentence=HOSTILE,
)
_COPIAS_REPOSITORY = cop.RepositoryBlock(
    id=HOSTILE,
    asset_id=HOSTILE,
    asset_href=None,
    asset_note=HOSTILE,
    frequency=HOSTILE,
    thresholds=cop.Thresholds(24, 90, 7),
    last_snapshot=cop.LastSnapshot("2026-11-08 08:00 UTC", 2),
    checked_at="2026-11-08 10:00 UTC",
    evidence=_COPIAS_SUMMARY,
    results=(
        ch.ConnectorResults(
            HOSTILE,
            "2026-11-08 10:00 UTC",
            False,
            (
                ch.CheckRow("backups.backup_001_recent", "FAIL", "SUCCESS", HOSTILE, (HOSTILE,)),
                ch.CheckRow("backups.backup_005_restore_test", "UNKNOWN", "NOT_FOUND", None, ()),
                ch.CheckRow(HOSTILE, "PASS", "SUCCESS", None, ()),
            ),
        ),
    ),
    check_labels=cop.CHECK_LABELS,
    problem=True,
    restore_test=cop.RestoreDeclaration(
        HOSTILE, "2026-11-01 10:00 UTC", HOSTILE, HOSTILE, "2027-02-01 11:00 UTC", False, True
    ),
    guide=cop.Guide(command=HOSTILE, yaml=HOSTILE, steps=(HOSTILE,)),
)
_COPIAS_REPOSITORY_BARE = cop.RepositoryBlock(
    id="caja",
    asset_id="bk-diario",
    asset_href="/inventario/activos/bk-diario",
    asset_note=None,
    frequency=None,
    thresholds=cop.Thresholds(24, 90, 7),
    last_snapshot=None,
    checked_at=None,
    evidence=None,
    results=(),
    check_labels=cop.CHECK_LABELS,
    problem=False,
    restore_test=None,
    guide=cop.Guide(command="ens-evidence x", yaml="a: 1\n", steps=("Paso",)),
)
_COPIAS_BOX = cop.BoxBackup(
    last=cop.BoxRun(HOSTILE, "started", HOSTILE, HOSTILE, None, HOSTILE),
    interrupted=(cop.InterruptedRun(HOSTILE, HOSTILE),),
    unreadable_count=2,
    note=HOSTILE,
)
_COPIAS_BOX_EMPTY = cop.BoxBackup(last=None, interrupted=(), unreadable_count=0, note="nota")


def _copias_page(**fields):
    base = {
        "configured": True,
        "config_error": None,
        "backrest_href": "https://copias.example.org",
        "wizard_href": "/herramientas/copias",
        "runbook_href": "/ayuda/kit-copias",
        "history_notes": (HOSTILE,),
        "repositories": (_COPIAS_REPOSITORY, _COPIAS_REPOSITORY_BARE),
        "box": _COPIAS_BOX,
        "history": (
            cop.HistoryRow(HOSTILE, HOSTILE, HOSTILE, "sí", HOSTILE, HOSTILE, HOSTILE),
            cop.HistoryRow("RT-2", "caja", "x", "no", "Ana", "r", "y"),
            cop.HistoryRow("RT-3", "caja", "x", "sin verificar", "Ana", "r", "y"),
        ),
        "unreadable_tests": 2,
        "generated_at": "2026-11-08 12:00 UTC",
    }
    return {"page": cop.CopiasPage(**(base | fields)), "permissions": EDITOR}


_CON_ENV = con.CredentialInfo("env", ("ENS_GITHUB_TOKEN",), "deploy/.env de la caja")
_CON_PREFIX = con.CredentialInfo(
    "env_prefix", ("ENS_BACKUPS_<ID>_PASSWORD",), "deploy/.env de la caja"
)
_CON_SETTINGS = con.CredentialInfo("settings", ("ENS_RESTIC_PASSWORD",), "ajustes backup.*")
_CON_NONE = con.CredentialInfo("none", (), "")


def _con_config(status="configurado", **fields):
    labels = {
        "sin-configurar": "Sin configurar",
        "configurado": "Configurado",
        "invalida": "Configuración no válida",
        "ilegible": "Configuración ilegible",
    }
    base = {
        "status": status,
        "label": labels[status],
        "problem": None,
        "asset_id": None,
        "asset_href": None,
        "asset_note": None,
        "shown": (),
    }
    return con.ConfigState(**(base | fields))


_CON_CONFIGURED = _con_config(
    asset_id=HOSTILE,
    asset_href=None,
    asset_note=HOSTILE,
    shown=((HOSTILE, HOSTILE),),
)


def _con_last(**fields):
    base = {
        "evidence_id": HOSTILE,
        "collected_at": "2026-11-08 10:00 UTC",
        "status": "SUCCESS",
        "status_label": "correcta",
        "unreadable": False,
        "partial": False,
        "failure": None,
        "sections": (rec.SectionRow(HOSTILE, "SUCCESS"),),
    }
    return rec.LastCollection(**(base | fields))


def _con_block(name="github", **fields):
    base = {
        "name": name,
        "label": HOSTILE,
        "href": f"/conectores/{name}",
        "editable": True,
        "can_configure": True,
        "config": _CON_CONFIGURED,
        "credential": _CON_ENV,
        "doc_href": f"/ayuda/conector-{name}",
        "kit_href": None,
        "status_href": None,
        "last": None,
        "evidence": None,
        "results": (),
        "history_notes": (HOSTILE,),
    }
    return con.ConnectorBlock(**(base | fields))


_CON_EVIDENCE = con.ConnectorEvidence(
    **vars(dataclasses.replace(_COPIAS_SUMMARY, connector="github", sentence=HOSTILE))
)
_CON_RESULTS = (
    ch.ConnectorResults(
        HOSTILE,
        "2026-11-08 10:00 UTC",
        False,
        (
            ch.CheckRow("GH-001", "PASS", "SUCCESS", None, ()),
            ch.CheckRow(HOSTILE, "FAIL", "PERMISSION_DENIED", HOSTILE, (HOSTILE,)),
        ),
    ),
)
_CON_BACKUPS = _con_block(
    "backups",
    editable=False,
    can_configure=False,
    credential=_CON_PREFIX,
    kit_href="/herramientas/copias",
    status_href="/copias",
    last=_con_last(),
    repositories=(
        rec.RepositoryState(HOSTILE, "SUCCESS", "correcta", None),
        rec.RepositoryState(
            "otro",
            "ERROR",
            "con error",
            rec.Failure("credential_missing", "credencial ausente", HOSTILE),
        ),
        rec.RepositoryState("tercero", None, "sin estado", None),
    ),
)


def _con_request(state="DONE", label="Terminada", tone="neutral", text="", **fields):
    base = {
        "state": state,
        "label": label,
        "tone": tone,
        "text": text,
        "requested_at": "2026-11-08 11:59 UTC",
        "alive": state in ("PENDING", "RUNNING"),
        "result": None,
    }
    return pet.RequestView(**(base | fields))


_CON_RESULT = pet.ResultView(HOSTILE, HOSTILE, HOSTILE, HOSTILE, evidence_invalid=False)
_CON_REQUESTS = {
    "pendiente": _con_request("PENDING", "Pendiente", "warn", pet.PENDING_TEXT),
    "en-curso": _con_request("RUNNING", "En curso", "warn", pet.RUNNING_TEXT),
    "caducada": _con_request("EXPIRED", "Caducada", "neutral", pet.EXPIRED_TEXT),
    "hecha": _con_request(result=_CON_RESULT),
    "recogida-con-error": _con_request(
        label="Terminada con error de recogida", tone="bad", result=_CON_RESULT
    ),
    "checks-no-registrados": _con_request(
        label="Terminada; comprobaciones no registradas", tone="warn", result=_CON_RESULT
    ),
    "no-se-pudo": _con_request(label="No se pudo hacer: " + HOSTILE, tone="bad"),
    "id-invalido": _con_request(
        result=pet.ResultView(None, None, "sin comprobaciones", None, evidence_invalid=True)
    ),
}
_CON_REQUEST_BLOCKS = tuple(
    _con_block(
        "github",
        request=request,
        can_collect=not request.alive,
        collect_action="/conectores/github/recoger",
    )
    for request in _CON_REQUESTS.values()
)
_CON_BLOCKS = (
    _con_block("github", config=_con_config("sin-configurar")),
    _con_block("cloudflare", last=None),
    _con_block("google-workspace", config=_con_config("invalida", problem=HOSTILE)),
    _con_block("keystore", config=_con_config("ilegible", problem=HOSTILE), credential=_CON_NONE),
    _con_block(
        "passwords",
        last=_con_last(
            status="ERROR",
            status_label="con error",
            failure=rec.Failure("credential_missing", "credencial ausente", HOSTILE),
            sections=(),
        ),
    ),
    _con_block(
        "restic",
        credential=_CON_SETTINGS,
        last=_con_last(
            partial=True,
            sections=(
                rec.SectionRow(HOSTILE, "SUCCESS"),
                rec.SectionRow("repos", "PERMISSION_DENIED"),
            ),
            sections_unreadable=True,
        ),
        evidence=_CON_EVIDENCE,
        results=_CON_RESULTS,
    ),
    _con_block(
        "scheduler",
        last=_con_last(status=None, status_label="estado no legible", unreadable=True, sections=()),
    ),
    _CON_BACKUPS,
    _con_block(
        "otro",
        editable=False,
        can_configure=False,
        credential=None,
        doc_href=None,
    ),
)


def _con_page(blocks=_CON_BLOCKS, **fields):
    page = con.ConectoresPage(
        blocks=blocks,
        manifest_notes=(HOSTILE,),
        inventory_notes=(HOSTILE,),
        generated_at="2026-11-08 12:00 UTC",
    )
    return {
        "page": dataclasses.replace(page, **fields),
        "saved": None,
        "saved_text": HOSTILE,
        "requested_text": HOSTILE,
        "form_errors": [HOSTILE],
        "permissions": EDITOR,
    }


_CON_FIELDS = (
    con.FormField("asset_id", "Activo", "asset", True, HOSTILE, (Choice(HOSTILE, HOSTILE),)),
    con.FormField("organization", HOSTILE, "text", True, HOSTILE, ()),
    con.FormField("max_key_age_days", HOSTILE, "int", False, None, ()),
    con.FormField("token_kind", HOSTILE, "choice", False, None, (Choice("a", HOSTILE),)),
    con.FormField("domains", HOSTILE, "lines", False, None, ()),
)


def _con_form(**fields):
    base = {
        "name": "github",
        "label": HOSTILE,
        "action": "/conectores/github",
        "cancel_href": "/conectores",
        "fields": _CON_FIELDS,
        "asset_choices": (Choice(HOSTILE, HOSTILE),),
        "asset_type_label": HOSTILE,
        "preserved": ("public_routes",),
        "needs_confirmation": False,
        "writes_blocked": False,
        "blocked_notice": None,
        "credential": _CON_ENV,
        "doc_href": "/ayuda/conector-github",
    }
    return con.ConnectorForm(**(base | fields))


def _con_form_context(form=None, block=None, **extra):
    return {
        "form": form or _con_form(),
        "block": block or _con_block(),
        "values": {f.name: HOSTILE for f in _CON_FIELDS},
        "errors": {},
        "form_errors": [],
        "permissions": EDITOR,
    } | extra


_CON_FIELD_ERRORS = {f.name: [HOSTILE] for f in _CON_FIELDS} | {
    "confirmar_sobrescritura": [HOSTILE]
}


VIEW_CONTEXT = {
    "agents.html": {"rows": [_AGENT_ROW, _AGENT_BARE], "problems": [HOSTILE], "notes": [HOSTILE]},
    "agents_detail.html": {
        "detail": AgentDetail(
            _AGENT_ROW,
            [QueryRow("q1", "2026-11-08 10:00 UTC", 3, "ev-2"), QueryRow("q2", None, None, None)],
            [_AGENT_CHECK, CheckRow("HOST-001", "FAIL", None, None, ())],
            [_AGENT_CONTACT],
        ),
        "problems": [HOSTILE],
    },
    "views/copias.html": [
        _copias_page(),
        _copias_page(backrest_href=None, repositories=(_COPIAS_REPOSITORY_BARE,)),
        _copias_page(
            configured=False,
            repositories=(),
            box=_COPIAS_BOX_EMPTY,
            history=(),
            unreadable_tests=0,
            history_notes=(),
        ),
        _copias_page(
            config_error=HOSTILE,
            repositories=(),
            box=_COPIAS_BOX_EMPTY,
            history=(),
            unreadable_tests=0,
        ),
        _copias_page(
            repositories=(dataclasses.replace(_COPIAS_REPOSITORY_BARE, tests_unattributable=True),)
        ),
    ],
    "views/conectores.html": [
        _con_page(),
        _con_page(blocks=(), manifest_notes=(), inventory_notes=())
        | {"saved_text": None, "requested_text": None, "form_errors": []},
        _con_page(blocks=_CON_REQUEST_BLOCKS),
        _con_page() | {"permissions": READER},
    ],
    "views/conectores_form.html": [
        _con_form_context(),
        _con_form_context(
            form=_con_form(needs_confirmation=True, preserved=()),
            block=_con_block(config=_con_config("invalida", problem=HOSTILE)),
            errors=_CON_FIELD_ERRORS,
            form_errors=[HOSTILE],
        ),
        _con_form_context(
            form=_con_form(writes_blocked=True, blocked_notice=HOSTILE, asset_choices=()),
        ),
        _con_form_context(
            form=_con_form(
                fields=(con.FormField("asset_id", "Activo", "asset", True, None, ()),),
            ),
            values={},
        ),
        _con_form_context(block=_con_block(can_configure=False), permissions=READER),
        _con_form_context(form=None, block=_CON_BACKUPS),
        *(_con_form_context(block=block) for block in _CON_REQUEST_BLOCKS),
        _con_form_context(
            form=None,
            block=_con_block("otro", editable=False, can_configure=False, credential=None),
        ),
    ],
    "views/inventario.html": [
        {
            "page": _PAGE,
            "rows": _PAGE.rows,
            "reconciliation": _RECONCILE,
            "saved": HOSTILE,
            "revoked_notice": HOSTILE,
            "permissions": EDITOR,
        },
        {
            "page": inv.InventoryPage(
                rows=(),
                total=0,
                filters=inv.Filters(),
                filter_options=_OPTIONS,
                file_errors=(),
                integrity_warnings=(),
                writes_blocked=False,
                reconciliation=inv.Reconciliation(),
                history_notes=(),
                generated_at="2026-11-08 10:00 UTC",
            ),
            "rows": (),
            "reconciliation": inv.Reconciliation(),
            "saved": None,
            "revoked_notice": None,
            "permissions": READER,
        },
        {
            "page": _PAGE,
            "rows": _PAGE.rows,
            "reconciliation": _RECONCILE,
            "saved": None,
            "revoked_notice": None,
        },
    ],
    "views/inventario_activo.html": [
        {**_ACTIVO, "detail": _DETAIL, "permissions": EDITOR},
        {**_ACTIVO, "detail": _DETAIL_BARE, "permissions": READER},
        {
            **_ACTIVO,
            "detail": _DETAIL,
            "permissions": EDITOR,
            "form": _asset_form("editar"),
            "values": _FORM_VALUES,
            "form_errors": [HOSTILE],
        },
        {
            **_ACTIVO,
            "detail": _DETAIL,
            "permissions": EDITOR,
            "removal": _REMOVAL,
            "values": _FORM_VALUES,
        },
        {
            **_ACTIVO,
            "detail": _DETAIL,
            "permissions": EDITOR,
            "removal": inv.Removal(
                "x",
                "/inventario/activos/x/baja",
                "/inventario/activos/x",
                (HOSTILE,),
                (inv.RefRow("dependency", "dep-1", HOSTILE),),
                None,
            ),
            "saved": HOSTILE,
            "revoked_notice": HOSTILE,
        },
    ],
    "views/inventario_form.html": [
        {
            "form": _asset_form("nuevo"),
            "values": _FORM_VALUES,
            "errors": {},
            "form_errors": [],
            "permissions": EDITOR,
            **_BLOCKED,
        },
        {
            "form": _asset_form(
                "nuevo",
                type=None,
                type_label=None,
                fields=(),
                approval_warning=None,
                scope="included",
            ),
            "values": {},
            "errors": {},
            "form_errors": [HOSTILE],
            "permissions": EDITOR,
            **_BLOCKED,
        },
        {
            "form": _asset_form("nuevo"),
            "values": _FORM_VALUES,
            "errors": {},
            "form_errors": [],
            **_BLOCKED,
            "writes_blocked": True,
        },
    ],
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
        "kit_offer": {
            "name": "copias",
            "on_box": True,
            "tool_name": HOSTILE,
            "cta": "Añadir las copias",
            "url": "/herramientas/copias",
            "covers": HOSTILE,
            "not_covers": HOSTILE,
            "covers_clauses": [{"id": "mp.info.6.9", "text": HOSTILE}],
            "partial_clauses": [{"id": "mp.info.6.1", "text": "Texto oficial"}],
            "not_covered_clauses": [{"id": "mp.info.6.2", "text": None}],
            "ram": {
                "mib": 13,
                "measured_on": "2026-11-10",
                "image": HOSTILE,
                "conditions": HOSTILE,
            },
        },
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
    "views/monitorizacion.html": {
        "snapshot": object(),
        "stale": False,
        "generated_at": "2026-11-08 10:00 UTC",
        "interval_minutes": 15,
        "groups": [
            {
                "key": "vps",
                "title": "Servidor (VPS)",
                "rows": [
                    {
                        "id": "vps:disk",
                        "title": HOSTILE,
                        "tone": "bad",
                        "label": "Fallo",
                        "detail": HOSTILE,
                        "since": "2026-11-08 07:00 UTC",
                        "runbook_url": "/ayuda/monitorizacion",
                        "spark": {
                            "bars": [{"x": 0, "tone": "bad"}],
                            "width": 2,
                            "height": 16,
                            "summary": HOSTILE,
                        },
                        "observed_at": "—",
                    }
                ],
            }
        ],
        "mail_configured": True,
        "mail_last_attempt": "—",
        "mail_last_failed": True,
        "mail_last_error": HOSTILE,
        "runbook_url": "/ayuda/monitorizacion",
    },
    "views/herramientas.html": {
        "tools": [
            {
                "id": "copias",
                "on_box": True,
                "name": HOSTILE,
                "purpose": HOSTILE,
                "state": {
                    "key": "down",
                    "tone": "bad",
                    "label": "Caída",
                    "checked_at": "2026-11-08 10:00 UTC",
                },
                "measures": ["mp.info.6"],
                "measures_note": HOSTILE,
                "hints": {"down": HOSTILE},
                "open_url": "https://copias.example.org",
                "status_url": "/copias",
                "wizard_url": "/herramientas/copias",
                "cta": "Añadir las copias",
                "help_url": "/ayuda/kit-copias",
                "help_label": HOSTILE,
                "ram": {
                    "mib": 13,
                    "measured_on": "2026-11-10",
                    "image": HOSTILE,
                    "conditions": HOSTILE,
                },
            },
            {
                "id": "claves",
                "on_box": True,
                "name": "Claves y secretos",
                "purpose": "x",
                "state": {
                    "key": "nodata",
                    "tone": "neutral",
                    "label": "Sin datos",
                    "checked_at": "—",
                },
                "measures": ["op.exp.10"],
                "measures_note": "",
                "hints": {},
                "open_url": None,
                "status_url": None,
                "wizard_url": "/herramientas/claves",
                "cta": "Añadir las claves",
                "help_url": "/ayuda/kit-claves",
                "help_label": "x",
                "ram": None,
            },
        ],
    },
    "views/herramientas_claves.html": {
        "values": {"asset_id": HOSTILE, "key_shares": HOSTILE},
        "errors": {"asset_id": [HOSTILE], "key_threshold": [HOSTILE]},
        "form_errors": [HOSTILE],
        "application_assets": [HOSTILE, "app-facturas"],
        "enable_command": "deploy/box.sh kit enable claves <paquete>",
        "init_command": "deploy/box.sh kit claves init",
    },
    "views/herramientas_contrasenas.html": {
        "values": {"asset_id": HOSTILE, "inactive_days": HOSTILE, "host": HOSTILE},
        "errors": {"asset_id": [HOSTILE], "inactive_days": [HOSTILE], "host": [HOSTILE]},
        "form_errors": [HOSTILE],
        "application_assets": [HOSTILE, "app-facturas"],
        "enable_command": "deploy/box.sh kit enable contrasenas <paquete>",
        "host_setting": "ENS_CONTRASENAS_HOST",
        "setup_command": "deploy/box.sh kit contrasenas alta",
        "close_command": "deploy/box.sh kit contrasenas cerrar-alta",
    },
    "views/herramientas_claves_custodios.html": {
        "values": {"asset_id": HOSTILE, "key_shares": HOSTILE, "reviewer": HOSTILE},
        "errors": {"asset_id": [HOSTILE], "reviewer": [HOSTILE], "custodian_name_0": [HOSTILE]},
        "form_errors": [HOSTILE],
        "application_assets": [HOSTILE, "app-facturas"],
        "rows": [0, 1],
        "review_roles": [{"value": "security_officer", "label": HOSTILE}],
        "current": {
            "custodians": [{"name": HOSTILE, "since": "2026-11-01 00:00 UTC"}],
            "key_shares": 5,
            "key_threshold": 3,
            "reviewer": HOSTILE,
            "role": HOSTILE,
            "reviewed_at": "2026-11-07 00:00 UTC",
            "valid_until": "2027-11-07 00:00 UTC",
        },
        "unreadable_count": 1,
        "mismatches": [HOSTILE],
        "saved": True,
    },
    "views/ayuda.html": {
        "title": HOSTILE,
        "blocks": [
            {"kind": "h", "level": 2, "inline": [{"code": False, "text": HOSTILE}]},
            {"kind": "p", "inline": [{"code": True, "text": HOSTILE}]},
            {"kind": "pre", "text": HOSTILE},
            {"kind": "ul", "start": 1, "items": [[{"code": False, "text": HOSTILE}]]},
            {"kind": "ol", "start": 2, "items": [[{"code": False, "text": HOSTILE}]]},
        ],
    },
    "views/herramientas_copias.html": {
        "values": {"id": HOSTILE, "endpoint": HOSTILE, "host_paths": HOSTILE, "volumes": ["data"]},
        "errors": {"id": [HOSTILE], "write_secret_access_key": [HOSTILE]},
        "form_errors": [HOSTILE],
        "notices": [HOSTILE],
        "backup_assets": [HOSTILE, "copias-caja"],
        "volumes": [{"value": "data", "label": "Datos"}],
        "variants": [{"value": "caja", "label": "En la caja"}],
        "default_read_percent": 5,
        "enable_command": "deploy/box.sh kit enable copias <fichero>",
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
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        autoescape=jinja2.select_autoescape(["html"], default=True),
        undefined=jinja2.StrictUndefined,
    )
    return install_template_helpers(environment)


def _cases():
    for name in VIEW_TEMPLATES:
        variants = VIEW_CONTEXT[name]
        for variant in variants if isinstance(variants, list) else [variants]:
            yield name, {**CHROME, **variant}
    for step, name in WIZARD_TEMPLATES.items():
        for hx in (False, True):
            yield name, {**CHROME, **_wizard(step, hx_request=hx, **WIZARD_EXTRA[step])}
    for status in (401, 403, 409, 500):
        yield "error.html", {**CHROME, "status": status, "message": None}
    yield "error.html", {**CHROME, "status": 403, "message": HOSTILE}


def _with_errors(name, context):
    if name.startswith("views/inventario"):
        bad = {
            k: [HOSTILE]
            for k in (
                "asset_id",
                "asset_os",
                "asset_hostname",
                "asset_encrypted",
                "asset_deployment",
                "asset_type",
                "scope",
                "justification",
                "acepto_revocacion",
                "confirmar",
            )
        }
        return {**context, "errors": bad, "form_errors": [HOSTILE]}
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


def test_las_macros_usan_el_mismo_texto_de_estado_de_check_que_las_vistas():
    estados = _environment().get_template("_macros.html").make_module({}).ESTADOS
    assert all(estados[code] == pair for code, pair in STATUS_TEXT.items())


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


def _render(name, context):
    return _environment().get_template(name).render(**{**CHROME, **context})


def test_inventory_never_shows_no_evidence_favourably():
    html = _render("views/inventario.html", VIEW_CONTEXT["views/inventario.html"][0])
    row = html.split(">lap-sin</a>")[1].split("</tr>")[0]
    assert "sin evidencia" in row and "tone-ok" not in row
    partial = html.split("PASS parcial")[0].rsplit("<tr>", 1)[1]
    assert "tone-warn" in partial and "tone-ok" not in partial
    assert "Cobertura parcial" in html and "Nombre distinto" in html


def test_inventory_hides_write_actions_without_edit_or_when_blocked():
    for context in VIEW_CONTEXT["views/inventario.html"][1:]:
        html = _render("views/inventario.html", context)
        assert "Añadir un activo" not in html and 'method="post"' not in html
    blocked = _render("views/inventario.html", VIEW_CONTEXT["views/inventario.html"][0])
    assert "Añadir un activo" not in blocked and "Añadir al inventario" not in blocked
    reader = _render(
        "views/inventario_activo.html", VIEW_CONTEXT["views/inventario_activo.html"][1]
    )
    assert "Dar de baja" not in reader.split("Solo lectura")[0] and "editar=1" not in reader


def test_referenced_asset_removal_explains_and_offers_no_form():
    html = _render("views/inventario_activo.html", VIEW_CONTEXT["views/inventario_activo.html"][4])
    assert "No se puede dar de baja todavía" in html and 'name="confirmar"' not in html
    assert "/asistente/dependencias" in html


def test_revocation_warning_carries_the_acceptance_checkbox():
    html = _render("views/inventario_form.html", VIEW_CONTEXT["views/inventario_form.html"][0])
    assert 'name="acepto_revocacion"' in html and "revoca la aprobación" in html
    assert 'name="asset_hostname"' in html and 'value="excluded" checked' in html
    clean = _render("views/inventario_form.html", VIEW_CONTEXT["views/inventario_form.html"][1])
    assert 'name="acepto_revocacion"' not in clean


def test_inventory_pages_carry_the_no_conformity_note():
    for name in ("views/inventario.html", "views/inventario_activo.html"):
        context = VIEW_CONTEXT[name][0]
        assert "Esta vista no declara conformidad." in _render(name, context)


def test_copias_sin_configurar_no_es_favorable_y_lleva_al_asistente():
    html = _render("views/copias.html", VIEW_CONTEXT["views/copias.html"][2])
    assert "No hay copias configuradas" in html
    assert 'href="/herramientas/copias"' in html
    assert "tone-ok" not in html and 'method="post"' not in html


def test_copias_con_error_de_configuracion_muestra_el_mensaje_escapado_y_nada_favorable():
    html = _render("views/copias.html", VIEW_CONTEXT["views/copias.html"][3])
    assert "Sin copias configuradas que se puedan leer" in html
    assert "&lt;script&gt;" in html and "tone-ok" not in html


def test_copias_con_problema_ofrece_backrest_y_el_procedimiento():
    html = _render("views/copias.html", VIEW_CONTEXT["views/copias.html"][0])
    assert "Con problemas" in html
    assert 'href="https://copias.example.org"' in html and 'href="/ayuda/kit-copias"' in html
    assert "Esta vista no declara conformidad." in html


def test_copias_sin_backrest_lo_dice_y_no_enlaza_a_ningun_host():
    html = _render("views/copias.html", VIEW_CONTEXT["views/copias.html"][1])
    assert "Backrest no está desplegado en esta caja" in html
    assert "https://" not in html.split("<main", 1)[-1]


def test_conectores_sin_recogidas_es_neutral_y_sin_configurar_no_es_favorable():
    sin = _con_page(blocks=(_CON_BLOCKS[0],))
    html = _render("views/conectores.html", sin)
    assert "Sin recogidas todavía" in html and "Sin configurar" in html
    assert "tone-ok" not in html.split("<main", 1)[-1].split("Datos leídos", 1)[0]


def test_conectores_parcial_credencial_y_enlaces_de_ayuda():
    html = _render("views/conectores.html", _con_page())
    assert "Recogida parcial" in html and "credencial ausente" in html
    assert '<span class="mono">ENS_GITHUB_TOKEN</span> en deploy/.env de la caja' in html
    assert (
        "La web no puede saber si la credencial está puesta: míralo en la última recogida." in html
    )
    assert 'href="/ayuda/conector-github"' in html and 'href="/copias"' in html
    assert 'href="/herramientas/copias"' in html and "Sin ficha en esta versión" in html


def _main(html):
    return html.split("<main", 1)[-1]


def test_conectores_resumen_lista_los_mismos_conectores_y_no_es_favorable_sin_recogidas():
    page = _con_page()
    html = _render("views/conectores.html", page)
    resumen = html.split('aria-labelledby="h-resumen"', 1)[1].split("</section>", 1)[0]
    for i in range(1, len(page["page"].blocks) + 1):
        assert f'href="#h-conector-{i}"' in resumen
    assert resumen.count("<tr>") == len(page["page"].blocks) + 1  # + cabecera
    sin = _render("views/conectores.html", _con_page(blocks=(_CON_BLOCKS[1],)))
    fila = sin.split('aria-labelledby="h-resumen"', 1)[1].split("</section>", 1)[0]
    assert "Sin recogidas todavía" in fila and "tone-ok" not in fila


@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        ("failed", "anteriores a la última recogida, que falló"),
        ("partial", "anteriores a la última recogida, que fue parcial"),
        ("ok", "anteriores a la última recogida: no la reflejan todavía."),
        ("unreadable", "anteriores a una recogida posterior que no se pudo verificar."),
    ],
)
def test_conectores_resultados_anteriores_a_la_ultima_recogida_llevan_aviso(outcome, text):
    stale = dataclasses.replace(_CON_EVIDENCE, stale=True, last_outcome=outcome)
    block = dataclasses.replace(_CON_BLOCKS[5], evidence=stale)
    html = _render("views/conectores.html", _con_page(blocks=(block,)))
    assert text in html and html.index(text) < html.index("Resultados de las comprobaciones") + 900


def test_conectores_credencial_aparece_una_sola_vez_por_bloque():
    block = _CON_BLOCKS[4]
    html = _render("views/conectores.html", _con_page(blocks=(block,)))
    assert html.count("La web no puede saber si la credencial está puesta") == 1


def test_conectores_ficha_solo_si_no_hay_boton_y_registro_ilegible_avisa():
    html = _render("views/conectores.html", _con_page(blocks=(_CON_BLOCKS[0],)))
    assert "Ver la ficha de" not in html
    reader = _render(
        "views/conectores.html", _con_page(blocks=(_CON_BLOCKS[0],)) | {"permissions": READER}
    )
    assert "No se pudo leer el registro" not in reader
    broken = _render("views/conectores.html", _con_page(registry_unreadable=True))
    assert "No se pudo leer el registro de conectores" in broken


def test_conectores_lector_no_ve_botones_ni_formularios():
    readers = _con_page(
        blocks=tuple(
            dataclasses.replace(b, can_configure=False, can_collect=False)
            for b in (*_CON_BLOCKS, *_CON_REQUEST_BLOCKS)
        )
    )
    html = _render("views/conectores.html", readers | {"permissions": READER})
    assert "Configurar" not in html and 'method="post"' not in html and "<button" not in html


def test_conectores_form_con_confirmacion_errores_y_estado_inactivo():
    html = _render(
        "views/conectores_form.html",
        _con_form_context(
            form=_con_form(needs_confirmation=True),
            errors=_CON_FIELD_ERRORS,
        ),
    )
    assert 'name="confirmar_sobrescritura"' in html and 'id="err-organization"' in html
    assert "<select" in html and "<textarea" in html
    blocked = _render(
        "views/conectores_form.html",
        _con_form_context(form=_con_form(writes_blocked=True, blocked_notice="Aviso fijo")),
    )
    assert "Aviso fijo" in blocked and '<fieldset class="gate" disabled>' in blocked


def test_conectores_form_lector_y_backups_no_tienen_formulario():
    for context in (
        _con_form_context(block=_con_block(can_configure=False), permissions=READER),
        _con_form_context(form=None, block=_CON_BACKUPS),
    ):
        html = _render("views/conectores_form.html", context)
        assert 'method="post"' not in html and "<button" not in html


def test_inventario_activo_lista_los_conectores_que_lo_citan():
    html = _render("views/inventario_activo.html", VIEW_CONTEXT["views/inventario_activo.html"][0])
    assert "Conectores que citan este activo" in html and 'href="/conectores/github"' in html


def _request_html(key):
    block = _CON_REQUEST_BLOCKS[list(_CON_REQUESTS).index(key)]
    return _render("views/conectores.html", _con_page(blocks=(block,)))


def test_conectores_pendiente_y_en_curso_no_ofrecen_el_boton_y_dicen_cuando_se_recoge():
    for key in ("pendiente", "en-curso"):
        assert 'action="/conectores/github/recoger"' not in _request_html(key)
    assert "normalmente en menos de un minuto" in _request_html("pendiente")


def test_conectores_caducada_remite_al_runbook_y_vuelve_a_ofrecer_el_boton():
    html = _request_html("caducada")
    assert "runbook" in html and "pídela de nuevo" in html
    assert 'action="/conectores/github/recoger"' in html and "Recoger ahora</button>" in html


def test_conectores_terminada_nunca_lleva_tono_favorable():
    for key in (
        "hecha",
        "recogida-con-error",
        "checks-no-registrados",
        "no-se-pudo",
        "id-invalido",
    ):
        card = _request_html(key).split("Recoger ahora</h3>")[1].split("<form")[0]
        assert "tone-ok" not in card, key
    assert "tone-bad" in _request_html("recogida-con-error")
    assert "comprobaciones no registradas" in _request_html("checks-no-registrados")


def test_conectores_identificador_no_canonico_no_se_muestra():
    html = _request_html("id-invalido")
    assert "identificador no válido" in html


def test_conectores_errores_de_formulario_y_aviso_de_peticion_arriba():
    context = _con_page() | {"form_errors": ["Ya hay una petición"], "requested_text": "Creada"}
    html = _render("views/conectores.html", context)
    assert 'role="alert"' in html and html.index("Ya hay una petición") < html.index("Resumen")
    assert "Creada" in html


def test_conectores_ficha_de_solo_lectura_muestra_la_peticion_sin_boton():
    block = _CON_REQUEST_BLOCKS[0]
    block = dataclasses.replace(block, request=_CON_REQUESTS["caducada"], can_collect=True)
    html = _render("views/conectores_form.html", _con_form_context(block=block) | {"form": None})
    assert "Caducada" in html and "Recoger ahora</button>" not in html
    with_form = _render("views/conectores_form.html", _con_form_context(block=block))
    assert 'action="/conectores/github/recoger"' in with_form
