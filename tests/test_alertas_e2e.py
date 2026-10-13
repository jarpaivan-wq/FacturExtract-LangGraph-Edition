"""Sistema de alertas de punta a punta: carga por la interfaz REAL (pestaña Lote), reloj simulado, planificador,
grafo 2, ventana «Vencimientos del día» con clics, diálogos reales, bandeja y reapertura de la aplicación.

«Hoy» es 2026-10-12 09:30 salvo que la prueba lo cambie con `ir_a`. La hora de los avisos es 09:00 (por defecto).
"""
import sqlite3
import time
import tkinter
from datetime import datetime
from tkinter import messagebox

import pytest
from PIL import Image

from facturextract.extractor import ExtractorFalso
from facturextract.ui import DialogoAvisos, DialogoPago
from facturextract.ui_vencimientos import DialogoFecha
from tests.factura_ejemplo import factura
from tests.ui_utils import abrir_app, bombear, clic, escribir, procesar, visible


def F(folio, venc, **kw):
    return factura(folio=str(folio), fecha_vencimiento=venc, **kw)


def descendientes(w):
    for h in w.winfo_children():
        yield h
        yield from descendientes(h)


def fecha(texto):
    return datetime.strptime(texto if " " in texto else texto + " 09:30", "%Y-%m-%d %H:%M")


class Mundo:
    """La aplicación abierta + el reloj simulado + atajos para actuar como el usuario."""

    def __init__(self, r, repo, reloj, fotos):
        self.r, self.repo, self.reloj, self.fotos = r, repo, reloj, fotos
        self.s = self.v = None
        self.abrir()

    def abrir(self):
        self.s, self.v = abrir_app(self.r, self.repo, self.reloj, [])
        bombear(self.v, self.s, 3)

    def cerrar(self):
        self.s.detener()
        self.v.destroy()

    def reabrir(self):
        self.cerrar()
        self.abrir()

    # ---- el reloj
    def ir_a(self, texto):
        self.reloj.fijar(fecha(texto))
        self.s.revisar_vencimientos()
        bombear(self.v, self.s, 3)

    def avanzar(self, **kw):
        self.reloj.avanzar(**kw)
        self.s.revisar_vencimientos()
        bombear(self.v, self.s, 3)

    # ---- cargar facturas por la pestaña Lote
    def cargar(self, *facturas):
        self.s.extractor.respuestas.extend(facturas)
        procesar(self.v, self.s, self.fotos[:len(facturas)])
        p = self.v.panel_lote
        clic(p.btn_todas)                                           # «Confirmar todas» (askyesno parchado a Sí)
        bombear(self.v, self.s, 4)

    # ---- consultas
    def esperando(self):
        return self.repo.seguimientos_esperando()

    def seguimientos(self):
        return self.repo._leer("SELECT * FROM seguimientos ORDER BY id")

    def ventana(self):
        w = getattr(self.v, "_ventana_venc", None)
        return w if w is not None and w.winfo_exists() else None

    def cerrar_ventana(self):
        if self.ventana():
            self.ventana().destroy()
        bombear(self.v, self.s, 2)

    def abrir_ventana_a_mano(self):
        clic(self.v.btn_venc)
        bombear(self.v, self.s, 2)
        return self.ventana()

    def hilos_de_alerta(self):
        con = sqlite3.connect(self.r.checkpoints_db)
        try:
            return sorted(f[0] for f in con.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'alerta-%'"))
        finally:
            con.close()

    def contador(self):
        t = self.v.btn_venc.cget("text")
        return int(t.split("(")[1].rstrip(")")) if "(" in t else 0

    # ---- diálogos reales (modales): se manejan desde un temporizador mientras el clic espera
    def con_dialogo(self, clase, hacer, boton):
        def buscar():
            for w in descendientes(self.v):
                if isinstance(w, clase) and w.winfo_exists():
                    return w

        def cuando():
            d = buscar()
            if d is None:
                ids.append(self.v.after(40, cuando))
                return
            hacer(d)

        def vigilante():
            d = buscar()
            if d is not None:
                d.destroy()
        ids = [self.v.after(80, cuando), self.v.after(6000, vigilante)]
        try:
            clic(boton)
        finally:
            for i in ids:
                try:
                    self.v.after_cancel(i)
                except tkinter.TclError:
                    pass
        bombear(self.v, self.s, 3)

    def accion(self, nombre, folio=None):
        """Pulsa Posponer / Pagada / Cerrar de la fila de la ventana (la primera, o la del folio)."""
        w = self.ventana()
        assert w is not None, "la ventana de vencimientos no está abierta"
        for sid, botones in w.filas.items():
            if folio is None or self.repo.obtener(self.repo.obtener_seguimiento(sid)["factura_id"])["folio"] == str(folio):
                return botones[nombre]
        raise AssertionError("no hay fila para ese folio")


@pytest.fixture
def fotos(tmp_path):
    out = []
    for i in range(6):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (200, 300), "white").save(p)
        out.append(str(p))
    return out


