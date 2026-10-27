import hashlib
import re
from html.parser import HTMLParser

import pytest
import yaml

from ens_evidence.catalog import catalog_sha256
from ens_evidence.catalog.model import Axis, Dimension, MeasureCard

from .conftest import CATALOG_DIR

ID_FORMAT = re.compile(r"^(?:org\.\d+|(?:op|mp)\.[a-z]+\.\d+)$")


class _SummaryIds(HTMLParser):
    """Primera columna de la primera tabla (resumen) que parece un id de medida."""

    def __init__(self) -> None:
        super().__init__()
        self.tables = 0
        self.in_cell = False
        self.cell = ""
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables += 1
        if tag == "tr":
            self.cell = ""
        if tag == "td" and self.tables == 1:
            self.in_cell, self.cell = True, ""

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if tag == "td" and self.in_cell:
            self.in_cell = False
            text = self.cell.strip()
            if ID_FORMAT.match(text):
                self.ids.append(text)


def _by_id(catalog):
    return {m.id: m for m in catalog.measures}


def test_los_ids_del_catalogo_coinciden_uno_a_uno_con_la_tabla_resumen(catalog, snapshot):
    parser = _SummaryIds()
    parser.feed(snapshot.decode("utf-8"))

    assert parser.ids
    assert sorted(m.id for m in catalog.measures) == sorted(parser.ids)


def test_todas_las_fichas_validan_contra_el_modelo():
    for file in sorted((CATALOG_DIR / "data").glob("*.yaml")):
        for raw in yaml.safe_load(file.read_text(encoding="utf-8"))["measures"]:
            MeasureCard.model_validate(raw)


def test_reextraer_desde_la_instantanea_reproduce_los_yaml_commiteados(
    tmp_path, extractor, snapshot, meta
):
    cards = extractor.extract(snapshot, meta)
    extractor.write_data(cards, tmp_path)

    committed = sorted((CATALOG_DIR / "data").glob("*.yaml"))
    assert [f.name for f in sorted(tmp_path.glob("*.yaml"))] == [f.name for f in committed]
    for file in committed:
        assert (tmp_path / file.name).read_bytes() == file.read_bytes()


def test_el_sha256_de_la_instantanea_coincide_con_los_metadatos(snapshot, meta):
    assert hashlib.sha256(snapshot).hexdigest() == meta["snapshot_sha256"]
    assert len(snapshot) == meta["snapshot_bytes"]


def test_una_instantanea_alterada_sin_actualizar_el_hash_se_rechaza(extractor, snapshot, meta):
    with pytest.raises(extractor.ExtractionError) as failure:
        extractor.extract(snapshot + b" ", meta)

    assert "sha256" in failure.value.problems[0]


