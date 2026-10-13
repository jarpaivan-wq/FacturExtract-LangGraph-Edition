"""Reglas de validación V01–V10 (solo código, sin modelo) y el subgrafo de validación.

Cada regla es una función pura que devuelve una lista de hallazgos. «Hoy» siempre llega
como parámetro (reloj inyectable). Los hallazgos bloqueantes hacen que la factura sea
«dudosa»; las advertencias no bloquean pero quedan visibles. La confianza que declare el
modelo no se usa.
"""
from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .modelo import DatosInvalidos, parse_fecha
from .rut import rut_valido

TASA_IVA = 0.19
TOLERANCIA_TOTAL = 1  # pesos
TIPOS_CON_VENCIMIENTO = {"factura", "factura_exenta"}
BLOQUEANTE, ADVERTENCIA = "bloqueante", "advertencia"

REGLAS = {
    "V01": "Presentes: folio, RUT emisor, RUT receptor, fecha de emisión, total y al menos una línea",
    "V02": "RUT con formato y dígito verificador válidos (módulo 11)",
    "V03": "Fecha de emisión válida y no futura",
    "V04": "Vencimiento no anterior a la emisión; se advierte si falta y el pago no está ya resuelto (el estado de pago lo decide el usuario)",
    "V05": "Neto + exento + IVA + otros impuestos = total (±1 peso)",
    "V06": "En facturas afectas, IVA cercano al 19% del neto",
    "V07": "La suma de las líneas coincide con neto + exento",
    "V08": "Por línea: cantidad × precio − descuento cercano al total de la línea (advertencia)",
    "V09": "(retirada: el estado de pago ya no se deduce del texto del medio de pago)",
    "V11": "Si hay otros impuestos mayores que cero, debe constar el nombre del impuesto adicional impreso",
    "V10": "Documento no registrado antes (mismo RUT emisor, folio y tipo)",
}


def _h(codigo: str, mensaje: str, campo: str | None = None, gravedad: str = BLOQUEANTE) -> dict[str, Any]:
    return {"codigo": codigo, "mensaje": mensaje, "campo": campo, "gravedad": gravedad}


def _fecha(valor: Any) -> date | None:
    try:
        return parse_fecha(valor)
    except DatosInvalidos:
        return None


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def v01_presentes(c: dict) -> list[dict]:
    faltan = []
    for campo, nombre in (("folio", "folio"), ("rut_emisor", "RUT del emisor"), ("rut_receptor", "RUT del receptor"),
                          ("fecha_emision", "fecha de emisión")):
        if not c.get(campo):
            faltan.append(_h("V01", f"Falta el dato: {nombre}.", campo))
    if not c.get("total"):
        faltan.append(_h("V01", "Falta el total.", "total"))
    if not c.get("lineas"):
        faltan.append(_h("V01", "No se encontró ninguna línea de detalle.", "lineas"))
    return faltan


def v02_rut(c: dict) -> list[dict]:
    out = []
    for campo, nombre in (("rut_emisor", "emisor"), ("rut_receptor", "receptor")):
        if c.get(campo) and not rut_valido(c[campo]):
            out.append(_h("V02", f"El RUT del {nombre} «{c[campo]}» no es válido.", campo))
    return out


def v03_fecha_emision(c: dict, hoy: date) -> list[dict]:
    texto = c.get("fecha_emision")
    if not texto:
        return []  # la ausencia ya la informa V01
    f = _fecha(texto)
    if f is None:
        return [_h("V03", "La fecha de emisión no es válida.", "fecha_emision")]
    if f > hoy:
        return [_h("V03", f"La fecha de emisión ({f:%d-%m-%Y}) es posterior a hoy.", "fecha_emision")]
    if f.year < 2000:
        return [_h("V03", f"La fecha de emisión ({f:%d-%m-%Y}) es demasiado antigua.", "fecha_emision")]
    return []


def v04_vencimiento(c: dict) -> list[dict]:
    """Un vencimiento ausente es solo una advertencia: la obligación real se exige al configurar el pago
    (una factura pendiente necesita vencimiento; una pagada con contado, transferencia o cheque no)."""
    texto = c.get("fecha_vencimiento")
    venc = _fecha(texto)
    if texto and venc is None:
        return [_h("V04", "La fecha de vencimiento no es válida.", "fecha_vencimiento")]
    if venc is None:
        if c.get("tipo_documento") in TIPOS_CON_VENCIMIENTO and c.get("pago_sugerido") in (None, "pendiente"):
            return [_h("V04", "No se leyó la fecha de vencimiento (se exigirá si la factura queda pendiente).",
                       "fecha_vencimiento", ADVERTENCIA)]
        return []
    emision = _fecha(c.get("fecha_emision"))
    if emision and venc < emision:
        return [_h("V04", "El vencimiento es anterior a la emisión.", "fecha_vencimiento")]
    return []


def v05_suma_total(c: dict) -> list[dict]:
    if c.get("tipo_documento") == "guia_despacho":
        return []
    suma = c["monto_neto"] + c["monto_exento"] + c["iva"] + c["otros_impuestos"]
    if abs(suma - c["total"]) > TOLERANCIA_TOTAL:
        return [_h("V05", f"Neto + exento + IVA + otros ({_miles(suma)}) no coincide con el total ({_miles(c['total'])}).",
                   "total")]
    return []


def v11_nombre_impuesto_adicional(c: dict) -> list[dict]:
    """Un impuesto adicional sin nombre impreso casi siempre es una partida inventada para cuadrar el total."""
    if (c.get("otros_impuestos") or 0) > 0 and not (c.get("nombre_otros_impuestos") or "").strip():
        return [_h("V11", f"Hay otros impuestos por {_miles(c['otros_impuestos'])} pero el documento no trae impreso "
                          "el nombre del impuesto adicional. Revise la imagen: puede ser una diferencia de totales "
                          "del propio documento.", "otros_impuestos")]
    return []