@pytest.fixture
def avisos(monkeypatch):
    """Los cuadros de mensaje no bloquean: se registran. «Confirmar todas» responde Sí."""
    vistos = []
    monkeypatch.setattr(messagebox, "showwarning", lambda t, m, **k: vistos.append(m))
    monkeypatch.setattr(messagebox, "showinfo", lambda t, m, **k: vistos.append(m))
    monkeypatch.setattr(messagebox, "showerror", lambda t, m, **k: vistos.append(m))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    return vistos


@pytest.fixture
def m(r, repo, reloj, fotos, avisos):
    mundo = Mundo(r, repo, reloj, fotos)
    yield mundo
    try:
        mundo.cerrar()
    except tkinter.TclError:
        pass


# ===================================================================== cuándo salta la alerta
def test_la_alerta_salta_3_dias_corridos_antes_del_vencimiento(m):
    m.cargar(F(1, "2026-10-20"))                                    # alerta = 17-oct
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-17" and m.esperando() == []
    for dia in ("13", "14", "15", "16"):
        m.ir_a(f"2026-10-{dia}")
        assert m.esperando() == [] and m.ventana() is None, dia
    m.ir_a("2026-10-17")
    assert len(m.esperando()) == 1 and m.ventana() is not None and m.contador() == 1
    assert m.v.btn_venc.cget("text") == "Vencimientos del día (1)"


def test_vencimiento_a_menos_de_3_dias_alerta_hoy_y_vencida_de_inmediato(m):
    m.cargar(F(1, "2026-10-14"))                                    # vence en 2 días
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-12"
    assert len(m.esperando()) == 1 and m.ventana() is not None      # salta al confirmar, sin esperar 15 minutos
    m.cerrar_ventana()
    m.cargar(F(2, "2026-10-08", fecha_emision="2026-10-01"))        # ya vencida
    assert {s["proxima_alerta"] for s in m.seguimientos()} == {"2026-10-12"}
    assert len(m.esperando()) == 2
    assert m.ventana() is not None                                  # aunque hoy ya se había abierto: es una carga nueva
    textos = " ".join(w.cget("text") for w in descendientes(m.ventana()) if w.winfo_class() == "TLabel")
    assert "VENCIDA hace 4 día(s)" in textos and "vence en 2 día(s)" in textos


def test_alerta_con_vencimiento_justo_a_3_dias_salta_hoy(m):
    m.cargar(F(1, "2026-10-15"))
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-12" and len(m.esperando()) == 1


# =============================================================================== Cerrar
def test_cerrar_hace_que_reaparezca_al_dia_siguiente(m):
    m.cargar(F(1, "2026-10-14"))
    clic(m.accion("cerrar"))
    bombear(m.v, m.s, 3)
    s = m.seguimientos()[0]
    assert (s["estado"], s["proxima_alerta"]) == ("pendiente", "2026-10-13") and m.esperando() == []
    assert m.contador() == 0
    m.ir_a("2026-10-12 15:00")                                      # el resto del día no vuelve
    m.ir_a("2026-10-12 23:45")
    assert m.esperando() == []
    m.ir_a("2026-10-13 09:30")                                      # al día siguiente reaparece
    assert len(m.esperando()) == 1 and m.ventana() is not None
    assert m.repo.obtener(m.esperando()[0]["factura_id"])["fecha_vencimiento"] == "2026-10-14"


