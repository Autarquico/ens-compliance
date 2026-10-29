"""HOST-004: cifrado del volumen raíz."""

from ens_evidence.models.enums import CheckStatus

from .check_data import evaluate, host_evidence

PASS, FAIL, UNKNOWN = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN
ROOT = "/dev/mapper/vg-root"


def linux(*disks, root=None):
    rows = {"disk_encryption": list(disks)}
    if root is not None:
        rows["root_mount"] = root
    return evaluate(host_evidence("linux", rows=rows))["HOST-004"]


def disk(name, encrypted="1", **extra):
    return {"name": name, "encrypted": encrypted, **extra}


def test_linux_con_la_raiz_cifrada_pasa():
    assert linux(disk(ROOT)).status is PASS


def test_linux_con_la_raiz_sin_cifrar_falla():
    assert linux(disk(ROOT, "0")).status is FAIL


def test_un_disco_no_relacionado_cifrado_no_salva_una_raiz_sin_cifrar():
    result = linux(disk("/dev/sdb1"), disk(ROOT, "0"))

    assert result.status is FAIL


def test_un_disco_no_relacionado_cifrado_no_cuenta_si_la_raiz_no_aparece():
    assert linux(disk("/dev/sdb1")).status is UNKNOWN


def test_la_raiz_se_casa_por_el_alias_del_dispositivo():
    root = [{"device": "/dev/dm-0", "device_alias": ROOT, "path": "/"}]

    assert linux(disk(ROOT), root=root).status is PASS


def test_estado_undefined_es_unknown():
    assert linux(disk(ROOT, encryption_status="undefined")).status is UNKNOWN


def test_dos_filas_para_la_raiz_es_un_mapeo_ambiguo():
    assert linux(disk(ROOT), disk(ROOT, "0")).status is UNKNOWN


def test_valor_de_cifrado_no_interpretable_es_unknown():
    assert linux(disk(ROOT, "")).status is UNKNOWN


def test_sin_filas_de_cifrado_es_unknown():
    assert linux().status is UNKNOWN


def test_sin_volumen_raiz_identificable_es_unknown():
    assert linux(disk(ROOT), root=[]).status is UNKNOWN
    assert linux(disk(ROOT), root=[{"device": ROOT, "path": "/home"}]).status is UNKNOWN


def mac(*, filevault, disks):
    rows = {"filevault_status": [{"filevault_status": filevault}], "disk_encryption": disks}
    return evaluate(host_evidence(rows=rows))["HOST-004"]


def test_macos_con_filevault_activo_y_disco_cifrado_pasa():
    result = mac(filevault="on", disks=[disk("/dev/disk3s1")])

    assert result.status is PASS
    assert "FileVault" in result.reason


def test_macos_con_filevault_desactivado_y_disco_sin_cifrar_falla():
    assert mac(filevault="off", disks=[disk("/dev/disk3s1", "0")]).status is FAIL


def test_macos_ignora_un_disco_ajeno_cifrado_al_juzgar_la_raiz():
    disks = [disk("/dev/disk3s1", "0"), disk("/dev/disk5s1")]

    assert mac(filevault="off", disks=disks).status is FAIL


def test_macos_cifrado_por_hardware_sin_filevault_no_prueba_cifrado():
    assert mac(filevault="off", disks=[disk("/dev/disk3s1")]).status is UNKNOWN


def test_macos_con_filevault_y_disco_sin_cifrar_no_coinciden_y_es_unknown():
    assert mac(filevault="on", disks=[disk("/dev/disk3s1", "0")]).status is UNKNOWN


def test_macos_con_filevault_pero_solo_discos_ajenos_es_unknown():
    assert mac(filevault="on", disks=[disk("/dev/disk9s1")]).status is UNKNOWN


def test_macos_con_estado_de_filevault_no_interpretable_o_sin_filas_es_unknown():
    assert mac(filevault="quizá", disks=[disk("/dev/disk3s1")]).status is UNKNOWN
    rows = {"filevault_status": []}
    assert evaluate(host_evidence(rows=rows))["HOST-004"].status is UNKNOWN


def test_macos_con_encryption_status_undefined_es_unknown():
    disks = [disk("/dev/disk3s1", encryption_status="undefined")]

    assert mac(filevault="on", disks=disks).status is UNKNOWN


def test_macos_con_valor_de_filevault_adivinado_es_unknown_y_el_motivo_lo_dice():
    for guessed in ("FileVault is On.", "enabled", "1", "true"):
        result = mac(filevault=guessed, disks=[disk("/dev/disk3s1")])

        assert result.status is UNKNOWN
        assert "no reconocido" in result.reason
        assert guessed.casefold() in result.reason


def test_macos_con_filas_de_filevault_contradictorias_es_unknown():
    rows = {"filevault_status": [{"filevault_status": "on"}, {"filevault_status": "off"}]}

    assert evaluate(host_evidence(rows=rows))["HOST-004"].status is UNKNOWN
