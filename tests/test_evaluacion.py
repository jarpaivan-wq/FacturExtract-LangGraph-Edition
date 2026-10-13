from datetime import date

from evaluacion import evaluadores, sinteticas
from facturextract.validacion import validar_todo


def test_sinteticas_son_deterministas_y_validas():
    a, b = sinteticas.datos_esperados(3), sinteticas.datos_esperados(3)
    assert a == b and a != sinteticas.datos_esperados(4)
    for semilla in range(1, 40):
        c = sinteticas.datos_esperados(semilla)
        assert validar_todo(c, date(2026, 10, 12), lambda *x: False) == [], semilla


def test_imagen_se_genera_y_degrada():
    c, im = sinteticas.generar(5)
    assert im.size[0] == 1000 and im.size[1] > 700
    assert len(sinteticas.a_jpeg(im)) > 5000


def test_evaluadores_exacto_aproximado_y_lineas():
    esp = sinteticas.datos_esperados(1)
    assert evaluadores.campos_exactos_correctos(evaluadores.evaluar(esp, esp))
    mal = dict(esp, folio="1", razon_social_emisor=esp["razon_social_emisor"].lower() + "x",
               lineas=esp["lineas"][:-1] if len(esp["lineas"]) > 1 else esp["lineas"] * 2)
    r = evaluadores.evaluar(esp, mal)
    assert r["exacto_folio"] == 0.0 and r["cantidad_lineas"] == 0.0
    assert 0.85 < r["aprox_razon_social_emisor"] < 1.0
    assert evaluadores.similitud("Cañería 1/2", "CANERIA 1/2") == 1.0
    assert not evaluadores.campos_exactos_correctos(r)


def test_resumen_promedia():
    r = evaluadores.resumen([{"a": 1.0}, {"a": 0.0}])
    assert r == {"a": 0.5}