# ============================================================================== Posponer
def test_posponer_con_el_dialogo_real_reprograma_sin_tocar_el_vencimiento(m):
    m.cargar(F(1, "2026-10-14"))

    def elegir_fecha(d):
        escribir(d.ent, "20-10-2026")
        clic(d.btn_ok)
    m.con_dialogo(DialogoFecha, elegir_fecha, m.accion("posponer"))
    s = m.seguimientos()[0]
    assert (s["estado"], s["proxima_alerta"]) == ("pendiente", "2026-10-20") and m.esperando() == []
    assert m.repo.obtener(s["factura_id"])["fecha_vencimiento"] == "2026-10-14"      # el vencimiento real no cambia
    m.ir_a("2026-10-19")
    assert m.esperando() == []
    m.ir_a("2026-10-20")
    assert len(m.esperando()) == 1 and m.ventana() is not None


def test_posponer_exige_fecha_posterior_a_hoy(m, avisos):
    m.cargar(F(1, "2026-10-14"))
    for malo in ("12-10-2026", "11-10-2026", "no es fecha"):         # hoy y el pasado no valen
        def poner(d, malo=malo):
            escribir(d.ent, malo)
            clic(d.btn_ok)
        m.con_dialogo(DialogoFecha, poner, m.accion("posponer"))
        assert len(m.esperando()) == 1, malo                        # la alerta sigue esperando
        assert avisos and avisos[-1], malo                           # y se avisó con un mensaje claro
    assert any("posterior a hoy" in a for a in avisos)
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-12"
    m.con_dialogo(DialogoFecha, lambda d: (escribir(d.ent, "13-10-2026", enter=True)), m.accion("posponer"))
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-13"      # «mañana» sí vale (y Enter acepta)


# ================================================================================ Pagada
@pytest.mark.parametrize("metodo", ["contado", "transferencia"])
def test_pagada_con_contado_o_transferencia_no_pide_numero(m, metodo):
    m.cargar(F(1, "2026-10-14"))

    def pagar(d):
        clic(d.radios[metodo])
        assert str(d.ent.cget("state")) == "disabled"               # no hay N° de documento que escribir
        clic(d.btn_ok)
    m.con_dialogo(DialogoPago, pagar, m.accion("pagada"))
    f = m.repo.listar_guardadas()[0]
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"], f["fecha_pago"]) == (
        "pagada", metodo, None, "2026-10-12")
    assert f["fecha_vencimiento"] == "2026-10-14"
    assert m.seguimientos()[0]["estado"] == "pagada" and m.esperando() == []
    for dia in ("2026-10-13", "2026-10-20", "2026-12-01"):           # una factura pagada deja de generar alertas
        m.ir_a(dia)
        assert m.esperando() == [], dia


def test_pagada_con_cheque_exige_numero_de_documento(m, avisos):
    m.cargar(F(1, "2026-10-14"))

    def pagar(d):
        clic(d.radios["cheque"])
        assert str(d.ent.cget("state")) == "normal"
        clic(d.btn_ok)                                              # sin número: el diálogo no avanza
        assert d.winfo_exists() and any("N° de documento" in a for a in avisos)
        escribir(d.ent, "5501")
        clic(d.btn_ok)
    m.con_dialogo(DialogoPago, pagar, m.accion("pagada"))
    f = m.repo.listar_guardadas()[0]
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"]) == ("pagada", "cheque", "5501")
    assert m.esperando() == [] and m.seguimientos()[0]["estado"] == "pagada"
    m.ir_a("2026-12-01")
    assert m.esperando() == []


