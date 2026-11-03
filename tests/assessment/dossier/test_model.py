import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ens_evidence.assessment.dossier import methodology
from ens_evidence.assessment.dossier.model import (
    COMMITMENT,
    INTERNAL_REVIEW_NOTICE,
    SECTION_TITLES,
    DossierSnapshot,
    Section,
    SectionState,
    SnapshotInputs,
    SourceRef,
)
from ens_evidence.catalog.controls import CatalogVersion
from ens_evidence.models.enums import Category

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
SHA = "a" * 64


def source(**overrides):
    return SourceRef(
        **{
            "kind": "matrix",
            "key": "controls/matrix.yaml",
            "sha256": SHA,
            "timestamps": [NOW],
            **overrides,
        }
    )


def section(number: int, **overrides) -> Section:
    return Section(
        **{
            "number": number,
            "title": SECTION_TITLES[number - 1],
            "state": SectionState.RECORDED,
            "sources": [source()],
            "content": {"n": number},
            **overrides,
        }
    )


def inputs() -> SnapshotInputs:
    return SnapshotInputs(
        generated_at=NOW,
        evaluated_at=NOW,
        reference_now=NOW,
        methodology_version=methodology.METHODOLOGY_VERSION,
        product_version="0.1.0",
        catalog_version=CatalogVersion(snapshot_sha256=SHA, consolidation_date="2022-05-01"),
    )


def snapshot(category=Category.BASICA, **overrides) -> DossierSnapshot:
    notice = None if category is Category.BASICA else INTERNAL_REVIEW_NOTICE
    return DossierSnapshot(
        **{
            "category": category,
            "notice": notice,
            "inputs": inputs(),
            "sections": [section(n) for n in range(1, 12)],
            **overrides,
        }
    )


def test_valid_snapshot_has_eleven_numbered_sections():
    assert [s.number for s in snapshot().sections] == list(range(1, 12))
    assert snapshot().commitment == COMMITMENT


@pytest.mark.parametrize(
    "sections",
    [
        [section(n) for n in range(1, 11)],
        [section(n) for n in range(1, 12)] + [section(11)],
        [section(n) for n in (2, 1, *range(3, 12))],
    ],
)
def test_rejects_wrong_section_set(sections):
    with pytest.raises(ValidationError):
        snapshot(sections=sections)


def test_rejects_title_not_matching_number():
    with pytest.raises(ValidationError):
        section(3, title="Alcance")


@pytest.mark.parametrize(
    "bad",
    [
        {"sha256": "A" * 64},
        {"sha256": "a" * 63},
        {"timestamps": []},
        {"timestamps": [datetime(2026, 11, 1)]},
    ],
)
def test_rejects_invalid_source(bad):
    with pytest.raises(ValidationError):
        source(**bad)


def test_rejects_unknown_fields_and_other_commitment():
    with pytest.raises(ValidationError):
        Section(**section(1).model_dump(), extra=1)
    with pytest.raises(ValidationError):
        snapshot(commitment="Conforme")


def test_no_conformity_state_exists():
    assert not {s for s in SectionState if re.search(r"CONFORM|COMPLIANT", s)}


@pytest.mark.parametrize("category", [Category.MEDIA, Category.ALTA])
def test_internal_review_notice_required_above_basica(category):
    assert snapshot(category).notice == INTERNAL_REVIEW_NOTICE
    with pytest.raises(ValidationError):
        snapshot(category, notice=None)


def test_basica_carries_no_internal_review_notice():
    with pytest.raises(ValidationError):
        snapshot(Category.BASICA, notice=INTERNAL_REVIEW_NOTICE)


def test_notice_mentions_internal_review_and_article_38():
    assert "no sustituye a la auditoría de certificación (art. 38.1)" in INTERNAL_REVIEW_NOTICE


def test_fixed_texts_never_say_conforme_or_cumple():
    texts = [
        COMMITMENT,
        INTERNAL_REVIEW_NOTICE,
        *SECTION_TITLES,
        methodology.methodology_text(),
        *map(str, SectionState),
    ]
    for text in texts:
        assert not re.search(r"\b(conforme|cumple)\b", text, re.IGNORECASE)


def test_model_dump_is_deterministic():
    first, second = snapshot(Category.MEDIA), snapshot(Category.MEDIA)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert DossierSnapshot.model_validate_json(first.model_dump_json()) == first


def test_methodology_hash_is_stable_and_tracks_version(monkeypatch):
    first = methodology.methodology_sha256()
    assert first == methodology.methodology_sha256()
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    monkeypatch.setattr(methodology, "METHODOLOGY_VERSION", "2")
    assert methodology.methodology_sha256() != first


BOE_FIXTURE = Path(__file__).parents[2] / "fixtures" / "boe" / "rd311-consolidado.txt"


def _boe_text() -> str | None:
    path = Path(os.environ.get("ENS_BOE_RD311_TXT") or BOE_FIXTURE)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.mark.skipif(
    _boe_text() is None,
    reason="sin instantánea del BOE: define ENS_BOE_RD311_TXT con el texto del RD 311/2022",
)
@pytest.mark.parametrize("source_name", list(methodology.QUOTES))
def test_quotes_appear_literally_in_boe(source_name):
    boe = _normalized(_boe_text())
    for paragraph in methodology.QUOTES[source_name].split("\n"):
        assert _normalized(paragraph) in boe
