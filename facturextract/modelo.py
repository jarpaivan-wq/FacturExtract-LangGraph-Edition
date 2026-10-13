"""Modelo de dominio: forma de los datos extraídos y su normalización.

Los 'campos' viajan como dict JSON-serializable (así los guarda el checkpointer).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

TIPOS_DOCUMENTO = {
    "factura": "Factura",
    "factura_exenta": "Factura exenta",
    "nota_credito": "Nota de crédito",
    "nota_debito": "Nota de débito",
    "guia_despacho": "Guía de despacho",
    "otro": "Otro documento",
}
TIPOS_CON_ALERTA = {"factura", "factura_exenta"}
# El cliente maneja solo tres métodos de pago. El modelo solo SUGIERE el estado inicial; decide el usuario.
METODOS_PAGO = ("contado", "transferencia", "cheque")
PAGO_SUGERIDO = ("pendiente",) + METODOS_PAGO

CAMPOS_TEXTO = [
    "tipo_documento", "rut_emisor", "razon_social_emisor", "rut_receptor",
    "razon_social_receptor", "folio", "fecha_emision", "fecha_vencimiento",
    "medio_pago", "pago_sugerido", "nombre_otros_impuestos", "observaciones",
]
CAMPOS_MONTO = ["monto_neto", "monto_exento", "iva", "otros_impuestos", "total"]


class DatosInvalidos(ValueError):
    """Los datos no se pueden interpretar; el mensaje va al usuario en lenguaje normal."""


def campos_vacios() -> dict[str, Any]:
    c: dict[str, Any] = {k: None for k in CAMPOS_TEXTO}
    c.update({k: 0 for k in CAMPOS_MONTO})
    c["tipo_documento"] = "factura"
    c["lineas"] = []
    return c


def parse_entero(valor: Any, nombre: str = "monto") -> int:
    """'1.234.567' / '$ 1234' / 1234.0 -> 1234567. Vacío -> 0."""
    if valor is None or valor == "":
        return 0
    if isinstance(valor, bool):
        raise DatosInvalidos(f"El valor de {nombre} no es un número.")
    if isinstance(valor, (int, float)):
        return int(round(valor))
    texto = re.sub(r"[\s$]", "", str(valor)).replace(".", "").replace(",", ".")
    try:
        return int(round(float(texto)))
    except ValueError:
        raise DatosInvalidos(f"El valor «{valor}» de {nombre} no es un número.") from None


def parse_fecha(valor: Any) -> date | None:
    """Acepta AAAA-MM-DD o DD-MM-AAAA / DD/MM/AAAA. Vacío -> None. Inválida -> DatosInvalidos."""
    if valor is None or str(valor).strip() == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    raise DatosInvalidos(f"La fecha «{valor}» no es válida (use día-mes-año).")


def normalizar_folio(folio: Any) -> str:
    texto = re.sub(r"\D", "", str(folio or ""))
    return str(int(texto)) if texto else ""


def normalizar_campos(crudo: dict[str, Any]) -> dict[str, Any]:
    """Devuelve campos con tipos limpios. Lanza DatosInvalidos si algo no se puede interpretar."""
    from .rut import formatear_rut

    c = campos_vacios()
    for k in CAMPOS_TEXTO:
        v = crudo.get(k)
        c[k] = None if v is None or str(v).strip() == "" else str(v).strip()
    c["tipo_documento"] = c["tipo_documento"] if c["tipo_documento"] in TIPOS_DOCUMENTO else (
        c["tipo_documento"] or "factura")
    if c["pago_sugerido"] not in PAGO_SUGERIDO:
        c["pago_sugerido"] = None
    c["rut_emisor"] = formatear_rut(c["rut_emisor"]) or None
    c["rut_receptor"] = formatear_rut(c["rut_receptor"]) or None
    c["folio"] = normalizar_folio(c["folio"]) or None
    for k in ("fecha_emision", "fecha_vencimiento"):
        f = parse_fecha(c[k])
        c[k] = f.isoformat() if f else None
    for k in CAMPOS_MONTO:
        c[k] = parse_entero(crudo.get(k), k.replace("_", " "))
    lineas = []
    for i, ln in enumerate(crudo.get("lineas") or [], start=1):
        lineas.append({
            "cantidad": _num(ln.get("cantidad"), f"cantidad de la línea {i}"),
            "detalle": (str(ln.get("detalle") or "")).strip(),
            "precio_unitario": _num(ln.get("precio_unitario"), f"precio de la línea {i}"),
            "descuento": parse_entero(ln.get("descuento"), f"descuento de la línea {i}"),
            "total_linea": parse_entero(ln.get("total_linea"), f"total de la línea {i}"),
        })
    c["lineas"] = lineas
    return c


def _num(v: Any, nombre: str) -> float:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(re.sub(r"\s", "", str(v)).replace(",", "."))
    except ValueError:
        raise DatosInvalidos(f"El valor «{v}» de {nombre} no es un número.") from None


def sin_acentos(texto: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn")