def test_el_grafo_tambien_valida_si_le_llega_un_cheque_sin_numero(m):
    """Defensa en profundidad: aunque la interfaz fallara, el grafo 2 vuelve a preguntar."""
    m.cargar(F(1, "2026-10-14"))
    sid = m.esperando()[0]["id"]
    out = m.s.planificador.decidir(sid, {"accion": "pagada", "metodo": "cheque", "nro_documento": ""})
    assert out["__interrupt__"][0].value["error"]
    assert m.repo.listar_guardadas()[0]["estado_pago"] == "pendiente" and len(m.esperando()) == 1


# ===================================================================== sin duplicados
def test_un_solo_run_por_seguimiento_y_dia_y_sin_segunda_alerta(m):
    m.cargar(F(1, "2026-10-14"))
    assert len(m.hilos_de_alerta()) == 1
    for _ in range(5):                                               # el planificador revisa muchas veces
        m.avanzar(minutes=15)
        assert len(m.esperando()) == 1
    assert m.hilos_de_alerta() == ["alerta-1-2026-10-12"]
    assert len(m.v._ventana_venc.filas) == 1                         # y una sola fila en la ventana
    m.reabrir()                                                      # ni al cerrar y volver a abrir
    m.ir_a("2026-10-12 12:00")
    assert len(m.esperando()) == 1 and len(m.hilos_de_alerta()) == 1


def test_cada_dia_es_un_run_nuevo_pero_nunca_dos_el_mismo_dia(m):
    m.cargar(F(1, "2026-10-14"))
    clic(m.accion("cerrar"))
    bombear(m.v, m.s, 3)
    m.ir_a("2026-10-13")
    m.ir_a("2026-10-13 10:00")
    assert len(m.hilos_de_alerta()) == 2 and len(m.esperando()) == 1


# =========================================================== ventana: cuándo se abre sola
def test_la_ventana_se_abre_sola_a_la_hora_la_primera_vez_del_dia(m):
    m.cargar(F(1, "2026-10-14"), F(2, "2026-10-15"))                 # ambas con alerta para hoy
    m.cerrar_ventana()
    m.cerrar_ventana()
    m.repo.set_ajuste("ultima_apertura_ventana", "")                  # como si hoy aún no se hubiera abierto
    m.ir_a("2026-10-13 08:00")                                       # mañana, ANTES de la hora (09:00)
    assert m.ventana() is None
    m.ir_a("2026-10-13 08:59")
    assert m.ventana() is None and m.contador() == 2                  # pendientes de ayer: visibles en el contador
    m.ir_a("2026-10-13 09:00")                                       # a la hora: se abre sola
    assert m.ventana() is not None and m.ventana().winfo_viewable()
    m.cerrar_ventana()
    for minutos in (15, 30, 45, 120):                                # el resto del día NO se vuelve a abrir
        m.ir_a(f"2026-10-13 09:{minutos % 60:02d}" if minutos < 60 else f"2026-10-13 {9 + minutos // 60}:00")
        assert m.ventana() is None
        assert m.contador() == 2                                      # solo queda el contador
    m.ir_a("2026-10-14 09:05")                                       # al otro día vuelve a abrirse, primera vez
    assert m.ventana() is not None


def test_la_hora_de_los_avisos_se_puede_configurar(m):
    m.cargar(F(1, "2026-10-14"))
    m.cerrar_ventana()
    m.ir_a("2026-10-12 09:30")
    m.v._ajustes_avisos()                                            # botón «Avisos…»
    dlg = [w for w in descendientes(m.v) if isinstance(w, DialogoAvisos)][0]
    dlg.update()
    assert dlg.var_hora.get() == "09:00" and dlg.var_int.get() == "15"      # valores por defecto
    escribir(dlg.ent_hora, "25:99")
    clic(dlg.btn_guardar)
    assert dlg.winfo_exists()                                         # hora inválida: no guarda
    escribir(dlg.ent_hora, "10:30")
    clic(dlg.btn_guardar)
    bombear(m.v, m.s, 3)
    assert m.repo.ajuste("hora_alerta") == "10:30"
    m.cargar_facturas = None
    m.repo.set_ajuste("ultima_apertura_ventana", "")
    m.ir_a("2026-10-13 09:30")                                       # antes de la nueva hora: no se abre sola
    assert m.ventana() is None
    m.ir_a("2026-10-13 10:30")
    assert m.ventana() is not None


