"""Los dos grafos de FacturExtract para LangGraph Studio (SOLO desarrollo; no forma parte de la entrega al cliente).

Studio pone su propio checkpointer, así que aquí se compilan sin él. La base de facturas y las carpetas de imágenes y
Excel son TEMPORALES (se borran al cerrar Studio): nunca se toca `datos/` de la aplicación.
El extractor es el real (modelo de visión): usa la ANTHROPIC_API_KEY del `.env` que carga langgraph.json.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from facturextract import config
from facturextract.config import Reloj, Rutas
from facturextract.db import Repo
from facturextract.excel import ExcelMensual
from facturextract.extractor import ExtractorClaude
from facturextract.grafo1 import Dependencias, construir_grafo1
from facturextract.grafo2 import construir_grafo2
from facturextract.modelo import normalizar_campos
from facturextract.rut import calcular_dv, formatear_rut


_COMPARTIDAS: dict[str, Dependencias] = {}


def _dependencias() -> Dependencias:
    """Una sola base/carpeta temporal por proceso: Studio vuelve a invocar la fábrica en cada reanudación de una
    pausa, y el run debe ver la misma factura registrada antes."""
    if "d" not in _COMPARTIDAS:
        _COMPARTIDAS["d"] = _crear_dependencias()
    return _COMPARTIDAS["d"]


def _crear_dependencias() -> Dependencias:
    tmp = Path(tempfile.mkdtemp(prefix="facturextract_studio_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    rutas = Rutas(tmp).crear()
    config.usar_almacen_windows()                       # certificados de Windows, nunca verify=False
    reloj = Reloj()
    repo = Repo(rutas.facturas_db)
    return Dependencias(repo, ExtractorClaude(), ExcelMensual(repo, rutas.excel, reloj), reloj, rutas)


def extraccion_facturas():
    """Grafo 1: lee una factura (extracción con visión), valida, reintenta con retroalimentación y pausa para revisión
    humana y para configurar el pago."""
    return construir_grafo1(_dependencias(), checkpointer=None)


def atencion_vencimientos():
    """Grafo 2: atención de un vencimiento. Se siembra en la base temporal una factura SINTÉTICA pendiente con su
    seguimiento (id 1) para poder usarlo desde Studio."""
    d = _dependencias()
    if d.repo.obtener("demo-factura"):          # ya sembrada en esta sesión de Studio
        return construir_grafo2(d, checkpointer=None)
    hoy = date.today()
    cuerpo = "11111111"
    campos = normalizar_campos({
        "tipo_documento": "factura", "folio": "900001", "rut_emisor": formatear_rut(cuerpo + calcular_dv(cuerpo)),
        "razon_social_emisor": "PROVEEDORA FICTICIA DE PRUEBA S.A.", "rut_receptor": "99.999.999-9",
        "razon_social_receptor": "CLIENTE DEMO PORTAFOLIO LTDA", "fecha_emision": (hoy - timedelta(days=20)).isoformat(),
        "fecha_vencimiento": (hoy + timedelta(days=2)).isoformat(), "medio_pago": "Crédito", "monto_neto": 100000,
        "iva": 19000, "total": 119000,
        "lineas": [{"cantidad": 1, "detalle": "SERVICIO DEMO", "precio_unitario": 100000, "descuento": 0,
                    "total_linea": 100000}]})
    d.repo.crear_factura("demo-factura", [], d.reloj.ahora())
    d.repo.guardar_factura("demo-factura", campos, [], hoy)
    return construir_grafo2(d, checkpointer=None)
