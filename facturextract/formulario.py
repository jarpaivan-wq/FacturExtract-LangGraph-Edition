"""Conversión entre los campos de una factura y el formulario de revisión (sin Tkinter, probable)."""
from __future__ import annotations

from datetime import date
from typing import Any

from .modelo import DatosInvalidos, TIPOS_DOCUMENTO, parse_fecha

CAMPOS_FORMULARIO = [
    ("tipo_documento", "Tipo de documento"), ("folio", "Folio"),
    ("rut_emisor", "RUT emisor"), ("razon_social_emisor", "Razón social emisor"),
    ("rut_receptor", "RUT receptor"), ("razon_social_receptor", "Razón social receptor"),
    ("fecha_emision", "Fecha de emisión"), ("fecha_vencimiento", "Fecha de vencimiento"),
    ("medio_pago", "Medio de pago"), ("monto_neto", "Monto neto"), ("monto_exento", "Monto exento"),
    ("iva", "IVA"), ("otros_impuestos", "Otros impuestos"),
    ("nombre_otros_impuestos", "Nombre del impuesto adicional"), ("total", "Total"),
    ("observaciones", "Observaciones"),
]
CAMPOS_FECHA = ("fecha_emision", "fecha_vencimiento")
ENCABEZADO_LINEAS = "cantidad | detalle | precio unitario | descuento | total línea"


def fecha_a_texto(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        f = parse_fecha(iso)
        return f"{f:%d-%m-%Y}" if f else ""
    except DatosInvalidos:
        return str(iso)


def numero_a_texto(n: Any) -> str:
    if n is None:
        return ""
    return str(int(n)) if float(n) == int(n) else str(n)


def campos_a_formulario(campos: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for clave, _ in CAMPOS_FORMULARIO:
        v = campos.get(clave)
        if clave in CAMPOS_FECHA:
            out[clave] = fecha_a_texto(v)
        else:
            out[clave] = "" if v is None else str(v)
    return out


def lineas_a_texto(lineas: list[dict]) -> str:
    return "\n".join(
        f"{numero_a_texto(ln['cantidad'])} | {ln['detalle'] or ''} | {numero_a_texto(ln['precio_unitario'])} | "
        f"{numero_a_texto(ln['descuento'])} | {numero_a_texto(ln['total_linea'])}" for ln in lineas)


def texto_a_lineas(texto: str) -> list[dict]:
    lineas = []
    for n, fila in enumerate((t for t in texto.splitlines() if t.strip()), start=1):
        partes = [p.strip() for p in fila.split("|")]
        if len(partes) != 5:
            raise DatosInvalidos(f"La línea {n} debe tener 5 datos separados por «|»: {ENCABEZADO_LINEAS}.")
        lineas.append({"cantidad": partes[0], "detalle": partes[1], "precio_unitario": partes[2],
                       "descuento": partes[3], "total_linea": partes[4]})
    return lineas


def formulario_a_campos(valores: dict[str, str], texto_lineas: str, base: dict | None = None) -> dict:
    """Devuelve campos crudos (el grafo los normaliza y, si no se entienden, vuelve a preguntar).
    `base` son los campos originales: lo que el formulario no muestra (la sugerencia de pago) se conserva."""
    campos: dict[str, Any] = {"pago_sugerido": (base or {}).get("pago_sugerido")}
    campos.update({k: (v.strip() or None) for k, v in valores.items()})
    campos["lineas"] = texto_a_lineas(texto_lineas)
    return campos


def opciones_tipo() -> list[str]:
    return list(TIPOS_DOCUMENTO)


def nombre_tipo(clave: str | None) -> str:
    return TIPOS_DOCUMENTO.get(clave or "", clave or "")


def miles(n: Any) -> str:
    if n is None or n == "":
        return ""
    return f"{int(n):,}".replace(",", ".")


def hoy_texto(d: date) -> str:
    return f"{d:%d-%m-%Y}"


def pago_texto(f: dict) -> str:
    """Texto del estado de pago de una factura guardada (vista de facturas)."""
    if f["estado_pago"] == "pagada":
        metodo = (f.get("metodo_pago") or "").capitalize()
        nro = f" N° {f['nro_documento']}" if f.get("nro_documento") else ""
        return f"Pagada · {metodo}{nro}".strip()
    return {"pendiente": "Pendiente", "no aplica": "No aplica"}.get(f["estado_pago"], f["estado_pago"] or "")