def test_el_contador_se_actualiza_al_resolver_alertas(m):
    m.cargar(F(1, "2026-10-14"), F(2, "2026-10-14"))
    assert m.contador() == 2
    clic(m.accion("cerrar", folio=1))
    bombear(m.v, m.s, 3)
    assert m.contador() == 1 and len(m.ventana().filas) == 1


def test_con_la_aplicacion_oculta_en_la_bandeja_la_ventana_igual_aparece_y_el_contador_se_actualiza(m):
    class BandejaFalsa:
        def __init__(self):
            self.pendientes, self.detenida = None, False

        def actualizar(self, n):
            self.pendientes = n

        def detener(self):
            self.detenida = True
    m.v.bandeja = bandeja = BandejaFalsa()
    m.v.withdraw()                                                    # como al iniciar con Windows (--oculto)
    m.v.update()
    m.cargar_oculto = None
    # se carga la factura directamente en la base (la ventana principal está oculta)
    m.repo.crear_factura("a", [], m.reloj.ahora())
    m.repo.guardar_factura("a", F(1, "2026-10-20"), [], m.reloj.hoy())
    m.ir_a("2026-10-17")
    assert m.v.state() == "withdrawn"                                 # la principal sigue oculta
    assert m.ventana() is not None and m.ventana().winfo_viewable()   # pero el aviso aparece
    assert bandeja.pendientes == 1                                    # y la bandeja muestra el contador


# ============================================================= cerrar y reabrir la aplicación
def test_alerta_pendiente_se_retoma_al_reabrir(m):
    m.cargar(F(1, "2026-10-14"))
    assert len(m.esperando()) == 1
    m.reabrir()                                                       # se cierra con la alerta sin atender
    assert len(m.esperando()) == 1 and m.contador() == 1              # sigue ahí, no se perdió
    assert len(m.hilos_de_alerta()) == 1                              # y no se creó una segunda
    w = m.abrir_ventana_a_mano()
    assert len(w.filas) == 1
    clic(m.accion("cerrar"))                                          # y se puede resolver con normalidad
    bombear(m.v, m.s, 3)
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-13" and m.esperando() == []


def test_alerta_de_un_dia_con_la_aplicacion_cerrada_aparece_al_volver(m):
    m.cargar(F(1, "2026-10-17"))                                      # alerta el 14-oct
    m.cerrar()                                                        # PC apagado el 14, 15 y 16
    m.reloj.fijar(fecha("2026-10-17 10:00"))
    m.abrir()                                                         # vuelve a abrirla el 17
    assert len(m.esperando()) == 1 and m.ventana() is not None        # aparece (y se abre sola: 1.ª vez del día)
    textos = " ".join(w.cget("text") for w in descendientes(m.ventana()) if w.winfo_class() == "TLabel")
    assert "vence HOY" in textos


def test_checkpoints_perdidos_no_pierden_la_alerta(m):
    m.cargar(F(1, "2026-10-14"))
    m.s.detener()
    m.v.destroy()
    for ext in ("", "-wal", "-shm"):
        p = m.r.checkpoints_db.with_name(m.r.checkpoints_db.name + ext)
        if p.exists():
            p.unlink()                                                # checkpoints.db es desechable
    m.abrir()
    assert len(m.esperando()) == 1                                    # se vuelve a lanzar desde facturas.db
    clic(m.abrir_ventana_a_mano() and m.accion("cerrar"))
    bombear(m.v, m.s, 3)
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-13"


