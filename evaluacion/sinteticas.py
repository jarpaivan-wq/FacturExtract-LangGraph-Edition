"""Generador de facturas SINTÉTICAS (datos inventados) para evaluar el modelo de visión.

Cada semilla produce, de forma determinista, los campos esperados y una imagen degradada
(perspectiva, desenfoque, iluminación, ruido, compresión). Nunca se usan facturas reales.
"""
from __future__ import annotations

import io
import random
from datetime import date, timedelta

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from facturextract.modelo import normalizar_campos
from facturextract.rut import calcular_dv, formatear_rut

EMISORES = ["COMERCIAL ANDES LTDA", "AGRICOLA LOS LAGOS S.A.", "FERRETERIA EL MARTILLO SPA", "DISTRIBUIDORA SUR S.A.",
            "TRANSPORTES PATAGONIA LTDA", "IMPORTADORA PACIFICO SPA", "SERVICIOS INDUSTRIALES NORTE S.A."]
RECEPTORES = ["SOCIEDAD AGRICOLA DEL VALLE LTDA", "INVERSIONES LA ROCA SPA", "CONSTRUCTORA OHIGGINS S.A.",
              "COMERCIAL PEREZ Y CIA LTDA"]
PRODUCTOS = ["TORNILLO 1/4 X 2", "CEMENTO SACO 25KG", "ALAMBRE GALVANIZADO", "PINTURA LATEX 4L", "GUANTES DE CUERO",
             "FERTILIZANTE UREA 50KG", "MANGUERA 1/2 PULG", "ACEITE HIDRAULICO 20L", "FILTRO DE AIRE", "CADENA 3/8",
             "TABLA PINO 2X4", "BOMBA DE AGUA 1HP"]
MEDIOS = ["Crédito", "Cheque", "Transferencia", "Contado"]


def _rut(rng: random.Random) -> str:
    cuerpo = str(rng.randint(5_000_000, 25_000_000))
    return formatear_rut(cuerpo + calcular_dv(cuerpo))


def datos_esperados(semilla: int) -> dict:
    rng = random.Random(semilla)
    emision = date(2026, 10, 1) + timedelta(days=rng.randint(0, 10))
    medio = rng.choice(MEDIOS)
    venc = None if medio == "Contado" else emision + timedelta(days=rng.choice([15, 30, 45]))
    lineas = []
    for detalle in rng.sample(PRODUCTOS, rng.randint(1, 6)):
        cantidad, precio = rng.randint(1, 40), rng.choice(range(500, 90_000, 500))
        lineas.append({"cantidad": cantidad, "detalle": detalle, "precio_unitario": precio, "descuento": 0,
                       "total_linea": cantidad * precio})
    neto = sum(ln["total_linea"] for ln in lineas)
    iva = round(neto * 0.19)
    return normalizar_campos({
        "tipo_documento": "factura", "folio": str(rng.randint(1000, 99999)),
        "rut_emisor": _rut(rng), "razon_social_emisor": rng.choice(EMISORES),
        "rut_receptor": _rut(rng), "razon_social_receptor": rng.choice(RECEPTORES),
        "fecha_emision": emision.isoformat(), "fecha_vencimiento": venc.isoformat() if venc else None,
        "medio_pago": medio, "pago_sugerido": "contado" if medio == "Contado" else "pendiente",
        "monto_neto": neto, "monto_exento": 0, "iva": iva, "otros_impuestos": 0,
        "total": neto + iva, "observaciones": None, "lineas": lineas})


