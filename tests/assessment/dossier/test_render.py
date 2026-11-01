"""Salida de la instantánea y de la vista: determinismo, avisos, rótulos y escapado."""

import json
import re
from datetime import UTC, timedelta

import pytest
from matrix.evaluate_helpers import NOW

from ens_evidence.assessment.dossier import (
    TransitionEntry,
    build_snapshot,
    render_snapshot_json,
    render_snapshot_md,
    render_view_md,
    snapshot_sha256,
)
from ens_evidence.assessment.dossier.model import (
    COMMITMENT,
    INTERNAL_REVIEW_NOTICE,
    DossierSnapshot,
)
from ens_evidence.models.dossier import CorrectiveMeasure, Dossier, record_sha256, transition
from ens_evidence.models.enums import DossierState, Readiness, Role
from ens_evidence.models.readiness import ReadinessResult

from .worlds import INPUTS

FORBIDDEN = re.compile(r"\b(conforme|cumple)\b", re.IGNORECASE)
HOSTILE = "<script>alert(1)</script> [x](http://e.vil) | # **negrita** `código`\n\n> cita"
READY = ReadinessResult(readiness=Readiness.READY, blockers=[], pending_human_review=False)


@pytest.fixture
def snapshot(basic):
    return build_snapshot(basic, INPUTS)


def as_media(snapshot: DossierSnapshot) -> DossierSnapshot:
    document = snapshot.model_dump(mode="json")
    document.update(category="MEDIA", notice=INTERNAL_REVIEW_NOTICE)
    return DossierSnapshot.model_validate(document)


def chain(snapshot, *, conclusions="Sin hallazgos.", measures=(), declared=False, **extra):
    """Cadena válida de transiciones hasta ANALYZED (o DECLARED) envuelta como entradas."""
    sha = snapshot_sha256(snapshot)
    dossier = Dossier(category=snapshot.category)
    steps = [
        (DossierState.SELF_ASSESSED, "Ana Ruiz", Role.SYSTEM_ADMINISTRATOR, {}),
        (
            DossierState.ANALYZED,
            "Marta Gil",
            Role.SECURITY_OFFICER,
            {
                "conclusions": conclusions,
                "corrective_measures": list(measures),
                "elevated_to": "Pedro Sanz",
                "elevated_at": NOW + timedelta(hours=2),
                "elevation_confirmed": True,
                "manual_judgment_review": True,
            },
        ),
    ]
    for index, (state, actor, role, fields) in enumerate(steps):
        dossier = transition(
            dossier,
            state,
            actor=actor,
            role=role,
            at=NOW + timedelta(hours=index + 1),
            dossier_sha256=sha,
            **fields,
        )
    if declared:
        analysis = dossier.transitions[-1]
        dossier = transition(
            dossier,
            DossierState.DECLARED,
            actor="Luis Pardo",
            role=Role.MANAGEMENT,
            at=NOW + timedelta(hours=5),
            dossier_sha256=sha,
            readiness=READY,
            analysis_sha256=record_sha256(analysis),
            **extra,
        )
    return [
        TransitionEntry(
            key=f"transitions/{n}-x.yaml",
            sha256=f"{n:064x}",
            recorded_at=NOW + timedelta(days=1, minutes=n),
            record=record,
        )
        for n, record in enumerate(dossier.transitions, start=1)
    ]


def test_json_es_determinista_y_roundtrip(snapshot):
    first = render_snapshot_json(snapshot)

    assert first == render_snapshot_json(snapshot)
    assert first == render_snapshot_json(DossierSnapshot.model_validate_json(first))
    assert first.endswith(b"\n") and json.loads(first)["commitment"] == COMMITMENT


def test_md_es_determinista_con_once_secciones_y_fuentes(snapshot):
    md = render_snapshot_md(snapshot)

    assert md == render_snapshot_md(snapshot)
    assert re.findall(r"^## (\d+)\. ", md, re.MULTILINE) == [str(n) for n in range(1, 12)]
    assert "La herramienta no declara conformidad" in md
    source = snapshot.sections[0].sources[0]
    assert f"sha256 {source.sha256}" in md
    assert source.timestamps[0].astimezone(UTC).isoformat() in md


