"""Ayudas para pruebas con la interfaz Tkinter REAL: clics, escritura con teclas y selección en listas.

Los eventos se generan sobre los widgets reales (mismo camino que un clic o una tecla del usuario): un botón
deshabilitado NO responde, una celda deshabilitada NO acepta texto, y un widget oculto no se puede pulsar.
"""
from __future__ import annotations

import time
import tkinter
from tkinter import ttk

import pytest

TECLAS = {"ñ": "ntilde", "-": "minus", " ": "space", "/": "slash", ".": "period", ":": "colon", "°": "degree", ",": "comma"}


def bombear(v, s=None, veces: int = 2, pausa: float = 0.0) -> None:
    """Espera (sin girar) a que el trabajador termine lo encolado y procesa los eventos en el hilo de la interfaz."""
    for _ in range(veces):
        if s is not None:
            s.esperar_inactivo()
        v.vaciar_eventos()
        v.update()


def visible(w) -> bool:
    """¿El usuario puede ver y pulsar este widget? (mapeado en pantalla)."""
    w.update_idletasks()
    return bool(w.winfo_ismapped()) and w.winfo_width() > 1


def habilitado(w) -> bool:
    try:
        return "disabled" not in w.state()
    except (AttributeError, tkinter.TclError):
        return str(w.cget("state")) != "disabled"


def clic(w, doble: bool = False) -> None:
    """Clic izquierdo real sobre el centro del widget. No hace nada si está oculto."""
    assert visible(w), f"el widget {w} no está visible: el usuario no podría pulsarlo"
    x, y = max(w.winfo_width() // 2, 1), max(w.winfo_height() // 2, 1)
    w.event_generate("<Enter>", x=x, y=y)
    w.event_generate("<Motion>", x=x, y=y)
    w.event_generate("<ButtonPress-1>", x=x, y=y)
    w.event_generate("<ButtonRelease-1>", x=x, y=y)
    if doble:
        w.event_generate("<Double-Button-1>", x=x, y=y)
    w.update()


def escribir(entrada, texto: str, enter: bool = False) -> None:
    """Escribe en una celda con teclas reales: la selecciona entera y teclea. Si está deshabilitada no cambia."""
    assert visible(entrada), "la celda no está visible"
    entrada.focus_force()
    entrada.update()
    entrada.select_range(0, "end")
    entrada.icursor("end")
    for ch in texto:
        entrada.event_generate("<KeyPress>", keysym=TECLAS.get(ch, ch))
    if enter:
        entrada.event_generate("<KeyPress>", keysym="Return")
    entrada.update()


def elegir(combo: ttk.Combobox, etiqueta: str) -> None:
    """Elige una opción de la lista desplegable (equivale a abrirla y pinchar la opción)."""
    assert visible(combo) and "disabled" not in combo.state(), "la lista está deshabilitada"
    assert etiqueta in combo.cget("values"), f"{etiqueta!r} no es una opción"
    combo.set(etiqueta)
    combo.event_generate("<<ComboboxSelected>>")
    combo.update()


def abrir_app(r, repo, reloj, respuestas, extractor=None, inicio=None, geometria="1250x760+5+5"):
    from facturextract.extractor import ExtractorFalso
    from facturextract.servicio import Servicio
    from facturextract.ui import VentanaPrincipal

    s = Servicio(r, repo, extractor or ExtractorFalso(respuestas), reloj)
    s.iniciar()
    v = None
    for intento in range(4):
        try:
            v = VentanaPrincipal(s, repo, r, inicio=inicio)
            break
        except tkinter.TclError as e:
            texto = str(e).lower()
            if ".tcl" in texto:      # fallo intermitente de la instalación de Tcl/Tk
                time.sleep(0.5)                                    # de la Tienda de Windows: se reintenta
                continue
            s.detener()
            if "display" in texto:
                pytest.skip("sin pantalla disponible")
            raise
    if v is None:
        s.detener()
        pytest.skip("Tcl/Tk no disponible de forma intermitente en este equipo")
    if geometria:
        v.geometry(geometria)
    v.deiconify()
    v.update()
    v.update_idletasks()
    return s, v


def procesar(v, s, fotos) -> None:
    v.notebook.select(0)
    v.update()
    v.grupos = [[f] for f in fotos]
    v._pintar_grupos()
    clic(v.btn_procesar)
    bombear(v, s, 3)
    assert not v._procesando
    v.notebook.select(v.tab_lote)
    bombear(v, s, 2)


def fid_de(repo, folio: str) -> str:
    for f in repo.listar_por_estado("por_confirmar", "en_revision", "guardada"):
        c = f["extraido"] or {"folio": f["folio"]}
        if str(c.get("folio")) == str(folio):
            return f["id"]
    raise AssertionError(f"no hay factura con folio {folio}")


def descendientes(w):
    for h in w.winfo_children():
        yield h
        yield from descendientes(h)


def con_dialogo(v, s, clase, hacer, boton):
    """Pulsa `boton` (que abre un diálogo modal de `clase`) y maneja ese diálogo REAL con `hacer(dialogo)` desde un
    temporizador mientras el clic espera. Si el diálogo se queda abierto, un vigilante lo cierra a los 6 s."""
    def buscar():
        for w in descendientes(v):
            if isinstance(w, clase) and w.winfo_exists():
                return w
    ids = []

    def cuando():
        d = buscar()
        if d is None:
            ids.append(v.after(40, cuando))
            return
        hacer(d)

    def vigilante():
        d = buscar()
        if d is not None:
            d.destroy()
    ids += [v.after(80, cuando), v.after(6000, vigilante)]
    try:
        clic(boton)
    finally:
        for i in ids:
            try:
                v.after_cancel(i)
            except tkinter.TclError:
                pass
    bombear(v, s, 3)


def ir_a_pestana(v, s, nombre: str) -> None:
    v.notebook.select([i for i in v.notebook.tabs() if v.notebook.tab(i, "text").startswith(nombre)][0])
    bombear(v, s, 2)