def _fuente(tam: int):
    for nombre in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def _m(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _f(iso: str | None) -> str:
    return f"{date.fromisoformat(iso):%d-%m-%Y}" if iso else ""


def dibujar(c: dict) -> Image.Image:
    im = Image.new("RGB", (1000, 800 + 36 * len(c["lineas"])), "white")
    d = ImageDraw.Draw(im)
    g, n, p = _fuente(30), _fuente(22), _fuente(18)
    d.rectangle((620, 20, 980, 150), outline="red", width=4)
    d.text((640, 30), f"R.U.T.: {c['rut_emisor']}", fill="red", font=n)
    d.text((640, 70), {"nota_credito": "NOTA DE CREDITO ELECTRONICA"}.get(c["tipo_documento"], "FACTURA ELECTRONICA"), fill="red", font=n if c["tipo_documento"] != "nota_credito" else p)
    d.text((640, 110), f"N° {c['folio']}", fill="red", font=g)
    d.text((30, 30), c["razon_social_emisor"], fill="black", font=g)
    d.text((30, 80), "Giro: venta de productos varios", fill="black", font=p)
    d.text((30, 180), f"Fecha Emision: {_f(c['fecha_emision'])}", fill="black", font=n)
    if c["fecha_vencimiento"]:
        d.text((480, 180), f"Fecha Vencimiento: {_f(c['fecha_vencimiento'])}", fill="black", font=n)
    d.text((30, 225), f"SEÑOR(ES): {c['razon_social_receptor']}", fill="black", font=n)
    d.text((30, 260), f"R.U.T.: {c['rut_receptor']}", fill="black", font=n)
    d.text((30, 295), f"Forma de pago: {c['medio_pago']}", fill="black", font=n)
    y = 360
    d.rectangle((20, y, 980, y + 34), fill=(225, 225, 225))
    con_dto = any(ln["descuento"] for ln in c["lineas"])
    for x, t in ((30, "Cant."), (130, "Descripcion"), (560, "Precio"), (690, "Dto."), (800, "Total")):
        if t != "Dto." or con_dto:
            d.text((x, y + 6), t, fill="black", font=p)
    for ln in c["lineas"]:
        y += 36
        d.text((30, y + 6), str(int(ln["cantidad"])), fill="black", font=p)
        d.text((130, y + 6), ln["detalle"], fill="black", font=p)
        d.text((560, y + 6), _m(ln["precio_unitario"]), fill="black", font=p)
        if con_dto and ln["descuento"]:
            d.text((690, y + 6), _m(ln["descuento"]), fill="black", font=p)
        d.text((800, y + 6), _m(ln["total_linea"]), fill="black", font=p)
    y += 70
    resumen = [("Monto Neto", c["monto_neto"]), ("IVA 19%", c["iva"])]
    if c["otros_impuestos"]:
        resumen.append(("Impuesto adicional", c["otros_impuestos"]))
    resumen.append(("TOTAL", c["total"]))
    for rot, v in resumen:
        d.text((620, y), rot, fill="black", font=n if rot != "TOTAL" else g)
        d.text((840, y), _m(v), fill="black", font=n if rot != "TOTAL" else g)
        y += 42
    return im


def degradar(im: Image.Image, semilla: int) -> Image.Image:
    rng = random.Random(semilla * 7919)
    w, h = im.size
    j = lambda m: rng.uniform(-m, m)
    quad = (j(25), j(25), j(25), h + j(25), w + j(25), h + j(25), w + j(25), j(25))   # perspectiva leve
    fondo = Image.new("RGB", (w, h), (180, 180, 175))
    t = im.transform((w, h), Image.Transform.QUAD, quad, resample=Image.Resampling.BICUBIC, fillcolor=(180, 180, 175))
    fondo.paste(t)
    im = fondo.rotate(rng.uniform(-3, 3), expand=False, fillcolor=(180, 180, 175))
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.3)))
    luz = Image.linear_gradient("L").resize((w, h)).rotate(rng.choice([0, 90, 180, 270]))
    sombra = Image.merge("RGB", [luz.point(lambda v: 170 + v // 3)] * 3)
    im = ImageChops.multiply(im, sombra)
    return im


def generar(semilla: int) -> tuple[dict, Image.Image]:
    c = datos_esperados(semilla)
    return c, degradar(dibujar(c), semilla)


def a_jpeg(im: Image.Image, calidad: int = 70) -> bytes:
    b = io.BytesIO()
    im.save(b, format="JPEG", quality=calidad)
    return b.getvalue()
