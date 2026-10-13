"""Interfaz Tkinter. Se actualiza SOLO desde su hilo: el servicio le manda eventos por una cola
que `_bombear` vacía con `after`. Los errores se muestran en lenguaje normal."""
from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import formulario as fm
from . import ventanas
from .config import Rutas
from .db import Repo
from .extractor import probar_conexion
from .modelo import DatosInvalidos, parse_fecha
from .servicio import Evento, Servicio

TIPOS_FOTO = [("Fotos de facturas", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"), ("Todos los archivos", "*.*")]
ROTULO_RESULTADO = {"guardada": "✔ Guardada", "revision": "⚠ Por revisar", "lista": "● Lista para confirmar", "duplicada": "✖ Ya existía",
                    "rechazada": "✖ Rechazada", "error": "✖ No se pudo leer"}


class VentanaPrincipal(tk.Tk):
    def __init__(self, servicio: Servicio, repo: Repo, rutas: Rutas, inicio=None):
        super().__init__()
        self.servicio, self.repo, self.rutas = servicio, repo, rutas
        self.inicio = inicio       # InicioWindows (o None: sin casilla de inicio automático)
        self.title("FacturExtract")
        self.grupos: list[list[str]] = []
        self._procesando = False
        self.comandos: queue.Queue[str] = queue.Queue()  # pedidos de otros hilos (bandeja, 2.ª instancia)
        self.bandeja = None                              # la aplicación residente la asigna
        self._saliendo = False
        self.protocol("WM_DELETE_WINDOW", self.ocultar)
        self._construir()
        ventanas.posicionar(self, None, 1180, 780, minimo=(760, 520))   # cabe en pantallas chicas
        self.after(150, self._bombear)
        self.refrescar_todo()

    # ------------------------------------------------------------------ armado
    def _construir(self) -> None:
        if getattr(self.servicio.reloj, "simulado", False):
            self._banner_simulado()
        barra = ttk.Frame(self, padding=(8, 6))
        barra.pack(fill="x")
        self.lbl_estado = ttk.Label(barra, text="")
        self.lbl_estado.pack(side="left")
        ttk.Button(barra, text="Probar conexión", command=self._probar_conexion).pack(side="right")
        ttk.Button(barra, text="Abrir carpeta de Excel", command=self._abrir_excel).pack(side="right", padx=6)
        ttk.Button(barra, text="Avisos…", command=self._ajustes_avisos).pack(side="right", padx=6)
        if self.inicio is not None:
            self.var_inicio = tk.BooleanVar(value=self.inicio.activo())
            self.chk_inicio = ttk.Checkbutton(barra, text="Iniciar con Windows", variable=self.var_inicio,
                                              command=self._alternar_inicio)
            self.chk_inicio.pack(side="right", padx=6)
        self.btn_venc = ttk.Button(barra, text="Vencimientos del día", command=lambda: self.abrir_vencimientos())
        self.btn_venc.pack(side="right", padx=6)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._tab_procesar()
        self._tab_lote()
        self._tab_facturas()
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self.refrescar_todo())

    def _banner_simulado(self) -> None:
        """Franja roja permanente: la fecha de «hoy» es simulada (solo desarrollo)."""
        self.title("FacturExtract  —  FECHA SIMULADA (desarrollo)")
        self.banner = tk.Frame(self, bg="#b71c1c")
        self.banner.pack(fill="x")
        self.lbl_banner = tk.Label(self.banner, bg="#b71c1c", fg="white", font=("Segoe UI", 10, "bold"), anchor="w")
        self.lbl_banner.pack(side="left", padx=10, pady=4)
        for texto, kw in (("+1 día", {"days": 1}), ("+1 hora", {"hours": 1})):
            tk.Button(self.banner, text=texto, command=lambda kw=kw: self._avanzar_reloj(**kw)).pack(side="right", padx=3, pady=2)
        tk.Button(self.banner, text="Revisar vencimientos ahora", command=self.servicio.revisar_vencimientos).pack(
            side="right", padx=3, pady=2)
        self._pintar_banner()

    def _pintar_banner(self) -> None:
        ahora = self.servicio.reloj.ahora()
        self.lbl_banner.config(text=f"⚠ FECHA SIMULADA (solo desarrollo): {ahora:%d-%m-%Y %H:%M}")
        self.after(1000, self._pintar_banner)

    def _avanzar_reloj(self, **kw) -> None:
        self.servicio.reloj.avanzar(**kw)
        self._pintar_banner_ahora()
        self.servicio.revisar_vencimientos()

    def _pintar_banner_ahora(self) -> None:
        ahora = self.servicio.reloj.ahora()
        self.lbl_banner.config(text=f"⚠ FECHA SIMULADA (solo desarrollo): {ahora:%d-%m-%Y %H:%M}")

    # ---- Procesar
    def _tab_procesar(self) -> None:
        t = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(t, text="Procesar")
        ttk.Label(t, text="1) Agregue las fotos.  2) Si dos fotos son la misma factura (frente y reverso), "
                          "selecciónelas y pulse «Juntar».  3) Pulse PROCESAR.", wraplength=950).pack(anchor="w")
        fila = ttk.Frame(t)
        fila.pack(fill="x", pady=8)
        ttk.Button(fila, text="Agregar fotos…", command=self._agregar_fotos).pack(side="left")
        ttk.Button(fila, text="Juntar 2 seleccionadas", command=self._juntar).pack(side="left", padx=6)
        ttk.Button(fila, text="Separar", command=self._separar).pack(side="left")
        ttk.Button(fila, text="Quitar", command=self._quitar).pack(side="left", padx=6)
        self.btn_procesar = tk.Button(fila, text="PROCESAR", font=("Segoe UI", 12, "bold"), bg="#2e7d32", fg="white",
                                      padx=24, command=self._procesar)
        self.btn_procesar.pack(side="right")
        self.lista = tk.Listbox(t, selectmode="extended", height=8, activestyle="none")
        self.lista.pack(fill="x")
        self.progreso = ttk.Progressbar(t, mode="indeterminate")
        self.progreso.pack(fill="x", pady=(8, 0))
        self.lbl_progreso = ttk.Label(t, text="")
        self.lbl_progreso.pack(anchor="w")
        cols = ("factura", "resultado", "detalle")
        self.res = ttk.Treeview(t, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (300, 140, 520)):
            self.res.heading(c, text=c.capitalize())
            self.res.column(c, width=w, anchor="w")
        self.res.pack(fill="both", expand=True, pady=(8, 0))

    def _agregar_fotos(self) -> None:
        rutas = filedialog.askopenfilenames(title="Elija las fotos de las facturas", filetypes=TIPOS_FOTO)
        for r in rutas:
            self.grupos.append([r])
        self._pintar_grupos()

    def _pintar_grupos(self) -> None:
        self.lista.delete(0, "end")
        for g in self.grupos:
            etiqueta = " + ".join(Path(r).name for r in g)
            self.lista.insert("end", f"{'[2 fotos, 1 factura] ' if len(g) == 2 else ''}{etiqueta}")

    def _juntar(self) -> None:
        sel = list(self.lista.curselection())
        if len(sel) != 2 or any(len(self.grupos[i]) != 1 for i in sel):
            messagebox.showinfo("Juntar fotos", "Seleccione exactamente 2 fotos sueltas (con la tecla Ctrl) "
                                                "para juntarlas como una sola factura.")
            return
        a, b = sel
        self.grupos[a] = self.grupos[a] + self.grupos[b]
        del self.grupos[b]
        self._pintar_grupos()

    def _separar(self) -> None:
        nuevos = []
        sel = set(self.lista.curselection())
        for i, g in enumerate(self.grupos):
            nuevos.extend([[r] for r in g] if i in sel else [g])
        self.grupos = nuevos
        self._pintar_grupos()

    def _quitar(self) -> None:
        sel = set(self.lista.curselection())
        self.grupos = [g for i, g in enumerate(self.grupos) if i not in sel]
        self._pintar_grupos()

    def _procesar(self) -> None:
        if not self.grupos:
            messagebox.showinfo("Procesar", "Primero agregue las fotos de las facturas.")
            return
        if self._procesando:
            return
        self._procesando = True
        self.btn_procesar.config(state="disabled")
        self.progreso.start(12)
        lote, self.grupos = self.grupos, []
        self._pintar_grupos()
        self.servicio.procesar_lote(lote)

    # ---- Lote (generaliza «Por revisar»)
    def _tab_lote(self) -> None:
        from .ui_lote import PanelLote

        self.panel_lote = PanelLote(self.notebook, self)
        self.tab_lote = self.panel_lote
        self.notebook.add(self.panel_lote, text="Lote")

    # ---- Facturas
    def _tab_facturas(self) -> None:
        t = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(t, text="Facturas")
        fila = ttk.Frame(t)
        fila.pack(fill="x")
        ttk.Label(fila, text="Mes de emisión:").pack(side="left")
        self.var_mes = tk.StringVar(value="Todos")
        self.cmb_mes = ttk.Combobox(fila, textvariable=self.var_mes, width=10, state="readonly")
        self.cmb_mes.pack(side="left", padx=4)
        ttk.Label(fila, text="Pago:").pack(side="left", padx=(10, 0))
        self.var_pago = tk.StringVar(value="Todos")
        cmb = ttk.Combobox(fila, textvariable=self.var_pago, width=12, state="readonly",
                           values=["Todos", "pendiente", "pagada"])
        cmb.pack(side="left", padx=4)
        for c in (self.cmb_mes, cmb):
            c.bind("<<ComboboxSelected>>", lambda e: self._refrescar_facturas())
        self.btn_marcar_pagada = ttk.Button(fila, text="Marcar pagada", command=self._marcar_pagada)
        self.btn_marcar_pagada.pack(side="right")
        self.btn_editar_pago = ttk.Button(fila, text="Editar pago…", command=self._editar_pago)
        self.btn_editar_pago.pack(side="right", padx=6)
        self.btn_editar_pago.state(["disabled"])        # sin fila seleccionada se ve, pero deshabilitado
        cols = ("emision", "tipo", "folio", "emisor", "total", "vence", "pago", "validacion")
        enc = ("Emisión", "Tipo", "Folio", "Emisor", "Total", "Vence", "Pago", "Validación")
        self.arbol_fac = ttk.Treeview(t, columns=cols, show="headings", selectmode="browse")
        for c, e, w in zip(cols, enc, (90, 110, 70, 260, 100, 90, 100, 260)):
            self.arbol_fac.heading(c, text=e)
            self.arbol_fac.column(c, width=w, anchor="e" if c == "total" else "w")
        self.arbol_fac.pack(fill="both", expand=True, pady=(8, 0))
        self.arbol_fac.bind("<<TreeviewSelect>>", lambda e: self._actualizar_botones_facturas())
        self.arbol_fac.bind("<Double-1>", self._doble_clic_factura)          # doble clic en la fila = Editar pago

    def _actualizar_botones_facturas(self) -> None:
        self.btn_editar_pago.state(["!disabled"] if self.arbol_fac.selection() else ["disabled"])

    def _doble_clic_factura(self, evento) -> None:
        fila = self.arbol_fac.identify_row(evento.y)
        if fila:
            self.arbol_fac.selection_set(fila)
            self._editar_pago()

    def _refrescar_facturas(self) -> None:
        todas = self.repo.listar_guardadas()
        meses = sorted({f["mes_emision"] for f in todas}, reverse=True)
        self.cmb_mes["values"] = ["Todos"] + meses
        mes = None if self.var_mes.get() == "Todos" else self.var_mes.get()
        pago = None if self.var_pago.get() == "Todos" else self.var_pago.get()
        elegida = self.arbol_fac.selection()          # la selección sobrevive a los refrescos
        for i in self.arbol_fac.get_children():
            self.arbol_fac.delete(i)
        for f in self.repo.listar_guardadas(mes, pago):
            self.arbol_fac.insert("", "end", iid=f["id"], values=(
                fm.fecha_a_texto(f["fecha_emision"]), fm.nombre_tipo(f["tipo_documento"]), f["folio"],
                f["razon_social_emisor"] or f["rut_emisor"], fm.miles(f["total"]),
                fm.fecha_a_texto(f["fecha_vencimiento"]), fm.pago_texto(f), f["estado_validacion"]))
        vuelve = [i for i in elegida if self.arbol_fac.exists(i)]
        if vuelve:
            self.arbol_fac.selection_set(vuelve)
        self._actualizar_botones_facturas()

    def _marcar_pagada(self) -> None:
        sel = self.arbol_fac.selection()
        if not sel:
            messagebox.showinfo("Marcar pagada", "Seleccione una factura de la lista.")
            return
        f = self.repo.obtener(sel[0])
        if f["estado_pago"] != "pendiente":
            messagebox.showinfo("Marcar pagada", f"Esta factura no tiene pago pendiente ({fm.pago_texto(f)}).")
            return
        r = DialogoPago.preguntar(self, f"Pago de la factura {f['folio']}")
        if r:
            self.servicio.pagar(f["id"], r["metodo"], r["nro_documento"])

    def _editar_pago(self) -> None:
        """Corrige el pago de la factura seleccionada (en ambos sentidos: pendiente <-> pagada, o cambia el método)."""
        sel = self.arbol_fac.selection()
        if not sel:
            messagebox.showinfo("Editar pago", "Seleccione una factura de la lista.", parent=self)
            return
        f = self.repo.obtener(sel[0])
        if f["estado_pago"] not in ("pendiente", "pagada"):
            messagebox.showinfo("Editar pago", "Este documento no tiene estado de pago (solo las facturas lo tienen).",
                                parent=self)
            return
        r = DialogoEditarPago.preguntar(self, f, self.servicio.reloj.hoy())
        if r:
            self.servicio.corregir_pago(f["id"], r["estado"], r["metodo"], r["nro_documento"], r["fecha_pago"],
                                        r["fecha_vencimiento"])

    # ------------------------------------------------------------------- eventos
    def refrescar_todo(self) -> None:
        self.panel_lote.refrescar()
        self._refrescar_facturas()
        self.actualizar_contador_vencimientos()

    def actualizar_contador_vencimientos(self) -> None:
        n = len(self.repo.seguimientos_esperando())
        self.btn_venc.config(text=f"Vencimientos del día ({n})" if n else "Vencimientos del día")
        if self.bandeja is not None:
            self.bandeja.actualizar(n)

    # ---- residente: ocultar / mostrar / salir
    def pedir(self, accion: str) -> None:
        """Seguro desde cualquier hilo: solo encola; la ejecuta el hilo de la interfaz."""
        self.comandos.put(accion)

    def _ejecutar_comando(self, accion: str) -> None:
        logging.getLogger(__name__).info("Comando de la bandeja: %s", accion)
        if accion == "abrir":
            self.mostrar()
        elif accion == "vencimientos":
            self.abrir_vencimientos()
        elif accion == "inicio" and self.inicio is not None:
            self.var_inicio.set(not self.inicio.activo())
            self._alternar_inicio()
        elif accion == "conexion":
            self.mostrar()
            self._probar_conexion()
        elif accion == "salir":
            self.salir()

    def mostrar(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def ocultar(self) -> None:
        """Cerrar la ventana la oculta; la aplicación sigue en la bandeja (si hay bandeja)."""
        if self.bandeja is None:
            self.salir()
        else:
            self.withdraw()

    def salir(self) -> None:
        self._saliendo = True
        if self.bandeja is not None:
            self.bandeja.detener()
        self.destroy()

    def abrir_vencimientos(self) -> None:
        from .ui_vencimientos import VentanaVencimientos

        v = getattr(self, "_ventana_venc", None)
        if v is not None and v.winfo_exists():
            v.deiconify()
            v.lift()
            v.refrescar()
            return
        self._ventana_venc = VentanaVencimientos(self, self.servicio.decidir_vencimiento)
        self._ventana_venc.lift()
        self._ventana_venc.attributes("-topmost", True)
        self._ventana_venc.after(400, lambda: self._ventana_venc.attributes("-topmost", False))

    def vaciar_eventos(self) -> bool:
        """Procesa lo pendiente en las colas (eventos del servicio y pedidos de la bandeja). Devuelve False si
        la aplicación se está cerrando. Solo se llama desde el hilo de la interfaz."""
        try:
            while True:
                self._manejar(self.servicio.eventos.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                self._ejecutar_comando(self.comandos.get_nowait())
                if self._saliendo:
                    return False
        except queue.Empty:
            pass
        return True

    def _bombear(self) -> None:
        if self.vaciar_eventos():
            self.after(150, self._bombear)

    def _manejar(self, ev: Evento) -> None:
        d = ev.datos
        if ev.tipo == "progreso":
            self.lbl_progreso.config(text=d["texto"])
        elif ev.tipo == "factura":
            self.res.insert("", 0, values=(d.get("nombre", ""), ROTULO_RESULTADO.get(d["resultado"], d["resultado"]),
                                           d.get("mensaje", "")))
        elif ev.tipo == "lote_fin":
            self._procesando = False
            self.progreso.stop()
            self.btn_procesar.config(state="normal")
            self.lbl_progreso.config(text=(
                f"Listo: {d['listas']} lista(s) para confirmar, {d['revision']} con hallazgos, "
                f"{d['guardadas']} guardada(s), {d['duplicadas']} ya existían, {d['errores']} con problema."))
            self.refrescar_todo()
            if d["listas"] or d["revision"]:
                self.notebook.select(self.tab_lote)   # una sola ventana para todo el lote
        elif ev.tipo == "lote_confirmado":
            self.panel_lote.al_lote_confirmado(d["resultados"])
        elif ev.tipo in ("revision", "alertas", "pago"):
            self.refrescar_todo()
            v = getattr(self, "_ventana_venc", None)
            if v is not None and v.winfo_exists():
                v.refrescar()
            if ev.tipo == "alertas" and d.get("abrir_ventana"):
                self.abrir_vencimientos()
            if ev.tipo == "pago" and d.get("excel") != "ok":
                self.lbl_estado.config(text="El Excel del mes está abierto: se actualizará solo al cerrarlo.")
        elif ev.tipo == "aviso":
            messagebox.showwarning("Atención", d["mensaje"], parent=self)
        elif ev.tipo == "error":
            messagebox.showerror("Algo salió mal", d["mensaje"], parent=self)
        elif ev.tipo == "conexion":
            (messagebox.showinfo if d["ok"] else messagebox.showwarning)("Probar conexión", d["mensaje"], parent=self)
        elif ev.tipo == "excel":
            pend = [m for m, e in d["estado"].items() if e != "ok"]
            self.lbl_estado.config(text="Excel desactualizado: " + ", ".join(pend) if pend else "")
        else:
            self.manejar_evento_extra(ev)

    def manejar_evento_extra(self, ev: Evento) -> None:
        """Gancho para eventos de etapas posteriores (alertas, bandeja)."""

    # ----------------------------------------------------------------- utilidades
    def _probar_conexion(self) -> None:
        self.lbl_estado.config(text="Probando conexión…")

        def hilo() -> None:
            ok, msg = probar_conexion()
            carpetas = all(p.exists() for p in (self.rutas.datos, self.rutas.excel, self.rutas.imagenes))
            if not carpetas:
                ok, msg = False, msg + " Además, faltan carpetas de trabajo."
            self.servicio.emitir("conexion", ok=ok, mensaje=msg)

        threading.Thread(target=hilo, daemon=True).start()
        self.after(1500, lambda: self.lbl_estado.config(text=""))

    def _alternar_inicio(self) -> None:
        """Casilla «Iniciar con Windows»: activa o desactiva la entrada propia de inicio, y lo deja registrado."""
        from . import arranque

        quiere = bool(self.var_inicio.get())
        ok = self.inicio.activar() if quiere else self.inicio.desactivar()
        arranque.anotar("inicio con Windows", accion="activar" if quiere else "desactivar", resultado="ok" if ok else "error",
                        comando=self.inicio.comando_deseado() if quiere else "-")
        if not ok:
            messagebox.showwarning("Iniciar con Windows", "No se pudo cambiar el inicio automático.", parent=self)
        self.var_inicio.set(self.inicio.activo())
        if self.bandeja is not None:
            try:
                self.bandeja.icono.update_menu()
            except Exception:
                pass

    def _ajustes_avisos(self) -> None:
        DialogoAvisos(self, self.servicio)

    def _abrir_excel(self) -> None:
        self.rutas.excel.mkdir(parents=True, exist_ok=True)
        os.startfile(self.rutas.excel)  # type: ignore[attr-defined]


class DialogoAvisos(tk.Toplevel):
    """Hora a partir de la cual se abre sola la ventana de vencimientos y cada cuánto se revisa."""

    def __init__(self, padre: tk.Misc, servicio: Servicio):
        super().__init__(padre)
        self.servicio = servicio
        self.title("Avisos de vencimiento")
        self.resizable(False, False)
        self.transient(padre)
        hora, intervalo = servicio.ajustes_avisos()
        self.var_hora, self.var_int = tk.StringVar(value=hora), tk.StringVar(value=f"{intervalo:g}")
        barra = ttk.Frame(self, padding=(14, 6, 14, 12))
        barra.pack(side="bottom", fill="x")                           # botones siempre visibles
        self.btn_guardar = ttk.Button(barra, text="Guardar", command=self._guardar)
        self.btn_guardar.pack(side="left", padx=4)
        ttk.Button(barra, text="Cancelar", command=self.destroy).pack(side="left")
        f = ttk.Frame(self, padding=(14, 14, 14, 0))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Abrir los avisos desde las (HH:MM):").grid(row=0, column=0, sticky="w")
        self.ent_hora = ttk.Entry(f, textvariable=self.var_hora, width=8)
        self.ent_hora.grid(row=0, column=1, padx=6, pady=3)
        ttk.Label(f, text="Revisar cada (minutos):").grid(row=1, column=0, sticky="w")
        self.ent_int = ttk.Entry(f, textvariable=self.var_int, width=8)
        self.ent_int.grid(row=1, column=1, padx=6, pady=3)
        self.bind("<Escape>", lambda e: self.destroy())
        ventanas.posicionar(self, padre)
        self.grab_set()

    def _guardar(self) -> None:
        error = self.servicio.guardar_ajustes_avisos(self.var_hora.get(), self.var_int.get())
        if error:
            messagebox.showwarning("Avisos", error, parent=self)
        else:
            self.destroy()


class DialogoEditarPago(tk.Toplevel):
    """Corrige el pago de una factura ya guardada: estado (pendiente / pagada), método, N° de documento (solo cheque,
    obligatorio) y fecha de pago (por defecto hoy, editable). Si la factura no tiene vencimiento y queda pendiente,
    pide la fecha de vencimiento antes de permitir guardar. Botones fijos abajo."""

    def __init__(self, padre: tk.Misc, f: dict, hoy):
        super().__init__(padre)
        self.title(f"Editar pago — factura {f['folio']}")
        self.resultado: dict | None = None
        self.transient(padre)
        self.resizable(False, False)
        self.hoy = hoy
        self.sin_venc = not f["fecha_vencimiento"]
        self.var_estado = tk.StringVar(value=f["estado_pago"])
        self.var_metodo = tk.StringVar(value=f["metodo_pago"] or "contado")
        self.var_nro = tk.StringVar(value=f["nro_documento"] or "")
        self.var_fecha = tk.StringVar(value=fm.fecha_a_texto(f["fecha_pago"]) if f["fecha_pago"] else fm.hoy_texto(hoy))
        self.var_venc = tk.StringVar()
        barra = ttk.Frame(self, padding=(14, 6, 14, 12))
        barra.pack(side="bottom", fill="x")                          # los botones siempre visibles
        self.btn_guardar = ttk.Button(barra, text="Guardar", command=self._guardar)
        self.btn_guardar.pack(side="left", padx=4)
        self.btn_cancelar = ttk.Button(barra, text="Cancelar", command=self.destroy)
        self.btn_cancelar.pack(side="left")
        cuerpo = ttk.Frame(self, padding=(14, 12, 14, 0))
        cuerpo.pack(fill="both", expand=True)
        ttk.Label(cuerpo, text=f"{f['razon_social_emisor'] or f['rut_emisor']}  ·  $ {fm.miles(f['total'])}",
                  font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(cuerpo, text="Estado:").grid(row=1, column=0, sticky="w")
        fe = ttk.Frame(cuerpo)
        fe.grid(row=1, column=1, sticky="w")
        self.radios_estado = {}
        for clave, texto in (("pendiente", "Pendiente"), ("pagada", "Pagada")):
            self.radios_estado[clave] = ttk.Radiobutton(fe, text=texto, value=clave, variable=self.var_estado,
                                                        command=self._actualizar)
            self.radios_estado[clave].pack(side="left", padx=(0, 10))
        ttk.Label(cuerpo, text="Método de pago:").grid(row=2, column=0, sticky="nw", pady=(6, 0))
        fm_ = ttk.Frame(cuerpo)
        fm_.grid(row=2, column=1, sticky="w", pady=(6, 0))
        self.radios_metodo = {}
        for clave, texto in (("contado", "Contado"), ("transferencia", "Transferencia"), ("cheque", "Cheque")):
            self.radios_metodo[clave] = ttk.Radiobutton(fm_, text=texto, value=clave, variable=self.var_metodo,
                                                        command=self._actualizar)
            self.radios_metodo[clave].pack(anchor="w")
        ttk.Label(cuerpo, text="N° de documento (cheque):").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.ent_nro = ttk.Entry(cuerpo, textvariable=self.var_nro, width=18)
        self.ent_nro.grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Label(cuerpo, text="Fecha de pago (día-mes-año):").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.ent_fecha = ttk.Entry(cuerpo, textvariable=self.var_fecha, width=14)
        self.ent_fecha.grid(row=4, column=1, sticky="w", pady=(6, 0))
        if self.sin_venc:
            ttk.Label(cuerpo, text="Vencimiento (falta; obligatorio si queda pendiente):").grid(
                row=5, column=0, sticky="w", pady=(6, 0))
            self.ent_venc = ttk.Entry(cuerpo, textvariable=self.var_venc, width=14)
            self.ent_venc.grid(row=5, column=1, sticky="w", pady=(6, 0))
        else:
            self.ent_venc = None
            ttk.Label(cuerpo, text=f"Vencimiento: {fm.fecha_a_texto(f['fecha_vencimiento'])} (no cambia)").grid(
                row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.bind("<Escape>", lambda e: self.destroy())
        self._actualizar()
        ventanas.posicionar(self, padre)
        self.grab_set()

    def _actualizar(self) -> None:
        pagada = self.var_estado.get() == "pagada"
        for r in self.radios_metodo.values():
            r.config(state="normal" if pagada else "disabled")
        self.ent_nro.config(state="normal" if pagada and self.var_metodo.get() == "cheque" else "disabled")
        self.ent_fecha.config(state="normal" if pagada else "disabled")
        if self.ent_venc is not None:
            self.ent_venc.config(state="normal" if not pagada else "disabled")

    def _guardar(self) -> None:
        estado = self.var_estado.get()
        metodo = self.var_metodo.get() if estado == "pagada" else None
        nro = self.var_nro.get().strip() if metodo == "cheque" else None
        if metodo == "cheque" and not nro:
            messagebox.showwarning("Falta dato", "Con cheque hay que indicar el N° de documento.", parent=self)
            return
        fecha_pago = None
        if estado == "pagada":
            try:
                f = parse_fecha(self.var_fecha.get())
            except DatosInvalidos as e:
                messagebox.showwarning("Fecha de pago", str(e), parent=self)
                return
            if f is None:
                messagebox.showwarning("Fecha de pago", "Indique la fecha de pago.", parent=self)
                return
            if f > self.hoy:
                messagebox.showwarning("Fecha de pago", "La fecha de pago no puede ser posterior a hoy.", parent=self)
                return
            fecha_pago = f.isoformat()
        venc = None
        if estado == "pendiente" and self.sin_venc:
            try:
                v = parse_fecha(self.var_venc.get())
            except DatosInvalidos as e:
                messagebox.showwarning("Vencimiento", str(e), parent=self)
                return
            if v is None:
                messagebox.showwarning("Vencimiento", "Para dejarla pendiente hace falta la fecha de vencimiento: "
                                       "indíquela.", parent=self)
                return
            venc = v.isoformat()
        self.resultado = {"estado": estado, "metodo": metodo, "nro_documento": nro, "fecha_pago": fecha_pago,
                          "fecha_vencimiento": venc}
        self.destroy()

    @classmethod
    def preguntar(cls, padre: tk.Misc, f: dict, hoy) -> dict | None:
        d = cls(padre, f, hoy)
        padre.wait_window(d)
        return d.resultado


class DialogoPago(tk.Toplevel):
    """Método de pago: contado, transferencia o cheque. Solo el cheque exige N° de documento."""

    def __init__(self, padre: tk.Misc, titulo: str):
        super().__init__(padre)
        self.title(titulo)
        self.resultado: dict | None = None
        self.transient(padre)
        self.resizable(False, False)
        self.var_metodo = tk.StringVar(value="contado")
        self.var_nro = tk.StringVar()
        barra = ttk.Frame(self, padding=(14, 6, 14, 12))
        barra.pack(side="bottom", fill="x")                           # botones siempre visibles
        self.btn_ok = ttk.Button(barra, text="Aceptar", command=self._aceptar)
        self.btn_ok.pack(side="left", padx=4)
        ttk.Button(barra, text="Cancelar", command=self.destroy).pack(side="left")
        f = ttk.Frame(self, padding=(14, 14, 14, 0))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="¿Con qué método se pagó?").grid(row=0, column=0, columnspan=2, sticky="w")
        self.radios = {}
        for i, (clave, texto) in enumerate((("contado", "Contado"), ("transferencia", "Transferencia"),
                                            ("cheque", "Cheque")), start=1):
            self.radios[clave] = ttk.Radiobutton(f, text=texto, value=clave, variable=self.var_metodo,
                                                 command=self._alternar)
            self.radios[clave].grid(row=i, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="N° de documento (cheque):").grid(row=4, column=0, sticky="w", pady=6)
        self.ent = ttk.Entry(f, textvariable=self.var_nro, width=16, state="disabled")
        self.ent.grid(row=4, column=1, padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        ventanas.posicionar(self, padre)
        self.grab_set()

    def _alternar(self) -> None:
        self.ent.config(state="normal" if self.var_metodo.get() == "cheque" else "disabled")

    def _aceptar(self) -> None:
        metodo, nro = self.var_metodo.get(), self.var_nro.get().strip()
        if metodo == "cheque" and not nro:
            messagebox.showwarning("Falta dato", "Con cheque hay que indicar el N° de documento.", parent=self)
            return
        self.resultado = {"metodo": metodo, "nro_documento": nro if metodo == "cheque" else None}
        self.destroy()

    @classmethod
    def preguntar(cls, padre: tk.Misc, titulo: str) -> dict | None:
        d = cls(padre, titulo)
        padre.wait_window(d)
        return d.resultado
