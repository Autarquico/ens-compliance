"""Tabla o columna inexistente en ese osquery: del script a HOST-005 UNKNOWN `not_supported`."""

import pytest

from ens_evidence.endpoint.checks import FIREWALL, evaluate_host_checks
from ens_evidence.endpoint.evidence import load_host_evidence
from ens_evidence.scope.inventory import load_assets

from .support import Answer, collect, import_package, normalized, utc_now
from .test_checks_laptop_e2e import endpoint_config


@pytest.mark.parametrize("error", ["Error: no such table: alf", "Error: no such column: stealth"])
def test_una_tabla_o_columna_inexistente_deja_host_005_en_unknown_not_supported(
    store, tmp_path, error
):
    run = collect(tmp_path / "run", answers={"alf": Answer(out=b"", code=1, err=error)})
    import_package(store, run.data)

    assert normalized(store)["data"]["queries"]["alf"]["status"] == "NOT_SUPPORTED"
    assets, _ = load_assets(store)
    results = evaluate_host_checks(
        assets.assets, load_host_evidence(store).items, endpoint_config(store), utc_now()
    )
    firewall = next(r for r in results if r.asset_id == "lap-ana" and r.check_id == FIREWALL)
    assert firewall.status == "UNKNOWN"
    assert firewall.reason_code == "not_supported"


def test_un_fallo_de_ejecucion_sin_pista_sigue_siendo_error(store, tmp_path):
    run = collect(tmp_path / "run", answers={"alf": Answer(out=b"", code=1, err="boom")})
    import_package(store, run.data)

    assert normalized(store)["data"]["queries"]["alf"]["status"] == "ERROR"
