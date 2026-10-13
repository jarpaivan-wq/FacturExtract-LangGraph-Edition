"""Estado de pago de una factura, decidido por el usuario (reglas puras, «hoy» como parámetro).

Estados al registrar: pendiente (a crédito) o pagada con contado, transferencia o cheque.
Solo el cheque exige N° de documento. El método de pago no toca el vencimiento original ni las
prórrogas de la alerta: la fecha de vencimiento de la factura nunca cambia por pagar o posponer.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .modelo import METODOS_PAGO, TIPOS_CON_ALERTA, DatosInvalidos, parse_fecha

DIAS_ANTES_ALERTA = 3
ESTADOS_CONFIG = ("pendiente",) + METODOS_PAGO
ETIQUETAS = {"pendiente": "Pendiente (crédito)", "contado": "Pagada · contado",
             "transferencia": "Pagada · transferencia", "cheque": "Pagada · cheque"}
POR_ETIQUETA = {v: k for k, v in ETIQUETAS.items()}


def alerta_por_defecto(vencimiento_iso: str | None, hoy: date) -> date | None:
    """Vencimiento menos 3 días corridos; si ya pasó (vence en menos de 3 días o vencida), hoy."""
    try:
        venc = parse_fecha(vencimiento_iso)
    except DatosInvalidos:
        return None
    return max(venc - timedelta(days=DIAS_ANTES_ALERTA), hoy) if venc else None


def es_alertable(tipo_documento: str | None) -> bool:
    return tipo_documento in TIPOS_CON_ALERTA


def estado_inicial_sugerido(campos: dict) -> str:
    """Precarga de la fila: lo que sugirió el modelo; sin sugerencia, pendiente si hay vencimiento, si no contado."""
    s = campos.get("pago_sugerido")
    if s in ESTADOS_CONFIG:
        return s
    return "pendiente" if campos.get("fecha_vencimiento") else "contado"


def validar_configuracion(campos: dict, pago: Any, hoy: date) -> tuple[dict | None, str | None]:
    """Valida la decisión «configurar pago y alerta». Devuelve (pago_normalizado, None) o (None, mensaje).

    pago_normalizado = {estado_pago, metodo_pago, nro_documento, fecha_alerta}
    """
    if not es_alertable(campos.get("tipo_documento")):
        return {"estado_pago": "no aplica", "metodo_pago": None, "nro_documento": None, "fecha_alerta": None}, None
    if not isinstance(pago, dict) or pago.get("estado") not in ESTADOS_CONFIG:
        return None, "Elija el estado de pago: pendiente, o pagada con contado, transferencia o cheque."
    estado = pago["estado"]
    nro = str(pago.get("nro_documento") or "").strip() or None
    if estado == "cheque":
        if not nro:
            return None, "Un pago con cheque requiere el N° de documento."
        return {"estado_pago": "pagada", "metodo_pago": "cheque", "nro_documento": nro, "fecha_alerta": None}, None
    if estado in METODOS_PAGO:
        return {"estado_pago": "pagada", "metodo_pago": estado, "nro_documento": None, "fecha_alerta": None}, None
    # pendiente
    if not campos.get("fecha_vencimiento"):
        return None, ("Una factura pendiente necesita fecha de vencimiento: complétela en el detalle "
                      "o cambie el estado a pagada.")
    # Toda factura pendiente alerta, sin importar el mes de emisión (la única forma de no alertar es estar pagada).
    try:
        alerta = parse_fecha(pago.get("fecha_alerta")) or alerta_por_defecto(campos["fecha_vencimiento"], hoy)
    except DatosInvalidos as ex:
        return None, str(ex)
    if alerta is None:
        return None, "No se pudo calcular la fecha de alerta."
    if alerta < hoy:
        return None, "La fecha de alerta no puede ser anterior a hoy."
    return {"estado_pago": "pendiente", "metodo_pago": None, "nro_documento": None,
            "fecha_alerta": alerta.isoformat()}, None


def por_defecto(campos: dict, hoy: date) -> dict:
    """Configuración por defecto (pruebas y evaluación; en la aplicación decide siempre el usuario)."""
    estado = estado_inicial_sugerido(campos)
    pago, error = validar_configuracion(campos, {"estado": estado}, hoy)
    if pago is None:
        pago, _ = validar_configuracion(campos, {"estado": "contado"}, hoy)
    return pago