def v06_iva(c: dict) -> list[dict]:
    if c.get("tipo_documento") != "factura":
        return []
    esperado = round(c["monto_neto"] * TASA_IVA)
    if abs(c["iva"] - esperado) > max(2, 0.005 * esperado):
        return [_h("V06", f"El IVA ({_miles(c['iva'])}) no corresponde al 19% del neto (esperado {_miles(esperado)}).",
                   "iva")]
    return []


def v07_suma_lineas(c: dict) -> list[dict]:
    lineas = c.get("lineas") or []
    if c.get("tipo_documento") == "guia_despacho" or not lineas:
        return []  # sin líneas ya lo informa V01
    suma = sum(ln["total_linea"] for ln in lineas)
    esperado = c["monto_neto"] + c["monto_exento"]
    if abs(suma - esperado) > max(2, len(lineas)):
        return [_h("V07", f"La suma de las líneas ({_miles(suma)}) no coincide con neto + exento ({_miles(esperado)}).",
                   "lineas")]
    return []


def v08_por_linea(c: dict) -> list[dict]:
    out = []
    for i, ln in enumerate(c.get("lineas") or [], start=1):
        esperado = round(ln["cantidad"] * ln["precio_unitario"] - ln["descuento"])
        if abs(esperado - ln["total_linea"]) > max(2, 0.01 * abs(ln["total_linea"])):
            out.append(_h("V08", f"Línea {i}: cantidad × precio − descuento ({_miles(esperado)}) no coincide con "
                                 f"el total de la línea ({_miles(ln['total_linea'])}).", "lineas", ADVERTENCIA))
    return out


def v10_duplicado(c: dict, existe: Callable[[str, str, str], bool]) -> list[dict]:
    if c.get("rut_emisor") and c.get("folio") and existe(c["rut_emisor"], c["folio"], c["tipo_documento"]):
        return [_h("V10", f"Ya existe el documento {c['tipo_documento']} folio {c['folio']} de {c['rut_emisor']}.",
                   "folio")]
    return []


def bloqueantes(hallazgos: list[dict]) -> list[dict]:
    return [h for h in hallazgos if h.get("gravedad", BLOQUEANTE) == BLOQUEANTE]


def veredicto(hallazgos: list[dict]) -> str:
    """duplicada | con_hallazgos (hay bloqueantes) | ok (puede traer advertencias)."""
    if any(h["codigo"] == "V10" for h in hallazgos):
        return "duplicada"
    return "con_hallazgos" if bloqueantes(hallazgos) else "ok"


def validar_todo(c: dict, hoy: date, existe: Callable[[str, str, str], bool]) -> list[dict]:
    """Atajo sin grafo (pruebas y evaluadores): corre todas las reglas."""
    return (v01_presentes(c) + v02_rut(c) + v03_fecha_emision(c, hoy) + v04_vencimiento(c)
            + v05_suma_total(c) + v11_nombre_impuesto_adicional(c) + v06_iva(c) + v07_suma_lineas(c)
            + v08_por_linea(c) + v10_duplicado(c, existe))


# ---------------------------------------------------------------- subgrafo ----
class EstadoValidacion(TypedDict, total=False):
    campos: dict
    hoy: str
    hallazgos: Annotated[list, operator.add]
    veredicto: str


def _hoy(e: EstadoValidacion) -> date:
    return date.fromisoformat(e["hoy"])


def n_presencia(e: EstadoValidacion) -> dict:
    c = e["campos"]
    return {"hallazgos": v01_presentes(c) + v02_rut(c)}


def n_fechas(e: EstadoValidacion) -> dict:
    c = e["campos"]
    return {"hallazgos": v03_fecha_emision(c, _hoy(e)) + v04_vencimiento(c)}


def n_montos(e: EstadoValidacion) -> dict:
    c = e["campos"]
    return {"hallazgos": v05_suma_total(c) + v11_nombre_impuesto_adicional(c) + v06_iva(c) + v07_suma_lineas(c)}


def n_advertencias(e: EstadoValidacion) -> dict:
    c = e["campos"]
    return {"hallazgos": v08_por_linea(c)}


def construir_subgrafo(existe: Callable[[str, str, str], bool]):
    """El chequeo de duplicados recibe su consulta a la base por inyección."""

    def n_duplicado(e: EstadoValidacion) -> dict:
        return {"hallazgos": v10_duplicado(e["campos"], existe)}

    def n_veredicto(e: EstadoValidacion) -> dict:
        return {"veredicto": veredicto(e.get("hallazgos", []))}

    g = StateGraph(EstadoValidacion)
    g.add_node("presencia", n_presencia)
    g.add_node("fechas", n_fechas)
    g.add_node("montos", n_montos)
    g.add_node("advertencias", n_advertencias)
    g.add_node("duplicado", n_duplicado)
    g.add_node("veredicto", n_veredicto)
    g.add_edge(START, "presencia")
    g.add_edge("presencia", "fechas")
    g.add_edge("fechas", "montos")
    g.add_edge("montos", "advertencias")
    g.add_edge("advertencias", "duplicado")
    g.add_edge("duplicado", "veredicto")
    g.add_edge("veredicto", END)
    return g.compile()


def validar(subgrafo, campos: dict, hoy: date) -> tuple[list[dict], str]:
    out = subgrafo.invoke({"campos": campos, "hoy": hoy.isoformat(), "hallazgos": []})
    return out["hallazgos"], out["veredicto"]
