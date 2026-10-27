from typer.testing import CliRunner

from ens_evidence.cli import app

runner = CliRunner()


def test_catalog_list_muestra_las_73_medidas():
    result = runner.invoke(app, ["catalog", "list"])

    assert result.exit_code == 0, result.output
    assert "op.acc.5" in result.output
    assert "73 medidas." in result.output


def test_catalog_list_filtra_por_grupo():
    result = runner.invoke(app, ["catalog", "list", "--group", "org"])

    assert result.exit_code == 0, result.output
    assert "4 medidas." in result.output
    assert "op.acc.5" not in result.output


def test_catalog_show_imprime_ficha_con_niveles_y_refuerzos():
    result = runner.invoke(app, ["catalog", "show", "op.acc.5"])

    assert result.exit_code == 0, result.output
    assert "op.acc.5" in result.output
    assert "+ [R2 o R3 o R4] + R5" in result.output
    assert "R5  Registro (exigido en: MEDIO, ALTO)" in result.output
    assert "R6  Limitación de la ventana de acceso (ningún nivel)" in result.output
    assert "R3  Certificados (alternativa en: BAJO, MEDIO, ALTO)" in result.output
    assert "R2  Contraseña + OTP (alternativa en: BAJO, MEDIO, ALTO)" in result.output


def test_catalog_show_muestra_exigido_y_alternativa_por_separado():
    result = runner.invoke(app, ["catalog", "show", "mp.s.2"])

    assert result.exit_code == 0, result.output
    assert "(exigido en: ALTA; alternativa en: BASICA, MEDIA)" in result.output


def test_catalog_show_imprime_el_texto_introductorio_del_grupo_y_del_subgrupo():
    result = runner.invoke(app, ["catalog", "show", "op.ext.1"])

    assert result.exit_code == 0, result.output
    assert "op.ext  Recursos externos" in result.output
    assert "mantendrá la plena responsabilidad de los riesgos" in result.output
    assert "El marco operacional está constituido por las medidas" in result.output


def test_catalog_list_empieza_por_org_1_y_termina_por_mp():
    result = runner.invoke(app, ["catalog", "list"])

    ids = [
        line.split()[0] for line in result.output.splitlines() if line[:3] in ("org", "op.", "mp.")
    ]
    assert ids[0] == "org.1"
    assert ids[-1].startswith("mp.")
    assert ids.index("op.pl.1") < ids.index("mp.if.1")


def test_catalog_show_de_id_inexistente_falla_con_mensaje_en_espanol():
    result = runner.invoke(app, ["catalog", "show", "xx.nada.9"])

    assert result.exit_code == 1
    assert "xx.nada.9 no está en el catálogo" in result.output
