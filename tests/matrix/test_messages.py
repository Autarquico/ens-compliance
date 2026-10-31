import pytest

from ens_evidence.matrix import messages
from ens_evidence.matrix.messages import BLOCKER_TEXTS, blocker_lines, blocker_text
from ens_evidence.models.enums import Role
from ens_evidence.models.readiness import Blocker, BlockerKind

FORBIDDEN = ("cumple", "certifica", "garantiza")


def blocker(kind, detail="", measure="op.acc.5"):
    return Blocker(kind=kind, measure_id=measure, detail=detail)


def test_una_medida_sin_revisar_no_se_presenta_como_no_conforme():
    for kind, detail in (
        (BlockerKind.NOT_COMPLIANT, "NOT_ASSESSED"),
        (BlockerKind.MISSING_REVIEW, "review_missing"),
    ):
        text = blocker_text(blocker(kind, detail))

        assert text.startswith("sin revisar")
        assert "no conforme" not in text and "NOT_COMPLIANT" not in text


def test_la_valoracion_de_una_persona_si_se_dice_no_conforme():
    text = blocker_text(blocker(BlockerKind.NOT_COMPLIANT, "NON_COMPLIANT"))

    assert "no conforme" in text


def test_sin_revisar_se_dice_una_sola_vez_aunque_haya_dos_bloqueos():
    both = [
        blocker(BlockerKind.NOT_COMPLIANT, "NOT_ASSESSED"),
        blocker(BlockerKind.MISSING_REVIEW, "review_missing"),
    ]

    assert len(blocker_lines(both)) == 1


@pytest.mark.parametrize(
    ("kind", "detail", "command"),
    [
        (BlockerKind.MISSING_REVIEW, "owner_missing", "matrix set op.acc.5 --owner"),
        (BlockerKind.MISSING_REVIEW, "review_every_days_missing", "matrix set op.acc.5 --every"),
        (BlockerKind.MISSING_REVIEW, "review_missing", "matrix review op.acc.5"),
        (BlockerKind.MISSING_REVIEW, "", "matrix review op.acc.5"),
        (BlockerKind.EXPIRED_REVIEW, "context_changed", "matrix review op.acc.5"),
        (BlockerKind.EXPIRED_REVIEW, "links_changed", "matrix review op.acc.5"),
        (BlockerKind.EXPIRED_REVIEW, "evidence_changed:EV-1", "matrix review op.acc.5"),
        (BlockerKind.MISSING_EVIDENCE, "clause:op.acc.5.1", "matrix link op.acc.5"),
        (BlockerKind.MISSING_EVIDENCE, "", "matrix link op.acc.5"),
        (BlockerKind.EXPIRED_EVIDENCE, "evidence_expired:EV-1", "matrix link op.acc.5"),
        (BlockerKind.EXPIRED_EVIDENCE, "document_not_approved:politica:OBSOLETE", "docs approve"),
        (BlockerKind.UNDETERMINED_APPLICABILITY, "declaration_stale", "applicability status"),
    ],
)
def test_cada_bloqueo_dice_el_comando_del_siguiente_paso(kind, detail, command):
    text = blocker_text(blocker(kind, detail))

    assert command in text
    assert kind.value not in text


def test_un_detalle_desconocido_no_se_pierde():
    text = blocker_text(blocker(BlockerKind.MISSING_EVIDENCE, "algo_nuevo:x"))

    assert "algo_nuevo:x" in text


def test_las_clausulas_sin_evidencia_van_en_una_sola_linea():
    blockers = [blocker(BlockerKind.MISSING_EVIDENCE, f"clause:op.acc.5.{n}") for n in (1, 2, 3)]

    (line,) = blocker_lines(blockers)

    assert "faltan evidencias en 3 cláusulas: op.acc.5.1, op.acc.5.2, op.acc.5.3" in line
    assert "--covers op.acc.5.1,op.acc.5.2,op.acc.5.3" in line


def test_check_con_sujeto_y_check_desconocido_no_se_confunden():
    subject = blocker_text(blocker(BlockerKind.MISSING_EVIDENCE, "check_unknown_subject:c1:repo-a"))
    unknown = blocker_text(blocker(BlockerKind.MISSING_EVIDENCE, "check_unknown:c1"))

    assert "repo-a" in subject and "no pudo valorar" in subject
    assert "no existe" in unknown


def test_check_no_results_indica_collect_y_check_all():
    text = blocker_text(blocker(BlockerKind.MISSING_EVIDENCE, "check_no_results:c1"))

    assert "ens-evidence collect <conector>" in text
    assert "ens-evidence check --all" in text


@pytest.mark.parametrize(
    "detail",
    [
        "check_last_run_failed:github",
        "check_history_unreadable:evidence/checks/github/000002.json",
        "check_scope_changed:github",
        "evidence_unbound:EV-20260101-aaaa",
        "check_evidence_changed:EV-20260101-aaaa",
    ],
)
def test_los_bloqueos_de_checks_tienen_texto_propio(detail):
    text = blocker_text(blocker(BlockerKind.MISSING_EVIDENCE, detail))

    assert detail.split(":")[0] not in text


def test_los_textos_no_afirman_conformidad():
    texts = [*BLOCKER_TEXTS.values(), *messages.ASSESSMENT_LABELS.values()]

    for text in texts:
        assert not any(word in text.lower() for word in FORBIDDEN), text


def test_todo_rol_tiene_etiqueta():
    assert set(messages.ROLE_LABELS) == set(Role)
