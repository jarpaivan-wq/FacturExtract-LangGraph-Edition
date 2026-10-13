"""Grafo 2: atención de un vencimiento (un run por seguimiento y día; hilo = alerta + id + fecha).

El planificador (código, fuera del grafo) decide cuándo lanzar un run. La ventana «Vencimientos
del día» reanuda el run con la decisión del usuario; esa decisión se valida aquí dentro y, si es
inválida, el grafo vuelve a preguntar.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, TypedDict
import operator

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .db import DecisionInvalida
from .grafo1 import Dependencias
from .modelo import METODOS_PAGO, DatosInvalidos, parse_fecha

ACCIONES = ("posponer", "pagada", "cerrar")


class Estado2(TypedDict, total=False):
    seguimiento_id: int
    factura_id: str
    hilo: str
    decision: dict
    error_decision: str | None
    nueva_fecha: str
    resultado: str | None     # pospuesta | cerrada | pagada | ya_pagada
    mes: str
    excel: str
    eventos: Annotated[list[str], operator.add]


def hilo_alerta(seguimiento_id: int, hoy: date) -> str:
    return f"alerta-{seguimiento_id}-{hoy.isoformat()}"


def entrada_alerta(seguimiento_id: int, factura_id: str, hilo: str) -> Estado2:
    return {"seguimiento_id": seguimiento_id, "factura_id": factura_id, "hilo": hilo, "decision": {},
            "error_decision": None, "resultado": None, "eventos": []}


def construir_grafo2(d: Dependencias, checkpointer):
    def preparar_alerta(e: Estado2) -> dict:
        d.repo.marcar_esperando(e["seguimiento_id"], e["hilo"])    # idempotente
        return {"eventos": ["esperando_decision"]}

    def atencion_humana(e: Estado2) -> dict:
        decision = interrupt({"tipo": "vencimiento", "seguimiento_id": e["seguimiento_id"],
                              "factura_id": e["factura_id"], "error": e.get("error_decision")})
        return {"decision": decision}

    def validar_decision(e: Estado2) -> dict:
        dec = e.get("decision") or {}
        accion = dec.get("accion")
        if accion not in ACCIONES:
            return {"error_decision": "Elija una acción: posponer, pagada o cerrar."}
        if accion == "posponer":
            try:
                f = parse_fecha(dec.get("fecha"))
            except DatosInvalidos as ex:
                return {"error_decision": str(ex)}
            if f is None:
                return {"error_decision": "Para posponer hay que elegir una fecha."}
            if f <= d.reloj.hoy():
                return {"error_decision": "La nueva fecha debe ser posterior a hoy."}
            return {"error_decision": None, "nueva_fecha": f.isoformat()}
        if accion == "pagada":
            if dec.get("metodo") not in METODOS_PAGO:
                return {"error_decision": "Elija el método de pago: contado, transferencia o cheque."}
            if dec["metodo"] == "cheque" and not str(dec.get("nro_documento") or "").strip():
                return {"error_decision": "Un pago con cheque requiere el N° de documento."}
        return {"error_decision": None}

    def posponer(e: Estado2) -> dict:
        d.repo.reprogramar(e["seguimiento_id"], date.fromisoformat(e["nueva_fecha"]))
        return {"resultado": "pospuesta", "eventos": ["pospuesta"]}

    def cerrar(e: Estado2) -> dict:
        from datetime import timedelta

        d.repo.reprogramar(e["seguimiento_id"], d.reloj.hoy() + timedelta(days=1))
        return {"resultado": "cerrada", "eventos": ["cerrada"]}

    def registrar_pago(e: Estado2) -> dict:
        dec = e["decision"]
        try:
            mes = d.repo.registrar_pago(e["factura_id"], d.reloj.hoy(), dec["metodo"], dec.get("nro_documento"))
        except DecisionInvalida as ex:
            return {"resultado": "ya_pagada", "excel": "ok", "eventos": [f"pago_omitido: {ex}"]}
        return {"resultado": "pagada", "mes": mes, "eventos": ["pagada"]}

    def regenerar_excel(e: Estado2) -> dict:
        if not e.get("mes"):
            return {"eventos": ["excel_omitido"]}
        return {"excel": d.excel.regenerar(e["mes"]), "eventos": ["excel_regenerado"]}

    def tras_decision(e: Estado2) -> str:
        if e.get("error_decision"):
            return "atencion_humana"
        return {"posponer": "posponer", "cerrar": "cerrar", "pagada": "registrar_pago"}[e["decision"]["accion"]]

    g = StateGraph(Estado2)
    for nombre, fn in [("preparar_alerta", preparar_alerta), ("atencion_humana", atencion_humana),
                       ("validar_decision", validar_decision), ("posponer", posponer), ("cerrar", cerrar),
                       ("registrar_pago", registrar_pago), ("regenerar_excel", regenerar_excel)]:
        g.add_node(nombre, fn)
    g.add_edge(START, "preparar_alerta")
    g.add_edge("preparar_alerta", "atencion_humana")
    g.add_edge("atencion_humana", "validar_decision")
    g.add_conditional_edges("validar_decision", tras_decision,
                            ["atencion_humana", "posponer", "cerrar", "registrar_pago"])
    g.add_edge("registrar_pago", "regenerar_excel")
    for t in ("posponer", "cerrar", "regenerar_excel"):
        g.add_edge(t, END)
    return g.compile(checkpointer=checkpointer)


def config_hilo(hilo: str) -> dict:
    return {"configurable": {"thread_id": hilo}}


def lanzar(grafo, seguimiento_id: int, factura_id: str, hilo: str) -> dict:
    return grafo.invoke(entrada_alerta(seguimiento_id, factura_id, hilo), config_hilo(hilo))


def decidir(grafo, hilo: str, decision: dict) -> dict:
    return grafo.invoke(Command(resume=decision), config_hilo(hilo))


def hilo_en_pausa(grafo, hilo: str) -> bool:
    try:
        estado = grafo.get_state(config_hilo(hilo))
    except Exception:
        return False
    return any(t.interrupts for t in estado.tasks)
