"""RUT chileno: normalización y dígito verificador (módulo 11)."""
from __future__ import annotations

import re


def limpiar_rut(rut: str | None) -> str:
    """Quita puntos, guiones y espacios; deja 'cuerpoDV' en mayúsculas."""
    if not rut:
        return ""
    return re.sub(r"[^0-9kK]", "", str(rut)).upper()


def calcular_dv(cuerpo: str) -> str:
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    if resto == 11:
        return "0"
    if resto == 10:
        return "K"
    return str(resto)


def rut_valido(rut: str | None) -> bool:
    limpio = limpiar_rut(rut)
    if len(limpio) < 2:
        return False
    cuerpo, dv = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit() or int(cuerpo) == 0:
        return False
    return calcular_dv(cuerpo) == dv


def formatear_rut(rut: str | None) -> str:
    """12345678K -> 12.345.678-K. Si no se puede interpretar, devuelve el texto original."""
    limpio = limpiar_rut(rut)
    if len(limpio) < 2 or not limpio[:-1].isdigit():
        return (rut or "").strip()
    cuerpo = f"{int(limpio[:-1]):,}".replace(",", ".")
    return f"{cuerpo}-{limpio[-1]}"