# ============================================================ quién genera (y quién no) alerta
@pytest.mark.parametrize("nombre, factura_", [
    ("pagada de contado", lambda: F(1, "2026-10-14", pago_sugerido="contado")),
    ("pagada por transferencia", lambda: F(1, "2026-10-14", pago_sugerido="transferencia")),
    ("pagada con cheque", lambda: F(1, "2026-10-14", pago_sugerido="cheque")),
    ("nota de crédito", lambda: F(1, None, tipo_documento="nota_credito")),
    ("nota de débito", lambda: F(1, None, tipo_documento="nota_debito")),
    ("guía de despacho", lambda: F(1, None, tipo_documento="guia_despacho")),
])
def test_no_generan_alerta(m, nombre, factura_):
    f = factura_()
    if nombre == "pagada con cheque":                                 # el cheque exige N°: se configura a mano
        m.s.extractor.respuestas.append(f)
        procesar(m.v, m.s, m.fotos[:1])
        w = m.v.panel_lote.filas[next(iter(m.v.panel_lote.filas))]
        from tests.ui_utils import elegir
        elegir(w["combo"], "Pagada · cheque")
        escribir(w["nro"], "321")
        clic(w["accion"])
        bombear(m.v, m.s, 4)
    else:
        m.cargar(f)
    assert len(m.repo.listar_guardadas()) == 1, nombre
    assert m.seguimientos() == [] and m.esperando() == [] and m.ventana() is None, nombre
    for dia in ("2026-10-13", "2026-10-14", "2026-10-30", "2026-12-24"):
        m.ir_a(dia)
        assert m.esperando() == [], (nombre, dia)


def test_factura_de_un_mes_anterior_cargada_tarde_genera_alerta_como_cualquier_otra(m):
    m.cargar(F(1, "2026-10-20", fecha_emision="2026-09-25"))          # emitida en septiembre, hoy es octubre
    f = m.repo.listar_guardadas()[0]
    assert f["estado_pago"] == "pendiente" and f["mes_emision"] == "2026-09"      # el Excel sigue siendo el de septiembre
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-17" and m.esperando() == []
    m.ir_a("2026-10-16")
    assert m.esperando() == []
    m.ir_a("2026-10-17")                                              # salta en la fecha correcta
    assert len(m.esperando()) == 1 and m.ventana() is not None
    assert m.r.excel.joinpath("facturas_2026_09.xlsx").exists() and not m.r.excel.joinpath("facturas_2026_10.xlsx").exists()


def test_emitida_el_mes_pasado_con_vencimiento_en_dos_dias_alerta_hoy(m):
    m.cargar(F(1, "2026-10-14", fecha_emision="2026-09-28"))          # el caso real que falló
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-12"
    assert len(m.esperando()) == 1 and m.ventana() is not None


def test_emitida_hace_dos_meses_y_ya_vencida_alerta_hoy(m):
    m.cargar(F(1, "2026-09-05", fecha_emision="2026-08-10"))          # vencida hace más de un mes
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-10-12"
    assert len(m.esperando()) == 1 and m.ventana() is not None
    textos = " ".join(w.cget("text") for w in descendientes(m.ventana()) if w.winfo_class() == "TLabel")
    assert "VENCIDA hace 37 día(s)" in textos
    assert m.repo.listar_guardadas()[0]["mes_emision"] == "2026-08"
    assert m.repo.listar_guardadas()[0]["fecha_vencimiento"] == "2026-09-05"      # el vencimiento no se toca


