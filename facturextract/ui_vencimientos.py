"""Ventana «Vencimientos del día»: lista de seguimientos esperando decisión, con Posponer, Pagada y Cerrar."""
from __future__ import annotations

import tkinter as tk
from datetime import timedelta
from tkinter import messagebox, ttk
from typing import Callable

from . import formulario as fm
from . import ventanas
from .modelo import DatosInvalidos, parse_fecha
from .ui import DialogoPago


def texto_plazo(venc_iso: str | None, hoy) -> str:
    try:
        venc = parse_fecha(venc_iso)
    except DatosInvalidos:
        venc = None
    if venc is None:
        return "sin fecha"
    dias = (venc - hoy).days
    if dias < 0:
        return f"VENCIDA hace {-dias} día(s)"
    if dias == 0:
        return "vence HOY"
    return f"vence en {dias} día(s)"


class DialogoFecha(tk.Toplevel):
    def __init__(self, padre: tk.Misc, titulo: str, sugerida: str):
        super().__init__(padre)
        self.title(titulo)
        self.resultado: str | None = None
        self.resizable(False, False)
        self.var = tk.StringVar(value=sugerida)
        barra = ttk.Frame(self, padding=(14, 6, 14, 12))
        barra.pack(side="bottom", fill="x")                           # botones siempre visibles
        self.btn_ok = ttk.Button(barra, text="Aceptar", command=self._ok)
        self.btn_ok.pack(side="left", padx=4)
        ttk.Button(barra, text="Cancelar", command=self.destroy).pack(side="left")
        f = ttk.Frame(self, padding=(14, 14, 14, 0))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Nueva fecha de alerta (día-mes-año):").pack(anchor="w")
        self.ent = ttk.Entry(f, textvariable=self.var, width=14)
        self.ent.pack(anchor="w", pady=6)
        self.ent.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        ventanas.posicionar(self, padre)
        self.grab_set()

    def _ok(self) -> None:
        self.resultado = self.var.get().strip()
        self.destroy()

    @classmethod
    def preguntar(cls, padre, titulo, sugerida) -> str | None:
        d = cls(padre, titulo, sugerida)
        padre.wait_window(d)
        return d.resultado


class VentanaVencimientos(tk.Toplevel):
    """Lista de seguimientos esperando decisión. Cada fila: Posponer, Pagada y Cerrar. Con desplazamiento si hay muchos."""

    def __init__(self, principal, decidir: Callable[[int, dict], None]):
        super().__init__(principal)
        self.principal = principal
        self.decidir = decidir
        self.filas: dict[int, dict[str, ttk.Button]] = {}
        self.title("Vencimientos del día")
        barra = ttk.Frame(self, padding=(10, 6))
        barra.pack(side="bottom", fill="x")                           # siempre visible; las filas se desplazan
        self.btn_cerrar = ttk.Button(barra, text="Cerrar ventana", command=self.destroy)
        self.btn_cerrar.pack(side="right")
        ttk.Separator(self).pack(side="bottom", fill="x")
        self.lbl_titulo = ttk.Label(self, font=("Segoe UI", 10, "bold"))
        self.lbl_titulo.pack(anchor="w", padx=10, pady=(10, 4))
        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.lienzo = tk.Canvas(cuerpo, highlightthickness=0)
        sb = ttk.Scrollbar(cuerpo, orient="vertical", command=self.lienzo.yview)
        self.lienzo.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lienzo.pack(fill="both", expand=True)
        self.marco = ttk.Frame(self.lienzo)
        self.lienzo.create_window((0, 0), window=self.marco, anchor="nw")
        self.marco.bind("<Configure>", lambda e: self.lienzo.configure(scrollregion=self.lienzo.bbox("all")))
        self.bind("<MouseWheel>", lambda e: self.lienzo.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.refrescar()
        ventanas.posicionar(self, principal, 900, 440, minimo=(560, 300))

    def refrescar(self) -> None:
        for w in self.marco.winfo_children():
            w.destroy()
        self.filas = {}
        filas = self.principal.repo.seguimientos_esperando()
        hoy = self.principal.servicio.reloj.hoy()
        self.lbl_titulo.config(text=f"Hoy es {fm.hoy_texto(hoy)}. Facturas con vencimiento cercano o vencido:")
        if not filas:
            ttk.Label(self.marco, text="No hay vencimientos pendientes.").grid(row=1, column=0, sticky="w")
            return
        for i, s in enumerate(filas, start=1):
            plazo = texto_plazo(s["fecha_vencimiento"], hoy)
            ttk.Label(self.marco, text=f"{s['razon_social_emisor'] or s['rut_emisor']}\nFolio {s['folio']}",
                      width=34).grid(row=i, column=0, sticky="w", pady=4)
            ttk.Label(self.marco, text=f"$ {fm.miles(s['total'])}", width=14).grid(row=i, column=1)
            ttk.Label(self.marco, text=f"{fm.fecha_a_texto(s['fecha_vencimiento'])}\n{plazo}", width=24,
                      foreground="#b71c1c" if "VENCIDA" in plazo or "HOY" in plazo else "black"
                      ).grid(row=i, column=2)
            b = {"posponer": ttk.Button(self.marco, text="Posponer", command=lambda s=s: self._posponer(s)),
                 "pagada": ttk.Button(self.marco, text="Pagada", command=lambda s=s: self._pagada(s)),
                 "cerrar": ttk.Button(self.marco, text="Cerrar", command=lambda s=s: self._cerrar(s))}
            for c, nombre in enumerate(("posponer", "pagada", "cerrar"), start=3):
                b[nombre].grid(row=i, column=c, padx=3)
            self.filas[s["id"]] = b

    def _posponer(self, s: dict) -> None:
        sugerida = fm.hoy_texto(self.principal.servicio.reloj.hoy() + timedelta(days=1))
        f = DialogoFecha.preguntar(self, "Posponer", sugerida)
        if f:
            self.decidir(s["id"], {"accion": "posponer", "fecha": f})

    def _pagada(self, s: dict) -> None:
        r = DialogoPago.preguntar(self, f"Pago de la factura {s['folio']}")
        if r:
            self.decidir(s["id"], {"accion": "pagada", **r})

    def _cerrar(self, s: dict) -> None:
        self.decidir(s["id"], {"accion": "cerrar"})
