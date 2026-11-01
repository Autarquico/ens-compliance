"""Escapado del HTML de los informes con un corpus hostil inyectado como datos del collector."""

import json
import re
from datetime import timedelta
from html import escape

import pytest

from ens_evidence.evidence.importer import import_manual
from ens_evidence.evidence.manifests import list_manifests

from .acceptance_fakes import ASSETS, HOSTILE_ENV
from .acceptance_helpers import START, connector, counts, cycle_world, daily_html, daily_json
from .test_render_html import HOSTILE, assert_inert

CYCLE = ("report", "daily", "--cycle")
MODES = {"github": "hostile", "cloudflare": "hostile", "google-workspace": "ok"}


@pytest.fixture(scope="module")
def hostile(tmp_path_factory):
    with pytest.MonkeyPatch.context() as monkeypatch:
        world = cycle_world(tmp_path_factory.mktemp("hostile"), monkeypatch, MODES)
        monkeypatch.setenv(HOSTILE_ENV, json.dumps(HOSTILE))
        for asset in HOSTILE[:2]:  # la evidencia manual sí admite texto libre en el activo
            import_manual(
                world.store,
                asset.encode(),
                asset_id=asset,
                submitted_by="ana",
                now=START - timedelta(hours=1),
                collected_at=START - timedelta(days=1),
                valid_until=START + timedelta(days=5),
            )
        world.cycle = world.run(*CYCLE)
        yield world


def test_el_corpus_hostil_llega_al_informe_como_texto_escapado(hostile):
    document = daily_html(hostile.store)

    parsed = assert_inert(document)

    text = "".join(parsed.text)
    for payload in HOSTILE[:2]:  # el activo de la evidencia manual llega a las caducidades
        assert payload in text
        assert escape(payload, quote=True) in document


def test_ningun_script_ni_manejador_de_evento_sin_escapar(hostile):
    document = daily_html(hostile.store)

    assert "<script" not in document.lower()
    assert re.search(r"<[^>]*\bon\w+\s*=", document, re.IGNORECASE) is None
    assert "<img" not in document.lower()


def test_la_csp_esta_presente_y_prohibe_todo_salvo_el_estilo_propio(hostile):
    document = daily_html(hostile.store)

    assert (
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
    ) in document


def test_el_json_conserva_el_corpus_como_datos(hostile):
    document = daily_json(hostile.store)

    assert HOSTILE[0] in json.dumps(document, ensure_ascii=False)


def test_un_resultado_con_fuente_ajena_no_deja_vigente_el_pass_anterior(hostile):
    document = daily_json(hostile.store)

    github = connector(document, "github")

    assert counts(github["checks"])["PASS"] == 0


def test_la_identidad_hostil_del_collector_se_rechaza_y_no_llega_a_la_evidencia(hostile):
    collected = [m for m in list_manifests(hostile.store).manifests if m.collector is not None]
    assert {m.source for m in collected} <= set(ASSETS)
    assert {m.asset_id for m in collected} <= set(ASSETS.values())

    github = connector(daily_json(hostile.store), "github")

    assert github["collection"]["value"] == "ERROR"
