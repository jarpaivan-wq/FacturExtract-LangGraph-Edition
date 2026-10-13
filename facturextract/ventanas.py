"""Tamaño y posición de las ventanas emergentes contra el ÁREA ÚTIL de la pantalla.

Problema que resuelve: ventanas que aparecían muy abajo, con los botones fuera de la pantalla.

· El área útil es la del monitor donde está la ventana principal (o el puntero si está oculta en la bandeja), SIN
  la barra de tareas (`rcWork` de Windows). Sirve con varios monitores, también con coordenadas negativas.
· Los tamaños se piden en «píxeles de diseño» (96 ppp) y se escalan con la escala de pantalla (100, 125, 150 %).
· La ventana completa (con barra de título y bordes) queda dentro del área útil; si no cabe, se reduce.
· Se centra sobre la ventana principal y se corrige al mostrarse, con los bordes reales medidos.
"""
from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk
from typing import Callable

MARGEN = 12            # píxeles de diseño que se dejan libres contra el borde del área útil
TITULO = 31            # estimación de barra de título + borde superior (se mide y corrige al mostrarse)
BORDE = 8

# Los tests pueden inyectar el área útil de un monitor simulado: f(x, y) -> (izq, arriba, der, abajo)
proveedor_area: Callable[[int, int], tuple[int, int, int, int]] | None = None


def escala(w: tk.Misc) -> float:
    """Escala de pantalla vista por Tk (1.0 = 100 %, 1.25 = 125 %…). Con procesos sin soporte de DPI Tk ve 1.0 y
    todas las coordenadas son «lógicas», coherentes entre sí."""
    try:
        return max(w.winfo_fpixels("1i") / 96.0, 1.0)
    except tk.TclError:
        return 1.0


def px(w: tk.Misc, n: float) -> int:
    return int(round(n * escala(w)))


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def _area_windows(x: int, y: int) -> tuple[int, int, int, int] | None:
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)      # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            w = info.rcWork
            return int(w.left), int(w.top), int(w.right), int(w.bottom)
    except Exception:
        pass
    return None


def area_util(ref: tk.Misc, x: int, y: int) -> tuple[int, int, int, int]:
    """(izq, arriba, der, abajo) del área útil del monitor que contiene el punto (x, y)."""
    if proveedor_area is not None:
        return proveedor_area(x, y)
    a = _area_windows(x, y)
    if a:
        return a
    return 0, 0, ref.winfo_screenwidth(), ref.winfo_screenheight() - px(ref, 48)    # sin API: se supone una barra de tareas


def _punto_de_referencia(v: tk.Misc, principal: tk.Misc | None) -> tuple[int, int]:
    try:
        if principal is not None and principal.winfo_exists() and principal.winfo_viewable() \
                and principal.state() != "iconic":
            return (principal.winfo_rootx() + principal.winfo_width() // 2,
                    principal.winfo_rooty() + principal.winfo_height() // 2)
    except tk.TclError:
        pass
    return v.winfo_pointerx(), v.winfo_pointery()          # principal oculta en la bandeja: el monitor del puntero


def _bordes(v: tk.Toplevel | tk.Tk, medidos: bool) -> tuple[int, int]:
    """(borde lateral, alto de título + borde superior). Medidos si la ventana ya está en pantalla."""
    if medidos:
        dx, dy = v.winfo_rootx() - v.winfo_x(), v.winfo_rooty() - v.winfo_y()
        if 0 <= dx < px(v, 40) and 0 < dy < px(v, 120):
            return dx, dy
    return px(v, BORDE), px(v, TITULO + BORDE)


def _colocar(v, principal, ancho_dis, alto_dis, medidos: bool) -> tuple[int, int]:
    cx, cy = _punto_de_referencia(v, principal)
    izq, arr, der, aba = area_util(v, cx, cy)
    m = px(v, MARGEN)
    dx, dy = _bordes(v, medidos)
    max_w = (der - izq) - 2 * m - 2 * dx
    max_h = (aba - arr) - 2 * m - dy - dx
    w = max(min(ancho_dis, max_w), 100)
    h = max(min(alto_dis, max_h), 100)
    ext_w, ext_h = w + 2 * dx, h + dy + dx
    x = min(max(cx - ext_w // 2, izq + m), der - m - ext_w)
    y = min(max(cy - ext_h // 2, arr + m), aba - m - ext_h)
    v.geometry(f"{w}x{h}+{x}+{y}")
    return w, h


def posicionar(v: tk.Toplevel | tk.Tk, principal: tk.Misc | None = None, ancho: float | None = None,
               alto: float | None = None, minimo: tuple[float, float] = (0, 0)) -> None:
    """Dimensiona y centra `v` dentro del área útil. `ancho`/`alto` en píxeles de diseño (None = lo que pide el
    contenido). Se vuelve a ajustar con los bordes reales al mostrarse."""
    try:
        if principal is not None and not principal.winfo_viewable():
            v.tk.call("wm", "transient", v._w, "")     # un diálogo «hijo» de una ventana oculta (bandeja) no se vería
    except tk.TclError:
        pass
    v.update_idletasks()
    ancho_dis = px(v, ancho) if ancho is not None else v.winfo_reqwidth()
    alto_dis = px(v, alto) if alto is not None else v.winfo_reqheight()
    w, h = _colocar(v, principal, ancho_dis, alto_dis, medidos=False)
    try:
        v.minsize(min(px(v, minimo[0]), w), min(px(v, minimo[1]), h))     # el mínimo nunca supera lo que cabe
        v.maxsize(10000, 10000)
    except tk.TclError:
        pass

    def corregir(_e=None) -> None:
        try:
            v.unbind("<Map>", mapa)
        except tk.TclError:
            return
        w2, h2 = _colocar(v, principal, ancho_dis, alto_dis, medidos=True)
        try:
            v.minsize(min(px(v, minimo[0]), w2), min(px(v, minimo[1]), h2))
        except tk.TclError:
            pass
    mapa = v.bind("<Map>", corregir, add="+")


class ContenidoDesplazable(ttk.Frame):
    """Un marco con barra vertical: si el contenido es más alto que la ventana, se desplaza el contenido (nunca la
    barra de botones, que va aparte, fija abajo). El ancho del contenido sigue al de la ventana."""

    def __init__(self, padre, **kw):
        super().__init__(padre, **kw)
        self.lienzo = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.barra = ttk.Scrollbar(self, orient="vertical", command=self.lienzo.yview)
        self.lienzo.configure(yscrollcommand=self.barra.set)
        self.barra.pack(side="right", fill="y")
        self.lienzo.pack(side="left", fill="both", expand=True)
        self.contenido = ttk.Frame(self.lienzo)
        self._ventana = self.lienzo.create_window((0, 0), window=self.contenido, anchor="nw")
        self.contenido.bind("<Configure>", self._al_cambiar_contenido)
        self.lienzo.bind("<Configure>", lambda e: self.lienzo.itemconfigure(self._ventana, width=e.width))
        self.bind_all("<MouseWheel>", self._rueda, add="+")

    def _al_cambiar_contenido(self, _e=None) -> None:
        self.lienzo.configure(scrollregion=self.lienzo.bbox("all"), width=max(self.contenido.winfo_reqwidth(), 100))

    def _rueda(self, ev) -> None:
        try:
            if str(ev.widget).startswith(str(self)) and self.lienzo.yview() != (0.0, 1.0):
                self.lienzo.yview_scroll(-1 if ev.delta > 0 else 1, "units")
        except tk.TclError:
            pass

    def alto_pedido(self) -> int:
        self.contenido.update_idletasks()
        return self.contenido.winfo_reqheight()
