from datetime import date

from facturextract import pago
from tests.factura_ejemplo import factura

HOY = date(2026, 10, 12)


def validar(c, **p):
    return pago.validar_configuracion(c, p, HOY)


def test_alerta_por_defecto_vencimiento_menos_3_dias_corridos():
    assert pago.alerta_por_defecto("2026-11-04", HOY) == date(2026, 11, 1)
    assert pago.alerta_por_defecto("2026-10-14", HOY) == HOY            # vence en menos de 3 días: de inmediato
    assert pago.alerta_por_defecto("2026-10-01", HOY) == HOY            # vencida: de inmediato
    assert pago.alerta_por_defecto(None, HOY) is None


def test_estado_inicial_es_solo_una_sugerencia():
    assert pago.estado_inicial_sugerido(factura(pago_sugerido="transferencia")) == "transferencia"
    assert pago.estado_inicial_sugerido(factura(pago_sugerido=None)) == "pendiente"                # hay vencimiento
    assert pago.estado_inicial_sugerido(factura(pago_sugerido=None, fecha_vencimiento=None)) == "contado"
    assert pago.estado_inicial_sugerido(factura(pago_sugerido="efectivo")) == "pendiente"           # no existe efectivo


def test_pendiente_valida_y_normaliza():
    p, e = validar(factura(), estado="pendiente", fecha_alerta="2026-10-20")
    assert e is None and p == {"estado_pago": "pendiente", "metodo_pago": None, "nro_documento": None,
                               "fecha_alerta": "2026-10-20"}
    p, e = validar(factura(), estado="pendiente")                                  # sin fecha: vence − 3 días
    assert p["fecha_alerta"] == "2026-11-01"


def test_alerta_no_puede_ser_anterior_a_hoy_pero_hoy_vale():
    assert "anterior a hoy" in validar(factura(), estado="pendiente", fecha_alerta="2026-10-11")[1]
    assert validar(factura(), estado="pendiente", fecha_alerta="2026-10-12")[1] is None


def test_pendiente_exige_vencimiento():
    p, e = validar(factura(fecha_vencimiento=None), estado="pendiente")
    assert p is None and "vencimiento" in e


def test_solo_cheque_exige_numero():
    assert validar(factura(), estado="cheque")[1] == "Un pago con cheque requiere el N° de documento."
    assert validar(factura(), estado="cheque", nro_documento="  ")[1] is not None
    p, _ = validar(factura(), estado="cheque", nro_documento=" 5512 ")
    assert (p["estado_pago"], p["metodo_pago"], p["nro_documento"]) == ("pagada", "cheque", "5512")
    for m in ("contado", "transferencia"):
        p, e = validar(factura(), estado=m, nro_documento="x")
        assert e is None and p["metodo_pago"] == m and p["nro_documento"] is None and p["fecha_alerta"] is None


def test_estados_invalidos():
    for malo in (None, "", "efectivo", "otro", "pagada"):
        assert validar(factura(), estado=malo)[1]
    assert pago.validar_configuracion(factura(), None, HOY)[1]
    assert pago.validar_configuracion(factura(), "pendiente", HOY)[1]


def test_solo_las_facturas_pueden_tener_pago_y_alerta():
    for tipo in ("nota_credito", "nota_debito", "guia_despacho", "otro"):
        p, e = validar(factura(tipo_documento=tipo), estado="pendiente")
        assert e is None and p["estado_pago"] == "no aplica" and p["fecha_alerta"] is None
    assert validar(factura(tipo_documento="factura_exenta"), estado="pendiente")[0]["fecha_alerta"]


def test_toda_factura_pendiente_alerta_sin_importar_el_mes_de_emision():
    p, e = validar(factura(fecha_emision="2026-09-20", fecha_vencimiento="2026-10-14"), estado="pendiente")
    assert e is None and p["estado_pago"] == "pendiente" and p["fecha_alerta"] == "2026-10-12"   # vence en 2 días: hoy
    p, _ = validar(factura(fecha_emision="2026-09-20", fecha_vencimiento="2026-10-30"), estado="pendiente")
    assert p["fecha_alerta"] == "2026-10-27"                                                     # venc - 3 días
    p, _ = validar(factura(fecha_emision="2026-08-01", fecha_vencimiento="2026-09-05"), estado="pendiente")
    assert p["fecha_alerta"] == "2026-10-12"                                                     # ya vencida: alerta hoy
    assert "anterior a hoy" in validar(factura(fecha_emision="2026-09-20"), estado="pendiente",
                                       fecha_alerta="2026-10-11")[1]                            # editable, desde hoy


def test_etiquetas_ida_y_vuelta():
    assert [pago.POR_ETIQUETA[pago.ETIQUETAS[k]] for k in pago.ESTADOS_CONFIG] == list(pago.ESTADOS_CONFIG)
