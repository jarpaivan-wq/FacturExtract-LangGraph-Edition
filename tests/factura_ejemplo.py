"""Factura sintética válida para pruebas (RUTs con dígito verificador correcto, datos inventados)."""
import copy

from facturextract.modelo import normalizar_campos

BASE = {
    "tipo_documento": "factura",
    "rut_emisor": "11.111.111-1",   # ficticio; el dígito verificador se corrige abajo
    "razon_social_emisor": "PROVEEDOR DE PRUEBA S.A.",
    "rut_receptor": "22.222.222-2",   # ficticio
    "razon_social_receptor": "CLIENTE DE PRUEBA LTDA",
    "folio": "1001",
    "fecha_emision": "2026-10-05",
    "fecha_vencimiento": "2026-11-04",
    "medio_pago": "CHEQUE",
    "pago_sugerido": "pendiente",
    "monto_neto": 1000000, "monto_exento": 0, "iva": 190000, "otros_impuestos": 0, "total": 1190000,
    "observaciones": None,
    "lineas": [
        {"cantidad": 10, "detalle": "PRODUCTO A", "precio_unitario": 60000, "descuento": 0, "total_linea": 600000},
        {"cantidad": 4, "detalle": "PRODUCTO B", "precio_unitario": 100000, "descuento": 0, "total_linea": 400000},
    ],
}


def _con_dv(rut: str) -> str:
    from facturextract.rut import calcular_dv, limpiar_rut
    cuerpo = limpiar_rut(rut)[:-1]
    return cuerpo + calcular_dv(cuerpo)


def factura(**cambios):
    d = copy.deepcopy(BASE)
    d["rut_emisor"] = _con_dv(d["rut_emisor"])
    d["rut_receptor"] = _con_dv(d["rut_receptor"])
    d.update(cambios)
    return normalizar_campos(d)
