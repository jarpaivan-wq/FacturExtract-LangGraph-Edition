"""Pestaña Lote: UNA sola tabla con todas las pausas pendientes (generaliza «Por revisar»).

Es solo una vista: cada fila es un run del grafo 1 detenido en su pausa. Nada se guarda hasta confirmar.
Mantiene las ediciones en curso (estado de pago, N° de documento, fecha de alerta) entre refrescos.
«Revisar…» (o un clic en la factura) abre la ventana de revisión con foto y campos editables.
Se actualiza solo desde el hilo de la interfaz.

Estado de los controles de una fila (por_confirmar o en_revision; las filas con error de lectura no se editan):
  · Estado de pago .... editable (no aplica en notas y guías).
  · N° de documento ... solo habilitado si el estado es «Pagada · cheque».
  · Fecha de alerta ... solo visible y editable si el estado es «Pendiente» y la factura generará alerta
                        (toda factura pendiente alerta, sea cual sea su mes de emisión). Precargada con vencimiento − 3 días corridos, o con hoy
                        si esa fecha ya pasó.
  · Confirmar ......... solo en filas listas (por_confirmar); las filas con hallazgos se confirman después de
                        revisarlas («Revisar…»).
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from . import formulario as fm
from . import ventanas
from . import pago as pg
from .modelo import DatosInvalidos
from .validacion import bloqueantes

ROJO, NEUTRO = "#ffcdd2", "#f5f5f5"
OPCIONES = [pg.ETIQUETAS[k] for k in pg.ESTADOS_CONFIG]
ESTADOS_LOTE = ("en_revision", "por_confirmar", "error_reintentable")
ZOOMS = (("Ajustar", None), ("Zoom 150%", 1.5), ("Zoom 250%", 2.5))


def tiene_bloqueantes(f: dict) -> bool:
    return bool(bloqueantes(f.get("hallazgos") or []))


class PanelLote(ttk.Frame):
    def __init__(self, padre, principal):
        super().__init__(padre, padding=6)
        self.principal = principal
        self.servicio, self.repo = principal.servicio, principal.repo
        self._vars: dict[str, dict[str, tk.StringVar]] = {}       # ediciones en curso por factura
        self._base: dict[str, dict[str, str]] = {}                 # valores precargados (para detectar «no tocado»)
        self._mensajes: dict[str, str] = {}                        # último problema de cada fila
        self.filas: dict[str, dict[str, Any]] = {}
        self._firma: tuple | None = None
        self.dialogo: "VentanaRevision | None" = None
        self._construir()

    # ------------------------------------------------------------------ armado
    def _construir(self) -> None:
        barra = ttk.Frame(self)
        barra.pack(fill="x")
        self.btn_todas = ttk.Button(barra, text="Confirmar todas (sin hallazgos)", command=self._confirmar_todas)
        self.btn_todas.pack(side="left")
        self.lbl_resumen = ttk.Label(barra, text="")
        self.lbl_resumen.pack(side="left", padx=12)
        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True, pady=(6, 0))
        self.lienzo_filas = tk.Canvas(cuerpo, highlightthickness=0)
        sy = ttk.Scrollbar(cuerpo, orient="vertical", command=self.lienzo_filas.yview)
        sx = ttk.Scrollbar(cuerpo, orient="horizontal", command=self.lienzo_filas.xview)
        self.lienzo_filas.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y")
        sx.pack(side="bottom", fill="x")
        self.lienzo_filas.pack(fill="both", expand=True)
        self.marco_filas = ttk.Frame(self.lienzo_filas)
        self.lienzo_filas.create_window((0, 0), window=self.marco_filas, anchor="nw")
        self.marco_filas.bind("<Configure>", lambda e: self.lienzo_filas.configure(
            scrollregion=self.lienzo_filas.bbox("all")))
        self.bind_all("<MouseWheel>", self._rueda, add="+")
        self.lbl_vacio = ttk.Label(self, text="No hay facturas pendientes. Procese fotos en la pestaña «Procesar».")

    def _rueda(self, ev) -> None:
        """La rueda del mouse desplaza la tabla solo cuando el puntero está sobre ella."""
        if str(ev.widget).startswith(str(self.lienzo_filas)):
            self.lienzo_filas.yview_scroll(-1 if ev.delta > 0 else 1, "units")

    # ----------------------------------------------------------------- refresco
    def pendientes(self) -> list[dict]:
        return self.repo.listar_por_estado(*ESTADOS_LOTE)

    def refrescar(self) -> None:
        pend = self.pendientes()
        firma = (tuple((f["id"], f["estado"], f["mensaje"]) for f in pend), tuple(sorted(self._mensajes.items())))
        if firma == self._firma:
            return          # sin cambios: no se reconstruyen las filas (así no se pierde el foco al escribir)
        self._firma = firma
        ids = {f["id"] for f in pend}
        for fid in list(self._vars):
            if fid not in ids:
                del self._vars[fid]
                self._base.pop(fid, None)
                self._mensajes.pop(fid, None)
        for w in self.marco_filas.winfo_children():
            w.destroy()
        self.filas = {}
        if pend:
            self.lbl_vacio.pack_forget()
            self._encabezado()
            for i, f in enumerate(pend, start=1):
                self._fila(i, f)
        else:
            self.lbl_vacio.pack(anchor="w", pady=10)
        n = len(pend)
        self.principal.notebook.tab(self.principal.tab_lote, text=f"Lote ({n})" if n else "Lote")
        self.btn_todas.state(["!disabled"] if any(f["estado"] == "por_confirmar" and not tiene_bloqueantes(f)
                                                  for f in pend) else ["disabled"])
        if self.dialogo is not None and self.dialogo.winfo_exists() and self.dialogo.fid not in ids:
            self.dialogo.destroy()          # la factura ya se confirmó, rechazó o salió del lote

    def _encabezado(self) -> None:
        for c, (t, w) in enumerate((("Factura", 24), ("Total", 10), ("Vence", 10), ("Estado de pago", 21),
                                    ("N° documento", 12), ("Fecha alerta", 11), ("", 9), ("", 10),
                                    ("Observación", 30))):
            ttk.Label(self.marco_filas, text=t, font=("Segoe UI", 9, "bold"), width=w).grid(row=0, column=c, sticky="w")

    def _defectos(self, f: dict) -> dict[str, str]:
        campos = f["extraido"] or {}
        hoy = self.servicio.reloj.hoy()
        estado = pg.estado_inicial_sugerido(campos) if campos else "pendiente"
        alerta = pg.alerta_por_defecto(campos.get("fecha_vencimiento"), hoy) if campos else None
        return {"estado": pg.ETIQUETAS[estado], "alerta": fm.hoy_texto(alerta) if alerta else ""}

    def _vars_de(self, f: dict) -> dict[str, tk.StringVar]:
        """Variables de la fila. Si la factura cambió (p. ej. tras corregirla) y el usuario no había tocado
        un campo, se vuelve a precargar con el valor nuevo."""
        nuevos = self._defectos(f)
        if f["id"] not in self._vars:
            self._vars[f["id"]] = {"estado": tk.StringVar(value=nuevos["estado"]), "nro": tk.StringVar(),
                                   "alerta": tk.StringVar(value=nuevos["alerta"])}
        else:
            v, base = self._vars[f["id"]], self._base[f["id"]]
            for k in ("estado", "alerta"):
                if v[k].get() == base[k]:
                    v[k].set(nuevos[k])
        self._base[f["id"]] = nuevos
        return self._vars[f["id"]]

    def _fila(self, i: int, f: dict) -> None:
        campos = f["extraido"] or {}
        fid = f["id"]
        rojo = tiene_bloqueantes(f) or f["estado"] == "error_reintentable"
        bg = ROJO if rojo else NEUTRO
        v = self._vars_de(f)
        w: dict[str, Any] = {"vars": v, "estado": f["estado"], "campos": campos,
                             "editable": f["estado"] in ("por_confirmar", "en_revision"),
                             "alertable": pg.es_alertable(campos.get("tipo_documento")), "hallazgos": f["hallazgos"],
                             "mensaje_base": self._observacion(f)}
        doc = (f"Folio {campos['folio']}\n{campos.get('razon_social_emisor') or campos.get('rut_emisor') or ''}"
               if campos.get("folio") else Path(f["imagenes"][0]).parent.name[:8] if f["imagenes"] else fid[:8])
        lbl = tk.Label(self.marco_filas, text=doc, bg=bg, anchor="w", justify="left", width=24, cursor="hand2")
        lbl.grid(row=i, column=0, sticky="we", pady=1)
        lbl.bind("<Button-1>", lambda e, fid=fid: self.abrir_revision(fid))
        w["etiqueta"] = lbl
        tk.Label(self.marco_filas, text=fm.miles(campos.get("total")), bg=bg, anchor="e", width=10).grid(
            row=i, column=1, sticky="we", pady=1)
        tk.Label(self.marco_filas, text=fm.fecha_a_texto(campos.get("fecha_vencimiento")), bg=bg, width=10).grid(
            row=i, column=2, sticky="we", pady=1)
        w["combo"] = ttk.Combobox(self.marco_filas, textvariable=v["estado"], values=OPCIONES, state="readonly",
                                  width=20)
        w["combo"].grid(row=i, column=3, padx=2)
        w["nro"] = ttk.Entry(self.marco_filas, textvariable=v["nro"], width=12)
        w["nro"].grid(row=i, column=4, padx=2)
        celda = ttk.Frame(self.marco_filas)
        celda.grid(row=i, column=5, padx=2)
        w["alerta"] = ttk.Entry(celda, textvariable=v["alerta"], width=11)
        w["sin_alerta"] = ttk.Label(celda, text="—", width=11, anchor="center")
        w["ver"] = ttk.Button(self.marco_filas, text="Ver…", width=7, command=lambda: self.abrir_revision(fid))
        w["ver"].grid(row=i, column=6, padx=2)
        if f["estado"] == "por_confirmar":
            w["accion"] = ttk.Button(self.marco_filas, text="Confirmar", width=10, command=lambda: self._confirmar_fila(fid))
        elif f["estado"] == "en_revision":
            w["accion"] = ttk.Button(self.marco_filas, text="Revisar…", width=10, command=lambda: self.abrir_revision(fid))
        else:
            w["accion"] = ttk.Button(self.marco_filas, text="Reintentar", width=10,
                                     command=lambda: self.servicio.reintentar(fid))
        w["accion"].grid(row=i, column=7, padx=2)
        w["obs"] = tk.Label(self.marco_filas, bg=bg, anchor="w", justify="left", wraplength=230, width=30)
        w["obs"].grid(row=i, column=8, sticky="we", pady=1)
        w["combo"].bind("<<ComboboxSelected>>", lambda e: self._al_cambiar_estado(fid))
        for entrada in (w["nro"], w["alerta"]):                      # Enter confirma la fila (si está lista)
            entrada.bind("<Return>", lambda e, fid=fid: self._enter(fid))
        self.filas[fid] = w
        self._aplicar_estado_fila(fid)

    @staticmethod
    def _observacion(f: dict) -> str:
        if f["estado"] == "error_reintentable":
            return f["mensaje"] or "No se pudo leer la factura."
        h = f.get("hallazgos") or []
        if f["estado"] == "en_revision":
            return f"{len(bloqueantes(h))} hallazgo(s): pulse «Revisar…»."
        return f"{len(h)} advertencia(s)" if h else ""

    def _clave_estado(self, fid: str) -> str:
        return pg.POR_ETIQUETA.get(self._vars[fid]["estado"].get(), "pendiente")

    def _al_cambiar_estado(self, fid: str) -> None:
        v, w = self._vars[fid], self.filas.get(fid)
        if w is None:
            return
        if self._clave_estado(fid) == "pendiente" and not v["alerta"].get().strip():
            a = pg.alerta_por_defecto(w["campos"].get("fecha_vencimiento"), self.servicio.reloj.hoy())
            v["alerta"].set(fm.hoy_texto(a) if a else "")
        self._aplicar_estado_fila(fid)

    def alerta_aplica(self, fid: str) -> bool:
        """¿Esta fila generará alerta? Toda factura pendiente (no pagada)."""
        w = self.filas[fid]
        return w["alertable"] and self._clave_estado(fid) == "pendiente"

    def _aplicar_estado_fila(self, fid: str) -> None:
        w = self.filas[fid]
        clave = self._clave_estado(fid)
        habil = w["editable"] and w["alertable"]
        w["combo"].config(state="readonly" if habil else "disabled")
        w["nro"].config(state="normal" if habil and clave == "cheque" else "disabled")
        if habil and self.alerta_aplica(fid):
            w["sin_alerta"].pack_forget()
            w["alerta"].config(state="normal")
            w["alerta"].pack()
        else:
            w["alerta"].pack_forget()
            w["sin_alerta"].pack()
        if not w["alertable"]:
            self._vars[fid]["estado"].set("No aplica")
        msg = self._mensajes.get(fid)
        w["obs"].config(text=msg or w["mensaje_base"],
                        fg="#b71c1c" if msg else "black")

    # ------------------------------------------------------------- confirmación
    def _item(self, fid: str) -> dict:
        v = self._vars[fid]
        return {"id": fid, "pago": {"estado": self._clave_estado(fid), "nro_documento": v["nro"].get(),
                                    "fecha_alerta": v["alerta"].get()}}

    def _enter(self, fid: str) -> None:
        if self.filas.get(fid, {}).get("estado") == "por_confirmar":
            self._confirmar_fila(fid)

    def _confirmar_fila(self, fid: str) -> None:
        self._mensajes.pop(fid, None)
        self.servicio.confirmar_lote([self._item(fid)])

    def _confirmar_todas(self) -> None:
        items = [self._item(f["id"]) for f in self.pendientes()
                 if f["estado"] == "por_confirmar" and not tiene_bloqueantes(f)]
        if not items:
            return
        if not messagebox.askyesno("Confirmar todas", f"¿Confirmar {len(items)} factura(s) con el estado de pago, "
                                   "N° de documento y fecha de alerta que ve en pantalla?\n\n"
                                   "Las filas en rojo (con hallazgos) no se incluyen: se revisan y confirman una a una.",
                                   parent=self.principal):
            return
        for it in items:
            self._mensajes.pop(it["id"], None)
        self.servicio.confirmar_lote(items)

    def al_lote_confirmado(self, resultados: dict[str, dict]) -> None:
        ok = sum(1 for r in resultados.values() if r["ok"])
        mal = len(resultados) - ok
        for fid, r in resultados.items():
            if not r["ok"]:
                self._mensajes[fid] = r["mensaje"]
        self.lbl_resumen.config(text=f"{ok} confirmada(s)" + (f", {mal} con problema (vea la columna Observación)"
                                                              if mal else "."))

    # ------------------------------------------------------------------ revisión
    def abrir_revision(self, fid: str) -> None:
        """Abre (o reutiliza) la ventana de revisión de esa factura: foto, campos y decisiones."""
        f = self.repo.obtener(fid)
        if not f or f["estado"] not in ESTADOS_LOTE:
            return
        if self.dialogo is None or not self.dialogo.winfo_exists():
            self.dialogo = VentanaRevision(self.principal, self)
        self.dialogo.cargar(f)
        self.dialogo.deiconify()
        self.dialogo.lift()
        self.dialogo.focus_force()


class VentanaRevision(tk.Toplevel):
    """Foto y campos de UNA factura del lote. Con hallazgos (en_revision) los campos son editables y se puede
    aprobar tal cual, corregir y validar, o rechazar. Las ya listas (por_confirmar) se ven en solo lectura."""

    def __init__(self, principal, panel: PanelLote):
        super().__init__(principal)
        self.principal, self.panel = principal, panel
        self.servicio = panel.servicio
        self.title("Revisión de factura")
        self.fid: str | None = None
        self._f: dict | None = None
        self._imagenes: list[str] = []
        self._i_foto, self._i_zoom = 0, 0
        self._foto: ImageTk.PhotoImage | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self._entradas: list[tk.Widget] = []
        self._construir()
        ventanas.posicionar(self, principal, 1120, 680, minimo=(600, 380))   # dentro del área útil, centrada
        self.bind("<Escape>", lambda e: self.destroy())

    def _construir(self) -> None:
        # Barra de acciones FIJA abajo (siempre visible); si el contenido es largo, se desplaza el contenido.
        acciones = ttk.Frame(self, padding=(8, 6))
        acciones.pack(side="bottom", fill="x")
        ttk.Separator(self).pack(side="bottom", fill="x")
        self.barra_acciones = acciones
        self.btn_aprobar = ttk.Button(acciones, text="Aprobar tal cual", command=lambda: self.decidir("aprobar"))
        self.btn_corregir = ttk.Button(acciones, text="Corregir y validar", command=lambda: self.decidir("corregir"))
        self.btn_rechazar = ttk.Button(acciones, text="Rechazar", command=self.rechazar)
        self.btn_reintentar = ttk.Button(acciones, text="Reintentar lectura", command=self.reintentar)
        self.btn_cerrar = ttk.Button(acciones, text="Cerrar", command=self.destroy)
        for w in (self.btn_aprobar, self.btn_corregir, self.btn_rechazar, self.btn_reintentar, self.btn_cerrar):
            w.pack(side="left", padx=2)
        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True)
        self.derecha = ventanas.ContenidoDesplazable(cuerpo)
        self.derecha.pack(side="right", fill="y")                       # primero: la columna de datos conserva su ancho
        izq = ttk.Frame(cuerpo, padding=6)
        izq.pack(side="left", fill="both", expand=True)
        barra = ttk.Frame(izq)
        barra.pack(fill="x")
        self.btn_foto = [ttk.Button(barra, text=f"Foto {i + 1}", command=lambda i=i: self._ver_foto(i)) for i in range(2)]
        for b in self.btn_foto:
            b.pack(side="left")
        self.btn_zoom = ttk.Button(barra, text=ZOOMS[0][0], command=self._ciclar_zoom)
        self.btn_zoom.pack(side="left", padx=8)
        cont = ttk.Frame(izq)
        cont.pack(fill="both", expand=True)
        self.lienzo = tk.Canvas(cont, bg="#444", highlightthickness=0, width=240, height=200)
        sy = ttk.Scrollbar(cont, orient="vertical", command=self.lienzo.yview)
        sx = ttk.Scrollbar(cont, orient="horizontal", command=self.lienzo.xview)
        self.lienzo.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y")
        sx.pack(side="bottom", fill="x")
        self.lienzo.pack(fill="both", expand=True)
        self.lienzo.bind("<Configure>", lambda e: self._pintar_foto())
        der = ttk.Frame(self.derecha.contenido, padding=6)
        der.pack(fill="both", expand=True)
        self.lbl_titulo = ttk.Label(der, text="", font=("Segoe UI", 11, "bold"))
        self.lbl_titulo.pack(anchor="w")
        self.lbl_hallazgos = tk.Label(der, text="", justify="left", anchor="nw", fg="#b71c1c", wraplength=430)
        self.lbl_hallazgos.pack(fill="x")
        form = ttk.Frame(der)
        form.pack(fill="x", pady=4)
        for i, (clave, etiqueta) in enumerate(fm.CAMPOS_FORMULARIO):
            ttk.Label(form, text=etiqueta).grid(row=i, column=0, sticky="w", pady=1)
            v = tk.StringVar()
            self.vars[clave] = v
            w = (ttk.Combobox(form, textvariable=v, values=fm.opciones_tipo(), width=28)
                 if clave == "tipo_documento" else ttk.Entry(form, textvariable=v, width=31))
            w.grid(row=i, column=1, sticky="w", padx=4, pady=1)
            self._entradas.append(w)
        ttk.Label(der, text=f"Líneas:  {fm.ENCABEZADO_LINEAS}", font=("Segoe UI", 8)).pack(anchor="w")
        self.txt_lineas = tk.Text(der, height=6, width=54, wrap="none")
        self.txt_lineas.pack(fill="x")

    # ------------------------------------------------------------------ contenido
    def cargar(self, f: dict) -> None:
        self.fid, self._f = f["id"], f
        self._imagenes = f["imagenes"]
        self._i_foto = 0
        for i, b in enumerate(self.btn_foto):
            b.state(["!disabled"] if i < len(self._imagenes) else ["disabled"])
        estado = f["estado"]
        revision = estado == "en_revision"
        campos = f["extraido"] or {}
        self.lbl_titulo.config(text=(f"Folio {campos['folio']}" if campos.get("folio") else "Factura sin leer")
                               + {"en_revision": "  ·  requiere revisión", "por_confirmar": "  ·  lista (solo lectura)",
                                  "error_reintentable": "  ·  error de lectura"}[estado])
        self.title(f"Revisión de factura — {self.lbl_titulo.cget('text')}")
        for b in (self.btn_aprobar, self.btn_corregir):
            b.state(["!disabled"] if revision else ["disabled"])
        self.btn_rechazar.state(["!disabled"] if estado != "error_reintentable" else ["disabled"])
        self.btn_reintentar.state(["!disabled"] if estado == "error_reintentable" else ["disabled"])
        for clave, val in fm.campos_a_formulario(campos).items():
            self.vars[clave].set(val)
        for w in self._entradas:
            if isinstance(w, ttk.Combobox):
                w.config(state="normal" if revision else "disabled")
            else:
                w.config(state="normal" if revision else "readonly")
        self.txt_lineas.config(state="normal")
        self.txt_lineas.delete("1.0", "end")
        self.txt_lineas.insert("1.0", fm.lineas_a_texto(campos.get("lineas", [])))
        self.txt_lineas.config(state="normal" if revision else "disabled")
        if estado == "error_reintentable":
            self.lbl_hallazgos.config(text=f["mensaje"] or "No se pudo leer la factura.")
        else:
            self.lbl_hallazgos.config(text="\n".join(("• " if h["gravedad"] == "bloqueante" else "◦ ") + h["mensaje"]
                                                     for h in (f["hallazgos"] or [])))
        self._pintar_foto()

    def _ver_foto(self, i: int) -> None:
        self._i_foto = i
        self._pintar_foto()

    def _ciclar_zoom(self) -> None:
        self._i_zoom = (self._i_zoom + 1) % len(ZOOMS)
        self.btn_zoom.config(text=ZOOMS[self._i_zoom][0])
        self._pintar_foto()

    def _pintar_foto(self) -> None:
        if not self._imagenes or self._i_foto >= len(self._imagenes):
            return
        try:
            with Image.open(self._imagenes[self._i_foto]) as im:
                im = im.convert("RGB")
                ancho_base = max(self.lienzo.winfo_width() - 4, 300)
                factor = ZOOMS[self._i_zoom][1] or 1.0
                ancho = int(ancho_base * factor)
                im = im.resize((ancho, max(int(im.height * ancho / im.width), 1)))
            self._foto = ImageTk.PhotoImage(im)
            self.lienzo.delete("all")
            self.lienzo.create_image(0, 0, anchor="nw", image=self._foto)
            self.lienzo.configure(scrollregion=(0, 0, im.width, im.height))
        except Exception:
            self.lienzo.delete("all")
            self.lienzo.create_text(20, 20, anchor="nw", fill="white", text="No se pudo mostrar la foto.")

    # ----------------------------------------------------------------- decisiones
    def decidir(self, accion: str) -> None:
        f = self._f
        if not f or f["estado"] != "en_revision":
            return
        decision: dict = {"accion": accion}
        if accion == "corregir":
            try:
                decision["campos"] = fm.formulario_a_campos({k: v.get() for k, v in self.vars.items()},
                                                            self.txt_lineas.get("1.0", "end"), f["extraido"])
            except DatosInvalidos as e:
                messagebox.showwarning("Revisar datos", str(e), parent=self)
                return
        self.servicio.decidir_revision(f["id"], decision)
        self.destroy()

    def rechazar(self) -> None:
        f = self._f
        if not f or not messagebox.askyesno("Rechazar", "¿Rechazar esta factura? No se guardará.", parent=self):
            return
        if f["estado"] == "en_revision":
            self.servicio.decidir_revision(f["id"], {"accion": "rechazar"})
        elif f["estado"] == "por_confirmar":
            self.servicio.confirmar_lote([{"id": f["id"], "accion": "rechazar"}])
        self.destroy()

    def reintentar(self) -> None:
        if self._f and self._f["estado"] == "error_reintentable":
            self.servicio.reintentar(self._f["id"])
            self.destroy()