def test_carga_masiva_de_facturas_antiguas_se_muestra_completa_con_desplazamiento(r, repo, reloj, tmp_path, avisos):
    n = 40
    fotos = []
    for i in range(n):
        p = tmp_path / f"m{i}.jpg"
        Image.new("RGB", (120, 160), "white").save(p)
        fotos.append(str(p))
    mundo = Mundo(r, repo, reloj, fotos)
    try:
        # emitidas en agosto/septiembre; vencimientos entre el 8 y el 17 de octubre (los hasta el 15 alertan hoy)
        facturas = [factura(folio=str(200 + i), fecha_emision=f"2026-{8 + i % 2:02d}-{1 + i % 28:02d}",
                            fecha_vencimiento=f"2026-10-{8 + i % 10:02d}") for i in range(n)]
        debe_alertar = [f for f in facturas if f["fecha_vencimiento"] <= "2026-10-15"]
        assert 0 < len(debe_alertar) < n
        mundo.v.geometry("1250x520+5+5")
        inicio = time.perf_counter()
        mundo.cargar(*facturas)
        bombear(mundo.v, mundo.s, 4)
        segundos = time.perf_counter() - inicio
        assert len(mundo.repo.listar_guardadas()) == n
        assert len(mundo.esperando()) == len(debe_alertar)
        assert len({s["factura_id"] for s in mundo.esperando()}) == len(debe_alertar)    # sin duplicados
        assert len(mundo.hilos_de_alerta()) == len(debe_alertar)                         # un run por seguimiento
        w = mundo.ventana()
        assert w is not None and len(w.filas) == len(debe_alertar)                       # las muestra todas
        w.update()
        assert w.lienzo.yview()[1] < 1.0                                                 # y se pueden recorrer
        antes = w.lienzo.yview()[0]
        w.event_generate("<MouseWheel>", delta=-120)
        w.update()
        assert w.lienzo.yview()[0] > antes
        assert mundo.contador() == len(debe_alertar)
        assert segundos < 90                                                              # la interfaz no se congela
        # la interfaz sigue respondiendo: resolver una alerta actualiza ventana y contador
        clic(mundo.accion("cerrar"))
        bombear(mundo.v, mundo.s, 3)
        assert len(mundo.esperando()) == len(debe_alertar) - 1 and mundo.contador() == len(debe_alertar) - 1
        mundo.avanzar(minutes=15)                                                         # y re-revisar no duplica nada
        assert len(mundo.esperando()) == len(debe_alertar) - 1
    finally:
        mundo.cerrar()


def test_seguimiento_con_vencimiento_en_otro_mes_sigue_vigente(m):
    m.ir_a("2026-10-28 09:30")
    m.cargar(F(1, "2026-11-20", fecha_emision="2026-10-28"))          # emitida en octubre, vence en noviembre
    assert m.seguimientos()[0]["proxima_alerta"] == "2026-11-17"
    m.ir_a("2026-11-05")                                              # ya cambió el mes: el seguimiento sigue vigente
    assert m.esperando() == [] and m.seguimientos()[0]["estado"] == "pendiente"
    m.ir_a("2026-11-16")
    assert m.esperando() == []
    m.ir_a("2026-11-17")
    assert len(m.esperando()) == 1 and m.ventana() is not None


# ============================================================ pagar desde la vista de facturas
def _pagar_desde_la_vista(m, folio, metodo="transferencia", nro=None):
    m.v.notebook.select([i for i in m.v.notebook.tabs() if m.v.notebook.tab(i, "text") == "Facturas"][0])
    bombear(m.v, m.s, 2)
    arbol = m.v.arbol_fac
    fid = [f["id"] for f in m.repo.listar_guardadas() if f["folio"] == str(folio)][0]
    arbol.selection_set(fid)
    arbol.update()

    def pagar(d):
        clic(d.radios[metodo])
        if nro:
            escribir(d.ent, nro)
        clic(d.btn_ok)
    m.con_dialogo(DialogoPago, pagar, m.v.btn_marcar_pagada)
    return fid


def test_pagar_desde_la_vista_sin_esperar_la_alerta_cierra_el_seguimiento(m):
    m.cargar(F(1, "2026-10-25"))                                      # alerta futura (22-oct): aún no esperando
    assert m.esperando() == [] and m.seguimientos()[0]["estado"] == "pendiente"
    fid = _pagar_desde_la_vista(m, 1, "cheque", "9001")
    f = m.repo.obtener(fid)
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"]) == ("pagada", "cheque", "9001")
    assert m.seguimientos()[0]["estado"] == "pagada"
    m.ir_a("2026-10-22")
    m.ir_a("2026-10-25")
    assert m.esperando() == [] and m.ventana() is None                # nunca salta la alerta


