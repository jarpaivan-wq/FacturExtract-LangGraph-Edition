"""Editar pago desde la pestaña Facturas: las seis comprobaciones esenciales.

«Hoy» es 2026-10-12 09:30. Cinco son reglas de la base (una transacción); la última usa la ventana REAL de Tkinter.
"""
from datetime import date, datetime
from tkinter import messagebox

import pytest

from facturextract.db import DecisionInvalida
from facturextract.ui import DialogoEditarPago
from tests.factura_ejemplo import factura
from tests.ui_utils import abrir_app, bombear, clic, descendientes, ir_a_pestana, visible

HOY = date(2026, 10, 12)
AHORA = datetime(2026, 10, 12, 9, 30)


def pagada(metodo, nro=None):
    return {"estado_pago": "pagada", "metodo_pago": metodo, "nro_documento": nro, "fecha_alerta": None}


def cargar(repo, fid="a", pago=None, **kw):
    repo.crear_factura(fid, [], AHORA)
    repo.guardar_factura(fid, factura(**kw), [], HOY, False, pago)


def seg(repo):
    return repo._leer("SELECT * FROM seguimientos ORDER BY id")


def corregir(repo, fid="a", estado="pagada", metodo="contado", nro=None, fecha=None, venc=None):
    return repo.corregir_pago(fid, HOY, AHORA, estado, metodo, nro, fecha, venc)


def test_pendiente_a_pagada_con_cheque(repo):
    cargar(repo, fecha_vencimiento="2026-10-14")
    r = corregir(repo, estado="pagada", metodo="cheque", nro="4411")
    f = repo.obtener("a")
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"], f["fecha_pago"]) == ("pagada", "cheque", "4411", "2026-10-12")
    assert f["fecha_vencimiento"] == "2026-10-14" and r["seguimiento"] == "cerrado"
    assert [s["estado"] for s in seg(repo)] == ["pagada"] and repo.seguimientos_esperando() == []


def test_pagada_a_pendiente_recrea_la_alerta(repo):
    cargar(repo, pago=pagada("cheque", "9001"), fecha_vencimiento="2026-11-04")
    assert seg(repo) == []
    corregir(repo, estado="pendiente")
    f = repo.obtener("a")
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"], f["fecha_pago"]) == ("pendiente", None, None, None)
    s = seg(repo)
    assert len(s) == 1 and (s[0]["estado"], s[0]["proxima_alerta"]) == ("pendiente", "2026-11-01")   # venc − 3 días


def test_pagada_a_pendiente_sin_vencimiento_exige_la_fecha(repo):
    cargar(repo, pago=pagada("contado"), fecha_vencimiento=None)
    with pytest.raises(DecisionInvalida, match="vencimiento"):
        corregir(repo, estado="pendiente")
    assert repo.obtener("a")["estado_pago"] == "pagada" and seg(repo) == []
    corregir(repo, estado="pendiente", venc="2026-10-30")
    assert repo.obtener("a")["fecha_vencimiento"] == "2026-10-30" and seg(repo)[0]["proxima_alerta"] == "2026-10-27"


def test_cheque_sin_numero_no_deja_guardar(repo):
    cargar(repo, fecha_vencimiento="2026-10-14")
    antes = (repo.obtener("a"), [dict(s) for s in seg(repo)])
    for nro in (None, "", "   "):
        with pytest.raises(DecisionInvalida, match="N° de documento"):
            corregir(repo, estado="pagada", metodo="cheque", nro=nro)
    assert repo.obtener("a") == antes[0] and [dict(s) for s in seg(repo)] == antes[1]
    assert repo.correcciones_de("a") == []


def test_ir_y_volver_varias_veces_no_duplica_seguimientos(repo):
    cargar(repo, fecha_vencimiento="2026-10-14")
    for i in range(6):
        corregir(repo, estado="pagada", metodo="transferencia")
        assert [s["estado"] for s in seg(repo)] == ["pagada"], i
        corregir(repo, estado="pendiente")
        assert len(seg(repo)) == 1 and seg(repo)[0]["estado"] == "pendiente", i
    assert repo.obtener("a")["fecha_vencimiento"] == "2026-10-14"


# ========================================================================== ventana real (Tkinter)
@pytest.fixture
def app(r, repo, reloj, monkeypatch):
    for nombre in ("showwarning", "showinfo", "showerror"):
        monkeypatch.setattr(messagebox, nombre, lambda t, m, **k: None)
    cargar(repo, "f1", folio="1", fecha_vencimiento="2026-10-14")
    s, v = abrir_app(r, repo, reloj, [])
    ir_a_pestana(v, s, "Facturas")
    yield s, v
    s.detener()
    v.destroy()


def test_el_boton_editar_pago_existe_en_la_ventana_real_y_se_habilita_al_seleccionar(app):
    s, v = app
    # Los botones de la barra de la pestaña Facturas (la fila de los filtros «Mes de emisión» y «Pago»)
    botones = {b.cget("text"): b for b in descendientes(v.notebook)
               if b.winfo_class() == "TButton" and b.winfo_rooty() < v.arbol_fac.winfo_rooty() and b.winfo_viewable()}
    assert "Editar pago…" in botones and "Marcar pagada" in botones            # los dos, uno junto al otro
    editar, marcar = botones["Editar pago…"], botones["Marcar pagada"]
    assert visible(editar) and visible(marcar)
    assert abs(editar.winfo_rooty() - marcar.winfo_rooty()) <= 2               # misma fila
    assert 0 < marcar.winfo_rootx() - (editar.winfo_rootx() + editar.winfo_width()) < 40    # contiguos
    assert "disabled" in editar.state()                                         # sin fila: se ve, pero deshabilitado
    v.arbol_fac.selection_set("f1")
    v.arbol_fac.update()
    assert "disabled" not in editar.state()                                     # con fila: habilitado
    v.arbol_fac.selection_set(())
    v.arbol_fac.update()
    assert "disabled" in editar.state()
    # doble clic sobre la fila = Editar pago…
    abierto = []
    v.after(150, lambda: [abierto.append(1), [w.destroy() for w in descendientes(v) if isinstance(w, DialogoEditarPago)]])
    x, y, _w, _h = v.arbol_fac.bbox("f1")
    for _ in range(2):
        v.arbol_fac.event_generate("<ButtonPress-1>", x=x + 20, y=y + 5)
        v.arbol_fac.event_generate("<ButtonRelease-1>", x=x + 20, y=y + 5)
    bombear(v, s, 2)
    assert abierto == [1] and v.arbol_fac.selection() == ("f1",)
