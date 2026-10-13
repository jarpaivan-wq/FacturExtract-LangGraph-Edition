"""Grafo 1: extracción de una factura (un run por factura, hilo = id de factura).

Reglas: un nodo, una acción; nada con efectos secundarios antes de un interrupt (el nodo con
interrupt no hace nada más); la imagen nunca viaja en el estado (solo su ruta); el modelo solo
extrae, el código valida, decide y guarda.
"""
from __future__ import annotations

import operator
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt

from . import pago as pago_mod
from . import validacion
from .config import Reloj, Rutas
from .db import Duplicada, Repo
from .excel import ExcelMensual
from .extractor import ErrorExtractor, ErrorTransitorio, Extractor
from .modelo import DatosInvalidos, normalizar_campos

MAX_REINTENTOS = 2  # reextracciones con retroalimentación antes de pasar a revisión humana
ACCIONES_REVISION = ("aprobar", "corregir", "rechazar")


@dataclass
class Dependencias:
    repo: Repo
    extractor: Extractor
    excel: ExcelMensual
    reloj: Reloj
    rutas: Rutas


class Estado1(TypedDict, total=False):
    factura_id: str
    rutas_imagen: list[str]
    campos: dict
    hallazgos: list[dict]
    veredicto: str
    intentos: int
    origen: str            # modelo | humano: quién produjo los campos vigentes
    decision: dict
    error_decision: str | None
    aprobada_manual: bool
    decision_pago: dict    # respuesta de la pausa «configurar pago y alerta»
    error_pago: str | None
    pago: dict             # configuración de pago ya validada
    resultado: str | None  # guardada | duplicada | rechazada | error
    mensaje: str | None
    mes: str
    eventos: Annotated[list[str], operator.add]


def abrir_checkpointer(ruta: Path) -> SqliteSaver:
    """Cada hilo de trabajo abre su propia conexión al checkpointer."""
    con = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(con)


def entrada_inicial(factura_id: str, rutas_imagen: list[str]) -> Estado1:
    return {"factura_id": factura_id, "rutas_imagen": list(rutas_imagen), "intentos": 0, "hallazgos": [],
            "campos": {}, "veredicto": "", "origen": "modelo", "decision": {}, "error_decision": None,
            "aprobada_manual": False, "decision_pago": {}, "error_pago": None, "pago": {}, "resultado": None, "mensaje": None, "eventos": []}