def test_basica_no_lleva_etiqueta_de_revision_interna(snapshot):
    assert "Revisión interna" not in render_snapshot_md(snapshot)


def test_media_lleva_etiqueta_en_md_y_json(snapshot):
    media = as_media(snapshot)

    assert "Revisión interna; no sustituye a la auditoría de certificación" in (
        render_snapshot_md(media)
    )
    assert json.loads(render_snapshot_json(media))["notice"] == INTERNAL_REVIEW_NOTICE
    assert "Revisión interna" in render_view_md(media, chain(media))


def test_vista_sin_transiciones_deja_conclusiones_pendientes(snapshot):
    md = render_view_md(snapshot, [])

    assert "## 10. Conclusiones" in md
    assert "Sin transiciones registradas" in md
    assert "Elevado a" not in md


def test_analyzed_sin_medidas_correctoras_muestra_la_elevacion(snapshot):
    md = render_view_md(snapshot, chain(snapshot))

    assert "Sin medidas correctoras registradas." in md
    assert "Elevado a Pedro Sanz (" in md
    assert "Sin hallazgos." in md
    entry = chain(snapshot)[1]
    for expected in (
        entry.key.replace("_", r"\_"),
        entry.sha256,
        entry.recorded_at.astimezone(UTC).isoformat(),
    ):
        assert expected in md
    assert snapshot_sha256(snapshot) in md
    assert f"sha256 del análisis: {entry.sha256}" in md


def test_medidas_correctoras_se_listan(snapshot):
    measure = CorrectiveMeasure(description="Cifrar copias", addressee=Role.SYSTEM_OWNER)

    md = render_view_md(snapshot, chain(snapshot, measures=[measure]))

    assert "Cifrar copias" in md
    assert "Sin medidas correctoras registradas." not in md


def test_declared_se_rotula_como_acto_de_la_organizacion(snapshot):
    entries = chain(snapshot, declared=True)

    md = render_view_md(snapshot, entries)

    assert "Declaración de conformidad registrada por Luis Pardo (" in md
    assert "acto de la organización, la herramienta no declara" in md
    assert "Documento enlazado" not in md
    assert f"sha256 del análisis citado: {entries[-1].record.analysis_sha256}" in md


def test_documento_enlazado_se_rotula_como_pendiente_de_revision_humana(snapshot):
    md = render_view_md(snapshot, chain(snapshot, declared=True, declaration_evidence_id="ev-1"))

    assert "Documento enlazado: ev-1" in md
    assert "integridad del documento verificada; firma, formato PDF-A y vinculación" in md
    assert "los revisa una persona" in md


def test_textos_hostiles_se_escapan(snapshot):
    measure = CorrectiveMeasure(description=HOSTILE, addressee=Role.SYSTEM_OWNER)

    md = render_view_md(snapshot, chain(snapshot, conclusions=HOSTILE, measures=[measure]))

    assert "<script>" not in md
    assert "[x](" not in md
    assert r"\<script\>" in md
    assert r"\[x\]" in md
    assert "\n# " not in md and "`código`" not in md


def test_contenido_de_secciones_se_escapa(snapshot):
    document = snapshot.model_dump(mode="json")
    document["sections"][1]["content"] = {"alcance": HOSTILE, "lista": [HOSTILE]}
    hostile = DossierSnapshot.model_validate(document)

    md = render_snapshot_md(hostile)

    assert "<script>" not in md and "[x](" not in md and "`código`" not in md


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("media", [False, True])
def test_nunca_conforme_ni_cumple(snapshot, media, declared):
    subject = as_media(snapshot) if media else snapshot
    entries = chain(subject, declared=declared and not media)

    for text in (
        render_snapshot_md(subject),
        render_view_md(subject, []),
        render_view_md(subject, entries),
    ):
        assert not FORBIDDEN.search(text), FORBIDDEN.search(text)