def test_pagar_desde_la_vista_mientras_la_alerta_esta_abierta_la_quita_de_la_ventana(m):
    m.cargar(F(1, "2026-10-14"), F(2, "2026-10-14"))
    assert len(m.ventana().filas) == 2
    _pagar_desde_la_vista(m, 1, "contado")
    bombear(m.v, m.s, 3)
    assert len(m.esperando()) == 1 and len(m.ventana().filas) == 1 and m.contador() == 1
    estados = {s["factura_id"]: s["estado"] for s in m.seguimientos()}
    assert sorted(estados.values()) == ["esperando_decision", "pagada"]


def test_la_vista_de_facturas_no_deja_pagar_dos_veces(m, avisos):
    m.cargar(F(1, "2026-10-25"))
    _pagar_desde_la_vista(m, 1, "contado")
    _pagar_desde_la_vista(m, 1, "transferencia")                      # ya pagada: avisa y no cambia nada
    assert any("no tiene pago pendiente" in a for a in avisos)
    assert m.repo.listar_guardadas()[0]["metodo_pago"] == "contado"


# ============================================================ planificador y robustez (servicio)
def test_el_planificador_revisa_cada_15_minutos_y_al_despertar(r, repo, reloj, monkeypatch):
    from facturextract.servicio import Servicio

    s = Servicio(r, repo, ExtractorFalso([]), reloj)
    s.iniciar()
    s.esperar_inactivo()
    cuenta = []
    monkeypatch.setattr(s, "revisar_vencimientos", lambda: cuenta.append(reloj.ahora()))
    s.iniciar_planificador(paso_segundos=0.02)                         # intervalo por defecto: 15 minutos
    try:
        time.sleep(0.2)
        assert cuenta == []
        reloj.avanzar(minutes=14)
        time.sleep(0.2)
        assert cuenta == []                                            # 14 min: todavía no
        reloj.avanzar(minutes=1)
        time.sleep(0.3)
        assert len(cuenta) == 1                                        # 15 min: revisa
        time.sleep(0.2)
        assert len(cuenta) == 1                                        # y no repite sin que pase el tiempo
        reloj.avanzar(hours=5)                                         # el PC estuvo suspendido y despierta
        time.sleep(0.3)
        assert len(cuenta) == 2                                        # revisa de inmediato al notar el salto
    finally:
        s.detener()


def test_el_intervalo_es_configurable(r, repo, reloj, monkeypatch):
    from facturextract.servicio import Servicio

    s = Servicio(r, repo, ExtractorFalso([]), reloj)
    s.iniciar()
    s.esperar_inactivo()
    assert s.ajustes_avisos() == ("09:00", 15.0)
    assert s.guardar_ajustes_avisos("08:15", "5") is None
    s.esperar_inactivo()
    assert s.ajustes_avisos() == ("08:15", 5.0)
    assert s.guardar_ajustes_avisos("8", "5") is not None and s.guardar_ajustes_avisos("09:00", "0") is not None
    cuenta = []
    monkeypatch.setattr(s, "revisar_vencimientos", lambda: cuenta.append(1))
    s.iniciar_planificador(paso_segundos=0.02)
    try:
        reloj.avanzar(minutes=4)
        time.sleep(0.2)
        assert cuenta == []
        reloj.avanzar(minutes=1)
        time.sleep(0.3)
        assert len(cuenta) == 1
    finally:
        s.detener()


def test_un_fallo_de_la_revision_periodica_no_molesta_al_usuario(r, repo, reloj, monkeypatch):
    from facturextract.servicio import Servicio

    s = Servicio(r, repo, ExtractorFalso([]), reloj)
    s.iniciar()
    s.esperar_inactivo()
    while not s.eventos.empty():
        s.eventos.get()
    monkeypatch.setattr(s.planificador, "revisar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    s.revisar_vencimientos()
    s.esperar_inactivo()
    tipos = []
    while not s.eventos.empty():
        tipos.append(s.eventos.get().tipo)
    s.detener()
    assert "error" not in tipos                                        # se registra en el log, sin popup cada 15 min
