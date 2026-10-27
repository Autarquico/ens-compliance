import importlib.util
import sys
from datetime import UTC, datetime

import pytest
import yaml

from ens_evidence.catalog import ReviewState, catalog_sha256, review_status
from ens_evidence.catalog import review as review_module

from .conftest import REPO


@pytest.fixture
def review_dir(tmp_path, monkeypatch):
    """Aísla `review.yaml` del real: el paquete se resuelve a una carpeta temporal."""
    monkeypatch.setattr(review_module.resources, "files", lambda _package: tmp_path)
    return tmp_path


def write_review(directory, sha: str) -> None:
    (directory / "review.yaml").write_text(
        yaml.safe_dump(
            {
                "catalog_sha256": sha,
                "reviewed_by": "Ana Ruiz",
                "reviewed_at": datetime(2026, 10, 20, 9, 0, tzinfo=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def test_sin_review_yaml_el_catalogo_figura_sin_revisar(review_dir):
    status = review_status()

    assert status.state is ReviewState.UNREVIEWED
    assert "sin revisar" in status.message


def test_review_con_hash_distinto_falla_por_catalogo_cambiado(review_dir):
    write_review(review_dir, "0" * 64)

    status = review_status()

    assert status.state is ReviewState.STALE
    assert "revisión nueva" in status.message


def test_review_con_el_hash_actual_pasa(review_dir):
    write_review(review_dir, catalog_sha256())

    status = review_status()

    assert status.state is ReviewState.CURRENT
    assert "Ana Ruiz" in status.message


def test_review_malformado_es_invalido(review_dir):
    (review_dir / "review.yaml").write_text("catalog_sha256: [\n", encoding="utf-8")

    assert review_status().state is ReviewState.INVALID


def test_el_catalogo_commiteado_tiene_una_revision_vigente_si_existe_review_yaml():
    real = REPO / "src" / "ens_evidence" / "catalog" / "review.yaml"
    if not real.exists():
        pytest.skip("revisión humana pendiente (tarea H): aún no hay review.yaml")

    assert review_status().state is ReviewState.CURRENT


@pytest.fixture(scope="module")
def review_tool():
    path = REPO / "tools" / "catalog_review.py"
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("catalog_review", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def test_el_informe_html_incluye_las_73_medidas_y_destaca_las_3_divergencias(
    review_tool, tmp_path, monkeypatch
):
    output = tmp_path / "informe.html"
    monkeypatch.setattr(sys, "argv", ["catalog_review.py", str(output)])

    assert review_tool.main() == 0

    html = output.read_text(encoding="utf-8")
    assert html.count("<section class=measure ") == 73
    divergences = html.split("<div class=divergences>")[1].split("</div>")[0]
    for measure_id in ("op.pl.2", "op.pl.4", "mp.s.4"):
        assert f"<b>{measure_id}</b>" in divergences
    assert divergences.count("<b>") == 3


def test_el_informe_html_incluye_el_texto_introductorio_de_grupos_y_subgrupos(
    review_tool, tmp_path, monkeypatch
):
    output = tmp_path / "informe.html"
    monkeypatch.setattr(sys, "argv", ["catalog_review.py", str(output)])
    review_tool.main()

    html = output.read_text(encoding="utf-8")

    assert "mantendrá la plena responsabilidad de los riesgos" in html
    assert "El sistema estará sujeto a medidas de monitorización" in html
    assert "acuerdos de colaboración" in html