def test_html_truncado_falla_con_la_lista_de_problemas(extract_manipulated):
    problems = extract_manipulated(lambda html: html[: len(html) // 2])

    assert problems
    assert any("truncado" in p or "sin cuerpo" in p for p in problems)


def test_etiqueta_desconocida_falla(extract_manipulated):
    problems = extract_manipulated(
        lambda html: html.replace("<tbody>", "<tbody><marquee>x</marquee>", 1)
    )

    assert any("marquee" in p for p in problems)


def test_medida_sin_cuerpo_falla_nombrandola(extract_manipulated):
    problems = extract_manipulated(
        lambda html: html.replace(
            "5.8.4 Protección frente a la denegación de servicio [mp.s.4].", "x"
        )
    )

    assert "mp.s.4: está en la tabla resumen y no tiene cuerpo" in problems


def test_titulo_de_resumen_distinto_y_no_listado_hace_fallar(extract_manipulated):
    problems = extract_manipulated(
        lambda html: html.replace(
            ">Arquitectura de Seguridad<", ">Arquitectura de Seguridad distinta<", 1
        )
    )

    assert any("op.pl.2" in p for p in problems)


def test_solo_hay_tres_divergencias_de_titulo_aceptadas(extractor, catalog):
    assert set(extractor.ACCEPTED_SUMMARY_TITLES) == {"op.pl.2", "op.pl.4", "mp.s.4"}
    with_notes = {m.id for m in catalog.measures if m.notes}
    assert with_notes == set(extractor.ACCEPTED_SUMMARY_TITLES)


def test_una_medida_por_categoria_lleva_niveles_de_categoria(catalog):
    measure = _by_id(catalog)["op.pl.1"]

    assert measure.applies_by is Axis.CATEGORY
    assert set(measure.levels) == {"BASICA", "MEDIA", "ALTA"}


def test_una_medida_por_dimension_lleva_su_dimension_y_niveles_bajo_medio_alto(catalog):
    for measure_id, dimensions in (("op.pl.4", [Dimension.D]), ("mp.info.6", [Dimension.D])):
        measure = _by_id(catalog)[measure_id]

        assert measure.applies_by is Axis.DIMENSION
        assert measure.dimensions == dimensions
        assert set(measure.levels) == {"BAJO", "MEDIO", "ALTO"}


def test_op_acc_5_medio_exige_base_mas_r5_y_una_de_r2_r3_r4(catalog):
    medio = _by_id(catalog)["op.acc.5"].levels["MEDIO"]

    assert not medio.not_applicable
    assert sorted(medio.all_of) == ["R5", "base"]
    assert medio.any_of == [["R2", "R3", "R4"]]


def test_celdas_no_aplicables_conservan_la_expresion_literal(catalog):
    not_applicable = [
        level for m in catalog.measures for level in m.levels.values() if level.not_applicable
    ]

    assert not_applicable
    assert all(level.expression in ("", "n.a.") for level in not_applicable)
    assert all(not level.all_of and not level.any_of for level in not_applicable)
    assert _by_id(catalog)["mp.if.6"].levels["BAJO"].expression == "n.a."


def test_ids_unicos_y_con_formato(catalog):
    ids = [m.id for m in catalog.measures]

    assert len(ids) == len(set(ids))
    assert all(ID_FORMAT.match(i) for i in ids)


def test_el_catalogo_tiene_73_medidas_por_grupo(catalog):
    counts: dict[str, int] = {}
    for m in catalog.measures:
        counts[m.group.value] = counts.get(m.group.value, 0) + 1

    assert counts == {"org": 4, "op": 33, "mp": 36}
    assert len(catalog.measures) == 73


def test_cada_ficha_referencia_la_instantanea_commiteada(catalog, meta):
    assert {m.source.snapshot_sha256 for m in catalog.measures} == {meta["snapshot_sha256"]}


def test_los_datos_estan_dentro_del_paquete():
    from importlib import resources

    data = resources.files("ens_evidence.catalog") / "data"

    assert sorted(f.name for f in data.iterdir() if f.name.endswith(".yaml")) == [
        "mp.yaml",
        "op.yaml",
        "org.yaml",
    ]
    assert len(catalog_sha256()) == 64


def test_un_refuerzo_exigido_en_media_figura_en_su_required_at(catalog):
    reinforcement = {r.id: r for r in _by_id(catalog)["mp.eq.4"].reinforcements}["R1"]

    assert reinforcement.required_at == ["MEDIO", "ALTO"]


def test_un_refuerzo_que_ningun_nivel_exige_tiene_required_at_vacio(catalog):
    reinforcements = {r.id: r for r in _by_id(catalog)["op.acc.5"].reinforcements}

    assert reinforcements["R6"].required_at == []
    assert reinforcements["R7"].required_at == []


def test_required_at_de_op_acc_5_solo_recoge_lo_que_la_tabla_exige_siempre(catalog):
    reinforcements = {r.id: r for r in _by_id(catalog)["op.acc.5"].reinforcements}

    assert reinforcements["R1"].required_at == []
    assert reinforcements["R2"].required_at == []
    assert reinforcements["R5"].required_at == ["MEDIO", "ALTO"]


def test_op_acc_5_r3_y_r4_son_alternativas_en_todos_los_niveles_no_exigencias(catalog):
    reinforcements = {r.id: r for r in _by_id(catalog)["op.acc.5"].reinforcements}

    for reinforcement_id in ("R3", "R4"):
        assert reinforcements[reinforcement_id].alternative_at == ["BAJO", "MEDIO", "ALTO"]
        assert reinforcements[reinforcement_id].required_at == []


def test_mp_s_2_r2_es_alternativa_en_basica_y_media_y_se_exige_en_alta(catalog):
    reinforcements = {r.id: r for r in _by_id(catalog)["mp.s.2"].reinforcements}

    assert reinforcements["R2"].alternative_at == ["BASICA", "MEDIA"]
    assert reinforcements["R2"].required_at == ["ALTA"]


def test_alternative_at_incoherente_con_la_tabla_se_rechaza(catalog):
    raw = _by_id(catalog)["op.acc.5"].model_dump(mode="json")
    next(r for r in raw["reinforcements"] if r["id"] == "R3")["alternative_at"] = ["BAJO"]

    with pytest.raises(ValueError, match="alternative_at no casa con la tabla"):
        MeasureCard.model_validate(raw)


def test_required_at_incoherente_con_la_tabla_se_rechaza(catalog):
    raw = _by_id(catalog)["mp.eq.4"].model_dump(mode="json")
    raw["reinforcements"][0]["required_at"] = ["BAJO"]

    with pytest.raises(ValueError, match="required_at no casa con la tabla"):
        MeasureCard.model_validate(raw)


def test_el_catalogo_se_ordena_org_op_mp(catalog):
    ids = [m.id for m in catalog.measures]

    assert ids[0] == "org.1"
    assert ids[-1].startswith("mp.")
    groups = [m.group.value for m in catalog.measures]
    assert groups == sorted(groups, key=["org", "op", "mp"].index)
    assert [g.id for g in catalog.groups] == ["org", "op", "mp"]


def test_el_texto_introductorio_de_grupos_y_subgrupos_esta_en_el_catalogo(catalog):
    subgroups = {s.id: " ".join(s.text) for s in catalog.subgroups}

    assert "mantendrá la plena responsabilidad de los riesgos" in subgroups["op.ext"]
    assert "acuerdos de colaboración" in subgroups["op.acc"]
    assert "El sistema estará sujeto a medidas de monitorización" in subgroups["op.mon"]
    assert catalog.group(_by_id(catalog)["op.acc.5"].group).text


def test_una_tabla_en_el_texto_de_introduccion_de_un_subgrupo_hace_fallar(extract_manipulated):
    problems = extract_manipulated(
        lambda html: html.replace(
            '<p class="parrafo_2">4.4.1 Contratación',
            '<table class="tabla"><tr><td>x</td></tr></table>'
            '<p class="parrafo_2">4.4.1 Contratación',
            1,
        )
    )

    assert "op.ext: tabla en el texto de introducción" in problems
