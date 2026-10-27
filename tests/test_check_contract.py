import pytest
from conftest import NOW, SHA
from pydantic import ValidationError

from ens_evidence.models import (
    CheckResult,
    CheckStatus,
    CollectionStatus,
    EvidenceManifest,
    translate_collection,
    unresolved_evidence_ids,
)


def make_check(status, collection_status=CollectionStatus.SUCCESS, **extra):
    return CheckResult(
        check_id="c1",
        asset_id="a",
        status=status,
        evaluated_at=NOW,
        reason="r",
        collection_status=collection_status,
        **extra,
    )


@pytest.mark.parametrize(
    ("collection", "kwargs", "status", "reason_code"),
    [
        (CollectionStatus.SUCCESS, {"compliant": True}, CheckStatus.PASS, None),
        (CollectionStatus.SUCCESS, {"compliant": False}, CheckStatus.FAIL, None),
        (CollectionStatus.SUCCESS, {"data_present": False}, CheckStatus.UNKNOWN, None),
        (CollectionStatus.SUCCESS, {"compliant": None}, CheckStatus.UNKNOWN, None),
        (CollectionStatus.ERROR, {}, CheckStatus.ERROR, None),
        (CollectionStatus.PERMISSION_DENIED, {}, CheckStatus.UNKNOWN, "permission_denied"),
        (CollectionStatus.NOT_SUPPORTED, {}, CheckStatus.UNKNOWN, "not_supported"),
    ],
)
def test_traduccion_recogida_a_check_segun_la_tabla_del_adr(
    collection, kwargs, status, reason_code
):
    assert tuple(translate_collection(collection, **kwargs)) == (status, reason_code)


@pytest.mark.parametrize(
    "collection",
    [CollectionStatus.ERROR, CollectionStatus.PERMISSION_DENIED, CollectionStatus.NOT_SUPPORTED],
)
def test_recogida_distinta_de_success_no_admite_pass_aunque_haya_evidencias(collection):
    with pytest.raises(ValidationError):
        make_check(CheckStatus.PASS, collection, evidence_ids=["ev-1"])


@pytest.mark.parametrize(
    ("collection", "status", "reason_code"),
    [
        (CollectionStatus.ERROR, CheckStatus.ERROR, None),
        (CollectionStatus.PERMISSION_DENIED, CheckStatus.UNKNOWN, "permission_denied"),
        (CollectionStatus.NOT_SUPPORTED, CheckStatus.UNKNOWN, "not_supported"),
    ],
)
def test_check_con_recogida_fallida_exige_exactamente_la_traduccion(
    collection, status, reason_code
):
    assert make_check(status, collection, reason_code=reason_code).status is status
    wrong_code = None if reason_code else "permission_denied"
    with pytest.raises(ValidationError):
        make_check(status, collection, reason_code=wrong_code)
    with pytest.raises(ValidationError):
        make_check(CheckStatus.FAIL, collection, reason_code=reason_code)


def test_modelos_rechazan_campos_desconocidos():
    with pytest.raises(ValidationError):
        make_check(CheckStatus.FAIL, bogus=1)


def test_evidence_ids_sin_manifiesto_se_detectan():
    manifest = EvidenceManifest(
        id="ev-1",
        source="s",
        asset_id="a",
        collected_at=NOW,
        artifact={"storage": "local", "key": "k", "sha256": SHA},
        collector={"name": "fake", "version": "1"},
        provenance={"kind": "collector", "submitted_by": "fake", "submitted_at": NOW},
    )
    check = make_check(CheckStatus.PASS, evidence_ids=["ev-1", "ev-9"])
    assert unresolved_evidence_ids(check, [manifest]) == ["ev-9"]


def test_manifiesto_rechaza_sha256_malformado():
    with pytest.raises(ValidationError):
        EvidenceManifest(
            id="ev-1",
            source="s",
            asset_id="a",
            collected_at=NOW,
            artifact={"storage": "local", "key": "k", "sha256": "xyz"},
            collector={"name": "fake", "version": "1"},
            provenance={"kind": "collector", "submitted_by": "fake", "submitted_at": NOW},
        )