def construir_grafo1(d: Dependencias, checkpointer, politica: RetryPolicy | None = None):
    politica = politica or RetryPolicy(max_attempts=3, initial_interval=2.0, backoff_factor=2.0,
                                       retry_on=ErrorTransitorio)
    subgrafo = validacion.construir_subgrafo(d.repo.existe_guardada)

    def registrar(e: Estado1) -> dict:
        destino = d.rutas.imagenes / e["factura_id"]
        destino.mkdir(parents=True, exist_ok=True)
        copias = []
        for i, origen in enumerate(e["rutas_imagen"], start=1):
            origen_p = Path(origen)
            copia = destino / f"{i}{origen_p.suffix.lower()}"
            if origen_p.resolve() != copia.resolve():
                shutil.copy2(origen_p, copia)
            copias.append(str(copia))
        d.repo.crear_factura(e["factura_id"], copias, d.reloj.ahora())
        d.repo.actualizar_imagenes(e["factura_id"], copias)
        return {"rutas_imagen": copias, "eventos": ["registrada"]}

    def cargar_imagen(e: Estado1) -> dict:
        """Comprueba que las imágenes están en disco (la imagen nunca entra al estado)."""
        faltan = [Path(r).name for r in e["rutas_imagen"] if not Path(r).is_file()]
        if faltan:
            return {"resultado": "error", "mensaje": f"No se encontró la foto: {', '.join(faltan)}."}
        d.repo.set_estado(e["factura_id"], "extrayendo")
        return {"eventos": ["extrayendo"]}

    def extraer(e: Estado1) -> dict:
        previos = e.get("hallazgos") if e.get("intentos", 0) > 0 and e.get("origen") == "modelo" else None
        campos_previos = e.get("campos") if previos else None
        try:
            campos = d.extractor.extraer(e["rutas_imagen"], previos, campos_previos)
        except ErrorTransitorio:
            raise  # lo reintenta la política de LangGraph; agotada, la atiende quien invoca
        except ErrorExtractor as ex:  # permanente: sin reintento
            return {"resultado": "error", "mensaje": ex.mensaje}
        return {"campos": campos, "intentos": e.get("intentos", 0) + 1, "origen": "modelo",
                "eventos": [f"extraccion_{e.get('intentos', 0) + 1}"]}

    def validar(e: Estado1) -> dict:
        hallazgos, veredicto = validacion.validar(subgrafo, e["campos"], d.reloj.hoy())
        return {"hallazgos": hallazgos, "veredicto": veredicto, "eventos": [f"validacion_{veredicto}"]}

    def preparar_revision(e: Estado1) -> dict:
        d.repo.guardar_borrador(e["factura_id"], e["campos"], e["hallazgos"], "en_revision")
        return {"eventos": ["en_revision"]}

    def revision_humana(e: Estado1) -> dict:
        decision = interrupt({"tipo": "revision", "factura_id": e["factura_id"],
                              "error": e.get("error_decision")})
        return {"decision": decision}

    def validar_decision(e: Estado1) -> dict:
        dec = e.get("decision") or {}
        accion = dec.get("accion")
        if accion not in ACCIONES_REVISION:
            return {"error_decision": "Elija una acción: aprobar, corregir o rechazar."}
        if accion == "corregir":
            if not isinstance(dec.get("campos"), dict):
                return {"error_decision": "Para corregir hay que enviar los datos corregidos."}
            try:
                campos = normalizar_campos(dec["campos"])
            except DatosInvalidos as ex:
                return {"error_decision": str(ex)}
            return {"error_decision": None, "campos": campos, "origen": "humano", "aprobada_manual": False}
        return {"error_decision": None, "aprobada_manual": accion == "aprobar"}

    def preparar_confirmacion(e: Estado1) -> dict:
        d.repo.guardar_borrador(e["factura_id"], e["campos"], e["hallazgos"], "por_confirmar")
        return {"eventos": ["por_confirmar"]}

    def configurar_pago(e: Estado1) -> dict:
        decision = interrupt({"tipo": "configurar_pago", "factura_id": e["factura_id"],
                              "error": e.get("error_pago")})
        return {"decision_pago": decision}

    def validar_configuracion(e: Estado1) -> dict:
        dec = e.get("decision_pago") or {}
        accion = dec.get("accion")
        if accion == "rechazar":
            return {"error_pago": None}
        if accion != "confirmar":
            return {"error_pago": "Elija confirmar o rechazar."}
        pago, error = pago_mod.validar_configuracion(e["campos"], dec.get("pago"), d.reloj.hoy())
        if error:
            return {"error_pago": error}
        return {"error_pago": None, "pago": pago}

    def guardar(e: Estado1) -> dict:
        try:
            r = d.repo.guardar_factura(e["factura_id"], e["campos"], e.get("hallazgos", []), d.reloj.hoy(),
                                       bool(e.get("aprobada_manual")), e["pago"])
        except Duplicada:
            return {"resultado": "duplicada"}
        return {"resultado": "guardada", "mes": r["mes"], "eventos": ["guardada"]}

    def regenerar_excel(e: Estado1) -> dict:
        estado = d.excel.regenerar(e["mes"])
        if estado != "ok":
            return {"mensaje": "La factura se guardó, pero el Excel del mes está abierto o bloqueado; "
                               "se actualizará solo cuando se cierre.", "eventos": ["excel_desactualizado"]}
        return {"eventos": ["excel_ok"]}

    def marcar_duplicada(e: Estado1) -> dict:
        c = e.get("campos") or {}
        msg = f"Esta factura ya estaba registrada (folio {c.get('folio')} de {c.get('rut_emisor')}). No se guardó de nuevo."
        d.repo.set_estado(e["factura_id"], "duplicada", msg)
        return {"resultado": "duplicada", "mensaje": msg, "eventos": ["duplicada"]}

    def marcar_rechazada(e: Estado1) -> dict:
        d.repo.set_estado(e["factura_id"], "rechazada", "Rechazada por el usuario.")
        return {"resultado": "rechazada", "eventos": ["rechazada"]}

    def marcar_error(e: Estado1) -> dict:
        d.repo.set_estado(e["factura_id"], "error_reintentable", e.get("mensaje"))
        return {"resultado": "error", "eventos": ["error"]}

    # ---- aristas condicionales
    def tras_cargar(e: Estado1) -> str:
        return "marcar_error" if e.get("resultado") == "error" else "extraer"

    def tras_extraer(e: Estado1) -> str:
        return "marcar_error" if e.get("resultado") == "error" else "validar"

    def decidir_veredicto(e: Estado1) -> str:
        v = e["veredicto"]
        if v == "duplicada":
            return "marcar_duplicada"
        if v == "ok":
            return "preparar_confirmacion"
        if e.get("origen") == "modelo" and e.get("intentos", 0) <= MAX_REINTENTOS:
            return "extraer"
        return "preparar_revision"

    def tras_decision(e: Estado1) -> str:
        if e.get("error_decision"):
            return "revision_humana"
        accion = e["decision"]["accion"]
        return {"aprobar": "preparar_confirmacion", "corregir": "validar", "rechazar": "marcar_rechazada"}[accion]

    def tras_configuracion(e: Estado1) -> str:
        if e.get("error_pago"):
            return "configurar_pago"
        return "marcar_rechazada" if e["decision_pago"]["accion"] == "rechazar" else "guardar"

    def tras_guardar(e: Estado1) -> str:
        return "marcar_duplicada" if e.get("resultado") == "duplicada" else "regenerar_excel"

    g = StateGraph(Estado1)
    for nombre, fn in [("registrar", registrar), ("cargar_imagen", cargar_imagen), ("validar", validar),
                       ("preparar_revision", preparar_revision), ("revision_humana", revision_humana),
                       ("preparar_confirmacion", preparar_confirmacion), ("configurar_pago", configurar_pago),
                       ("validar_configuracion", validar_configuracion),
                       ("validar_decision", validar_decision), ("guardar", guardar),
                       ("regenerar_excel", regenerar_excel), ("marcar_duplicada", marcar_duplicada),
                       ("marcar_rechazada", marcar_rechazada), ("marcar_error", marcar_error)]:
        g.add_node(nombre, fn)
    g.add_node("extraer", extraer, retry_policy=politica)
    g.add_edge(START, "registrar")
    g.add_edge("registrar", "cargar_imagen")
    g.add_conditional_edges("cargar_imagen", tras_cargar, ["extraer", "marcar_error"])
    g.add_conditional_edges("extraer", tras_extraer, ["validar", "marcar_error"])
    g.add_conditional_edges("validar", decidir_veredicto,
                            ["marcar_duplicada", "preparar_confirmacion", "extraer", "preparar_revision"])
    g.add_edge("preparar_revision", "revision_humana")
    g.add_edge("revision_humana", "validar_decision")
    g.add_conditional_edges("validar_decision", tras_decision,
                            ["revision_humana", "preparar_confirmacion", "validar", "marcar_rechazada"])
    g.add_edge("preparar_confirmacion", "configurar_pago")
    g.add_edge("configurar_pago", "validar_configuracion")
    g.add_conditional_edges("validar_configuracion", tras_configuracion,
                            ["configurar_pago", "guardar", "marcar_rechazada"])
    g.add_conditional_edges("guardar", tras_guardar, ["marcar_duplicada", "regenerar_excel"])
    for terminal in ("regenerar_excel", "marcar_duplicada", "marcar_rechazada", "marcar_error"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)


# ------------------------------------------------------------------ utilidades ----
def config_hilo(factura_id: str) -> dict:
    return {"configurable": {"thread_id": factura_id}}


def correr(grafo, factura_id: str, rutas_imagen: list[str]) -> dict:
    return grafo.invoke(entrada_inicial(factura_id, rutas_imagen), config_hilo(factura_id))


def reanudar(grafo, factura_id: str, decision: dict) -> dict:
    return grafo.invoke(Command(resume=decision), config_hilo(factura_id))


def pausa_pendiente(grafo, factura_id: str) -> dict | None:
    """Payload del interrupt si el hilo está en pausa; None si no."""
    try:
        estado = grafo.get_state(config_hilo(factura_id))
    except Exception:
        return None
    for t in estado.tasks:
        for i in t.interrupts:
            return i.value
    return None
