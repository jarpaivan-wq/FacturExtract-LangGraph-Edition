from datetime import date

import pytest

from facturextract.modelo import DatosInvalidos, normalizar_campos, parse_entero, parse_fecha
from facturextract.rut import calcular_dv, formatear_rut, rut_valido
from facturextract.validacion import (bloqueantes, construir_subgrafo, v01_presentes, v02_rut,
                                      v03_fecha_emision, v04_vencimiento, v05_suma_total, v06_iva,
                                      v07_suma_lineas, v08_por_linea, validar, validar_todo)
from tests.factura_ejemplo import factura

HOY = date(2026, 10, 12)
NUNCA = lambda *a: False


def test_rut_modulo11():
    assert calcular_dv("12345678") == "5"
    assert rut_valido("12.345.678-5") and rut_valido("123456785")
    assert not rut_valido("12.345.678-4")
    assert not rut_valido("") and not rut_valido(None) and not rut_valido("abc")
    assert formatear_rut("123456785") == "12.345.678-5"


def test_dv_k_y_cero():
    vistos = set()
    for cuerpo in range(1, 500):
        dv = calcular_dv(str(cuerpo))
        vistos.add(dv)
        assert rut_valido(f"{cuerpo}{dv}")
    assert {"K", "0"} <= vistos


def test_normalizacion():
    assert parse_entero("$1.234.567") == 1234567
    assert parse_fecha("05-10-2026").isoformat() == "2026-10-05"
    with pytest.raises(DatosInvalidos):
        parse_fecha("32-13-2026")
    with pytest.raises(DatosInvalidos):
        parse_entero("abc")
    assert normalizar_campos({"folio": "000123"})["folio"] == "123"
    assert normalizar_campos({"pago_sugerido": "cheque"})["pago_sugerido"] == "cheque"
    assert normalizar_campos({"pago_sugerido": "efectivo"})["pago_sugerido"] is None   # solo 3 métodos + pendiente


def test_factura_valida_sin_hallazgos():
    assert validar_todo(factura(), HOY, NUNCA) == []


def test_v01_presentes():
    c = factura(folio=None, rut_emisor=None, rut_receptor=None, fecha_emision=None, total=0, lineas=[])
    assert len(v01_presentes(c)) == 6


def test_v02_rut():
    c = factura(rut_emisor="11.111.111-2", rut_receptor="12.345.678-4")
    assert len(v02_rut(c)) == 2
    assert not v02_rut(factura(rut_emisor=None))      # la ausencia es V01


def test_v03_fecha():
    assert v03_fecha_emision(factura(fecha_emision="2026-10-13"), HOY)
    assert not v03_fecha_emision(factura(fecha_emision="2026-10-12"), HOY)
    c = factura(fecha_emision="2026-10-05")
    assert not v03_fecha_emision(c, date(2026, 10, 12))
    assert v03_fecha_emision(c, date(2026, 10, 1))   # "hoy" inyectado


def test_v04_vencimiento():
    h = v04_vencimiento(factura(fecha_vencimiento=None))                  # sugerencia pendiente: advertencia
    assert h and h[0]["gravedad"] == "advertencia"
    assert not v04_vencimiento(factura(fecha_vencimiento=None, pago_sugerido="contado"))
    assert not v04_vencimiento(factura(fecha_vencimiento=None, pago_sugerido="transferencia"))
    h = v04_vencimiento(factura(fecha_vencimiento="2026-10-01"))          # anterior a la emisión: bloqueante
    assert h and h[0]["gravedad"] == "bloqueante"
    assert not v04_vencimiento(factura(tipo_documento="nota_credito", fecha_vencimiento=None))
    assert not v04_vencimiento(factura(tipo_documento="guia_despacho", fecha_vencimiento=None))


def test_vencimiento_ausente_no_dispara_reintentos():
    from facturextract.validacion import bloqueantes
    assert bloqueantes(v04_vencimiento(factura(fecha_vencimiento=None))) == []


def test_v05_tolerancia_un_peso():
    assert not v05_suma_total(factura(total=1190001))
    assert v05_suma_total(factura(total=1190002))
    assert not v05_suma_total(factura(monto_exento=500, total=1190500))
    assert not v05_suma_total(factura(otros_impuestos=1000, total=1191000))


def test_v06_iva_solo_afectas():
    assert v06_iva(factura(iva=150000, total=1150000))
    assert not v06_iva(factura(iva=190001, total=1190001))
    exenta = factura(tipo_documento="factura_exenta", monto_neto=0, monto_exento=1000000, iva=0, total=1000000)
    assert validar_todo(exenta, HOY, NUNCA) == []


def test_v07_suma_lineas():
    assert v07_suma_lineas(factura(lineas=[
        {"cantidad": 1, "detalle": "x", "precio_unitario": 1, "descuento": 0, "total_linea": 1}]))
    assert not v07_suma_lineas(factura())


def test_v08_advertencia_por_linea():
    c = factura()
    c["lineas"][0]["precio_unitario"] = 1
    h = v08_por_linea(c)
    assert h and h[0]["gravedad"] == "advertencia"
    assert bloqueantes(h) == []


def test_nota_credito_con_otros_impuestos():
    nc = factura(tipo_documento="nota_credito", fecha_vencimiento=None, otros_impuestos=100000,
                  nombre_otros_impuestos="Impuesto específico", total=1290000)
    assert validar_todo(nc, HOY, NUNCA) == []


def test_subgrafo_ok_hallazgos_y_duplicada():
    sg = construir_subgrafo(NUNCA)
    assert validar(sg, factura(), HOY) == ([], "ok")
    h, v = validar(sg, factura(iva=1, total=1000001), HOY)
    assert v == "con_hallazgos" and any(x["codigo"] == "V06" for x in h)
    sg2 = construir_subgrafo(lambda rut, folio, tipo: True)
    assert validar(sg2, factura(), HOY)[1] == "duplicada"


def test_subgrafo_solo_advertencias_es_ok():
    sg = construir_subgrafo(NUNCA)
    c = factura()
    c["lineas"][0]["precio_unitario"] = 1                       # solo V08 (advertencia)
    h, v = validar(sg, c, HOY)
    assert v == "ok" and h and all(x["gravedad"] == "advertencia" for x in h)


def test_subgrafo_no_acumula_entre_corridas():
    sg = construir_subgrafo(NUNCA)
    validar(sg, factura(iva=1, total=1000001), HOY)
    assert validar(sg, factura(), HOY) == ([], "ok")
