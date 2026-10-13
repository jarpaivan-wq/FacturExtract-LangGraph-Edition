import sys
import uuid
from pathlib import Path

import pytest

import lanzador
from facturextract import residente


@pytest.mark.skipif(sys.platform != "win32", reason="registro de Windows")
def test_inicio_con_windows_activar_y_desactivar():
    nombre = "FacturExtractPrueba_" + uuid.uuid4().hex[:6]
    try:
        assert not residente.inicio_activo(nombre)
        assert residente.activar_inicio('"C:/x/pythonw.exe" "C:/x/FacturExtract.pyw" --oculto', nombre)
        assert residente.inicio_activo(nombre)
    finally:
        residente.desactivar_inicio(nombre)
    assert not residente.inicio_activo(nombre)


def test_comando_de_inicio_usa_pythonw_y_oculto():
    c = residente.comando_inicio(Path("C:/app"))
    assert "pythonw" in c.lower() and c.endswith("--oculto") and "FacturExtract.pyw" in c


def test_parsear_py_list():
    salida = " -V:3.13 *        C:/Python313/python.exe\n -V:3.10          C:/Python310/python.exe\n"
    assert lanzador.parsear_py_list(salida) == ["C:/Python313/python.exe", "C:/Python310/python.exe"]


def test_elegir_python_version_minima():
    vers = {"a": (3, 9), "b": (3, 11), "c": (3, 13), "d": (3, 14), "e": None}
    assert lanzador.elegir_python(["a", "e"], vers.get) is None                      # ninguno sirve
    assert lanzador.elegir_python(["a", "b"], vers.get) == ("b", (3, 11))
    assert lanzador.elegir_python(["b", "c", "d"], vers.get) == ("c", (3, 13))       # prefiere hasta 3.13
    assert lanzador.elegir_python(["d"], vers.get) == ("d", (3, 14))                 # si solo hay uno nuevo, sirve


def test_sin_python_adecuado_mensaje_claro(tmp_path, monkeypatch):
    monkeypatch.setattr(lanzador, "listar_candidatos", lambda *a, **k: ["x"])
    monkeypatch.setattr(lanzador, "version_de", lambda *a, **k: (3, 8))
    with pytest.raises(lanzador.ErrorLanzador, match="3.10"):
        lanzador.preparar_entorno(tmp_path, tmp_path / "log.txt")


def test_huella_cambia_con_requisitos_o_python():
    a = lanzador.huella_requisitos(lanzador.APP, (3, 13))
    assert a == lanzador.huella_requisitos(lanzador.APP, (3, 13))
    assert a != lanzador.huella_requisitos(lanzador.APP, (3, 12))


def test_env_y_clave(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / "d" / ".env"
    assert not lanzador.hay_clave(env)
    assert not lanzador.clave_con_forma_valida("hola")
    falsa = "sk-" + "x" * 30
    assert lanzador.clave_con_forma_valida(falsa)
    lanzador.escribir_env(env, falsa)
    assert lanzador.hay_clave(env)
