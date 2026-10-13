"""Evaluadores por campo: exacto (folio, RUT, fechas, montos) y aproximado (razón social, detalle) + líneas."""
from __future__ import annotations

from difflib import SequenceMatcher

from facturextract.modelo import sin_acentos
from facturextract.rut import limpiar_rut

CAMPOS_EXACTOS_TEXTO = ("folio", "fecha_emision", "fecha_vencimiento")
CAMPOS_RUT = ("rut_emisor", "rut_receptor")
CAMPOS_MONTO = ("monto_neto", "monto_exento", "iva", "otros_impuestos", "total")
UMBRAL_APROXIMADO = 0.85


def similitud(a: str | None, b: str | None) -> float:
    norm = lambda s: " ".join(sin_acentos(s or "").upper().split())
    a, b = norm(a), norm(b)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def evaluar(esperado: dict, obtenido: dict) -> dict[str, float]:
    """Devuelve {clave_evaluador: puntaje 0..1}."""
    r: dict[str, float] = {}
    for k in CAMPOS_EXACTOS_TEXTO:
        r[f"exacto_{k}"] = float((esperado.get(k) or None) == (obtenido.get(k) or None))
    for k in CAMPOS_RUT:
        r[f"exacto_{k}"] = float(limpiar_rut(esperado.get(k)) == limpiar_rut(obtenido.get(k)))
    for k in CAMPOS_MONTO:
        r[f"exacto_{k}"] = float(int(esperado.get(k) or 0) == int(obtenido.get(k) or 0))
    for k in ("razon_social_emisor", "razon_social_receptor"):
        r[f"aprox_{k}"] = similitud(esperado.get(k), obtenido.get(k))
    le, lo = esperado.get("lineas") or [], obtenido.get("lineas") or []
    r["cantidad_lineas"] = float(len(le) == len(lo))
    pares = list(zip(le, lo))
    r["aprox_detalle"] = (sum(similitud(a["detalle"], b["detalle"]) for a, b in pares) / max(len(le), len(lo))
                          if (le or lo) else 1.0)
    r["exacto_total_linea"] = (sum(int(a["total_linea"]) == int(b["total_linea"]) for a, b in pares)
                               / max(len(le), len(lo)) if (le or lo) else 1.0)
    return r


def resumen(resultados: list[dict[str, float]]) -> dict[str, float]:
    if not resultados:
        return {}
    claves = resultados[0].keys()
    return {k: sum(x[k] for x in resultados) / len(resultados) for k in claves}


def campos_exactos_correctos(r: dict[str, float]) -> bool:
    return all(v == 1.0 for k, v in r.items() if k.startswith("exacto_")) and r["cantidad_lineas"] == 1.0
