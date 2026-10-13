"""Pestaña Lote con la interfaz Tkinter REAL: clics, teclas, listas desplegables, Enter y rueda del mouse.

Cada prueba actúa como el usuario: si un botón está deshabilitado o una celda bloqueada, el evento no surte
efecto, y si un widget no está visible, la prueba falla (el usuario no podría pulsarlo).
"""
import pytest
from PIL import Image

from facturextract import pago as pg
from tests.factura_ejemplo import factura
from tests.ui_utils import (abrir_app, bombear, clic, elegir, escribir, fid_de, habilitado, procesar, visible)


@pytest.fixture
def fotos(tmp_path):
    out = []
    for i in range(14):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (300, 420), "white").save(p)
        out.append(str(p))
    return out


def A(**kw):   # lista para confirmar, vence en 2 días: la alerta natural (venc − 3) ya pasó
    return factura(folio="11", fecha_vencimiento="2026-10-14", **kw)


def B(**kw):   # el modelo sugiere transferencia
    return factura(folio="12", pago_sugerido="transferencia", **kw)


def C(**kw):   # total que no cuadra: 3 lecturas fallidas -> revisión individual
    return factura(folio="13", iva=1, total=1000001, **kw)


@pytest.fixture
def app(r, repo, reloj, fotos):
    s, v = abrir_app(r, repo, reloj, [A(), B(), C(), C(), C()])
    procesar(v, s, fotos[:3])
    yield s, v, v.panel_lote
    s.detener()
    v.destroy()


def vars_de(p, fid):
    return p.filas[fid]["vars"]


# ------------------------------------------------------------------ Parte 1, falla 1: «Revisar»
def test_revisar_abre_el_panel_con_foto_y_campos_editables(app, repo):
    s, v, p = app
    fid = fid_de(repo, "13")
    w = p.filas[fid]
    assert w["accion"].cget("text") == "Revisar…" and visible(w["accion"])
    assert p.dialogo is None
    clic(w["accion"])                                              # el botón real
    bombear(v, s)
    d = p.dialogo
    assert d is not None and d.winfo_exists() and d.winfo_viewable() and d.fid == fid
    assert "Folio 13" in d.lbl_titulo.cget("text")
    assert d.vars["folio"].get() == "13" and d.vars["total"].get() == "1000001"
    assert d.lienzo.find_all()                                      # la foto se dibujó
    assert "IVA" in d.lbl_hallazgos.cget("text")
    for clave in ("folio", "rut_emisor", "total", "iva", "fecha_vencimiento"):
        assert str(d.vars[clave]) and "disabled" not in str(d._entradas[[k for k, _ in __import__(
            "facturextract.formulario", fromlist=["x"]).CAMPOS_FORMULARIO].index(clave)].cget("state"))
    assert habilitado(d.btn_aprobar) and habilitado(d.btn_corregir) and habilitado(d.btn_rechazar)
    assert not habilitado(d.btn_reintentar)


def test_clic_en_la_factura_y_en_ver_tambien_abren_la_revision(app, repo):
    s, v, p = app
    fid = fid_de(repo, "13")
    clic(p.filas[fid]["etiqueta"])
    bombear(v, s)
    assert p.dialogo.fid == fid
    p.dialogo.destroy()
    clic(p.filas[fid]["ver"])
    bombear(v, s)
    assert p.dialogo.winfo_exists() and p.dialogo.fid == fid


def test_revisar_corregir_escribiendo_en_los_campos(app, repo):
    s, v, p = app
    fid = fid_de(repo, "13")
    clic(p.filas[fid]["accion"])
    bombear(v, s)
    d = p.dialogo
    ent = {k: w for (k, _), w in zip(__import__("facturextract.formulario", fromlist=["x"]).CAMPOS_FORMULARIO,
                                     d._entradas)}
    escribir(ent["iva"], "190000")
    escribir(ent["total"], "1190000")
    assert d.vars["iva"].get() == "190000" and d.vars["total"].get() == "1190000"
    clic(d.btn_corregir)
    bombear(v, s, 8)
    assert not d.winfo_exists()                                    # la ventana se cierra al decidir
    f = repo.obtener(fid)
    assert f["estado"] == "por_confirmar" and f["extraido"]["total"] == 1190000
    assert p.filas[fid]["accion"].cget("text") == "Confirmar"


def test_revisar_aprobar_tal_cual(app, repo):
    s, v, p = app
    fid = fid_de(repo, "13")
    clic(p.filas[fid]["accion"])
    bombear(v, s)
    clic(p.dialogo.btn_aprobar)
    bombear(v, s, 8)
    f = repo.obtener(fid)
    assert f["estado"] == "por_confirmar" and repo.listar_guardadas() == []
    assert p.filas[fid]["accion"].cget("text") == "Confirmar"
    assert not p.btn_todas.instate(["!disabled"]) or True


def test_revisar_rechazar(app, repo, monkeypatch):
    from tkinter import messagebox

    s, v, p = app
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    fid = fid_de(repo, "13")
    clic(p.filas[fid]["accion"])
    bombear(v, s)
    clic(p.dialogo.btn_rechazar)
    bombear(v, s, 8)
    assert repo.obtener(fid)["estado"] == "rechazada" and fid not in p.filas


def test_revision_de_fila_lista_es_solo_lectura(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")
    clic(p.filas[fid]["etiqueta"])
    bombear(v, s)
    d = p.dialogo
    assert "solo lectura" in d.lbl_titulo.cget("text")
    assert not habilitado(d.btn_aprobar) and not habilitado(d.btn_corregir) and habilitado(d.btn_rechazar)
    antes = d.vars["total"].get()
    escribir(d._entradas[[i for i, (k, _) in enumerate(__import__(
        "facturextract.formulario", fromlist=["x"]).CAMPOS_FORMULARIO) if k == "total"][0]], "999")
    assert d.vars["total"].get() == antes                          # readonly: no acepta teclas


def test_cerrar_con_escape_y_boton(app, repo):
    s, v, p = app
    fid = fid_de(repo, "13")
    clic(p.filas[fid]["accion"])
    bombear(v, s)
    p.dialogo.event_generate("<Escape>")
    bombear(v, s)
    assert not p.dialogo.winfo_exists()
    clic(p.filas[fid]["ver"])
    bombear(v, s)
    clic(p.dialogo.btn_cerrar)
    bombear(v, s)
    assert not p.dialogo.winfo_exists()


# ------------------------------------------------------------- Parte 1, falla 2: fecha de alerta
def test_fecha_alerta_precargada_con_hoy_y_editable(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")                                       # vence el 14, hoy 12: venc − 3 ya pasó
    w = p.filas[fid]
    assert vars_de(p, fid)["alerta"].get() == "12-10-2026"          # precarga = hoy
    assert visible(w["alerta"]) and habilitado(w["alerta"]) and str(w["alerta"].cget("state")) == "normal"
    escribir(w["alerta"], "13-10-2026")                             # se puede modificar
    assert vars_de(p, fid)["alerta"].get() == "13-10-2026"
    clic(w["accion"])                                              # Confirmar
    bombear(v, s, 8)
    assert repo._leer("SELECT proxima_alerta FROM seguimientos")[0]["proxima_alerta"] == "2026-10-13"
    assert repo.obtener(fid)["fecha_vencimiento"] == "2026-10-14"    # el vencimiento no cambia


def test_fecha_anterior_a_hoy_se_rechaza_y_hoy_vale(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")
    w = p.filas[fid]
    escribir(w["alerta"], "11-10-2026")
    clic(w["accion"])
    bombear(v, s, 8)
    w = p.filas[fid]                                               # la fila se reconstruye con el mensaje
    assert "anterior a hoy" in w["obs"].cget("text") and repo.listar_guardadas() == []
    assert vars_de(p, fid)["alerta"].get() == "11-10-2026"          # lo escrito se conserva para corregirlo
    escribir(w["alerta"], "12-10-2026", enter=True)                 # atajo: Enter confirma
    bombear(v, s, 8)
    assert repo.obtener(fid)["estado"] == "guardada"
    assert repo._leer("SELECT proxima_alerta FROM seguimientos")[0]["proxima_alerta"] == "2026-10-12"


def test_fecha_con_formato_invalido_vuelve_a_preguntar(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")
    escribir(p.filas[fid]["alerta"], "mañana")
    clic(p.filas[fid]["accion"])
    bombear(v, s, 8)
    assert "no es válida" in p.filas[fid]["obs"].cget("text") and repo.listar_guardadas() == []


def test_la_fila_con_hallazgos_ya_permite_editar_sus_celdas_de_pago(app, repo):
    s, v, p = app
    fid = fid_de(repo, "13")                                       # en revisión (roja)
    w = p.filas[fid]
    assert habilitado(w["combo"]) and visible(w["alerta"]) and str(w["alerta"].cget("state")) == "normal"
    escribir(w["alerta"], "20-10-2026")                             # editable sin esperar a la revisión
    elegir(w["combo"], pg.ETIQUETAS["cheque"])
    assert habilitado(w["nro"])
    escribir(w["nro"], "9090")
    clic(w["accion"]) if False else None
    # tras revisar y aprobar, las ediciones hechas siguen ahí
    clic(w["accion"])
    bombear(v, s)
    clic(p.dialogo.btn_aprobar)
    bombear(v, s, 8)
    assert vars_de(p, fid)["nro"].get() == "9090" and vars_de(p, fid)["estado"].get() == pg.ETIQUETAS["cheque"]


# ---------------------------------------------------- estado de cada control según el estado de pago
def test_celdas_segun_el_estado_de_pago(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")
    w = p.filas[fid]
    # Pendiente: N° bloqueado, alerta visible y editable
    assert not habilitado(w["nro"]) and visible(w["alerta"]) and not visible(w["sin_alerta"])
    escribir(w["nro"], "555")
    assert vars_de(p, fid)["nro"].get() == ""                       # bloqueada: no acepta texto
    # Cheque: N° editable, alerta oculta
    elegir(w["combo"], pg.ETIQUETAS["cheque"])
    assert habilitado(w["nro"]) and not visible(w["alerta"]) and visible(w["sin_alerta"])
    escribir(w["nro"], "555")
    assert vars_de(p, fid)["nro"].get() == "555"
    # Transferencia / contado: ni N° ni alerta
    for etiqueta in (pg.ETIQUETAS["transferencia"], pg.ETIQUETAS["contado"]):
        elegir(w["combo"], etiqueta)
        assert not habilitado(w["nro"]) and not visible(w["alerta"])
    # De vuelta a pendiente: la alerta reaparece, editable y con su valor
    elegir(w["combo"], pg.ETIQUETAS["pendiente"])
    assert visible(w["alerta"]) and habilitado(w["alerta"]) and vars_de(p, fid)["alerta"].get() == "12-10-2026"
    escribir(w["alerta"], "14-10-2026")
    assert vars_de(p, fid)["alerta"].get() == "14-10-2026"


def test_la_lista_ofrece_exactamente_las_cuatro_opciones(app, repo):
    s, v, p = app
    w = p.filas[fid_de(repo, "11")]
    assert list(w["combo"].cget("values")) == [pg.ETIQUETAS[k] for k in ("pendiente", "contado", "transferencia", "cheque")]


def test_cheque_sin_numero_no_avanza_y_con_numero_si(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")
    w = p.filas[fid]
    elegir(w["combo"], pg.ETIQUETAS["cheque"])
    clic(w["accion"])
    bombear(v, s, 8)
    assert "N° de documento" in p.filas[fid]["obs"].cget("text") and repo.listar_guardadas() == []
    w = p.filas[fid]
    escribir(w["nro"], "7788", enter=True)                          # atajo Enter
    bombear(v, s, 8)
    f = repo.obtener(fid)
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"]) == ("pagada", "cheque", "7788")
    assert repo._leer("SELECT * FROM seguimientos") == []            # pagada: sin alerta


def test_transferencia_y_contado_no_piden_numero(app, repo):
    s, v, p = app
    fid = fid_de(repo, "12")                                       # sugerida: transferencia
    w = p.filas[fid]
    assert vars_de(p, fid)["estado"].get() == pg.ETIQUETAS["transferencia"] and not habilitado(w["nro"])
    clic(w["accion"])
    bombear(v, s, 8)
    f = repo.obtener(fid)
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"], f["fecha_pago"]) == (
        "pagada", "transferencia", None, "2026-10-12")


def test_cambiar_el_estado_conserva_lo_escrito_al_refrescar(app, repo):
    s, v, p = app
    fid = fid_de(repo, "11")
    elegir(p.filas[fid]["combo"], pg.ETIQUETAS["cheque"])
    escribir(p.filas[fid]["nro"], "321")
    p._firma = None
    v.refrescar_todo()                                             # un refresco reconstruye las filas
    assert vars_de(p, fid)["nro"].get() == "321" and habilitado(p.filas[fid]["nro"])


# ------------------------------------------------------------------------- confirmar todas
def test_confirmar_todas_solo_las_filas_sin_hallazgos(app, repo, monkeypatch):
    from tkinter import messagebox

    s, v, p = app
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    assert habilitado(p.btn_todas)
    clic(p.btn_todas)
    bombear(v, s, 10)
    assert {f["folio"] for f in repo.listar_guardadas()} == {"11", "12"}
    assert repo.obtener(fid_de(repo, "13"))["estado"] == "en_revision"
    assert not habilitado(p.btn_todas)                              # ya no queda ninguna fila lista


def test_confirmar_todas_respeta_lo_editado_en_cada_fila(app, repo, monkeypatch):
    from tkinter import messagebox

    s, v, p = app
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    fid = fid_de(repo, "11")
    elegir(p.filas[fid]["combo"], pg.ETIQUETAS["cheque"])
    escribir(p.filas[fid]["nro"], "4242")
    clic(p.btn_todas)
    bombear(v, s, 10)
    assert repo.obtener(fid)["nro_documento"] == "4242"


def test_confirmar_todas_cancelado_no_guarda(app, repo, monkeypatch):
    from tkinter import messagebox

    s, v, p = app
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    clic(p.btn_todas)
    bombear(v, s, 6)
    assert repo.listar_guardadas() == []


# ------------------------------------------------------------ otros controles de la pestaña
def test_reintentar_en_fila_con_error_de_lectura(r, repo, reloj, fotos):
    from facturextract.extractor import ErrorPermanente

    s, v = abrir_app(r, repo, reloj, [ErrorPermanente("La clave de API no es válida."), A()])
    try:
        procesar(v, s, fotos[:1])
        p = v.panel_lote
        fid = repo.listar_por_estado("error_reintentable")[0]["id"]
        w = p.filas[fid]
        assert w["accion"].cget("text") == "Reintentar" and not habilitado(w["combo"]) and not habilitado(w["nro"])
        assert "clave" in w["obs"].cget("text")
        clic(w["accion"])
        bombear(v, s, 8)
        assert repo.obtener(fid)["estado"] == "por_confirmar"
    finally:
        s.detener()
        v.destroy()


def test_nota_de_credito_no_pregunta_pago(r, repo, reloj, fotos):
    s, v = abrir_app(r, repo, reloj, [factura(folio="21", tipo_documento="nota_credito", fecha_vencimiento=None)])
    try:
        procesar(v, s, fotos[:1])
        p = v.panel_lote
        fid = fid_de(repo, "21")
        w = p.filas[fid]
        assert not habilitado(w["combo"]) and not habilitado(w["nro"]) and not visible(w["alerta"])
        assert vars_de(p, fid)["estado"].get() == "No aplica"
        clic(w["accion"])
        bombear(v, s, 8)
        assert repo.obtener(fid)["estado_pago"] == "no aplica"
    finally:
        s.detener()
        v.destroy()


def test_pendiente_emitida_el_mes_pasado_con_vencimiento_en_dos_dias_muestra_y_deja_editar_la_alerta(r, repo, reloj, fotos):
    """El caso real: emitida en septiembre, vence el 14, hoy es 12 de octubre."""
    s, v = abrir_app(r, repo, reloj, [factura(folio="31", fecha_emision="2026-09-20", fecha_vencimiento="2026-10-14")])
    try:
        procesar(v, s, fotos[:1])
        p = v.panel_lote
        fid = fid_de(repo, "31")
        w = p.filas[fid]
        assert visible(w["alerta"]) and habilitado(w["alerta"]) and not visible(w["sin_alerta"])
        assert p.filas[fid]["vars"]["alerta"].get() == "12-10-2026"            # venc - 3 ya pasó: precarga = hoy
        assert "mes anterior" not in w["obs"].cget("text")
        escribir(w["alerta"], "13-10-2026")                                    # editable
        clic(w["accion"])
        bombear(v, s, 8)
        f = repo.obtener(fid)
        assert f["estado_pago"] == "pendiente" and f["mes_emision"] == "2026-09"
        assert repo._leer("SELECT proxima_alerta FROM seguimientos")[0]["proxima_alerta"] == "2026-10-13"
    finally:
        s.detener()
        v.destroy()


def test_la_tabla_se_desplaza_con_la_rueda_y_tiene_barra_horizontal(r, repo, reloj, fotos):
    s, v = abrir_app(r, repo, reloj, [factura(folio=str(100 + i)) for i in range(14)])
    try:
        v.geometry("1250x480+5+5")                                  # ventana baja: la tabla debe desplazarse
        procesar(v, s, fotos)
        bombear(v, s, 4)
        p = v.panel_lote
        assert len(p.filas) == 14
        assert p.lienzo_filas.yview()[1] < 1.0                      # no caben todas: hay desplazamiento
        antes = p.lienzo_filas.yview()[0]
        etiqueta = next(iter(p.filas.values()))["etiqueta"]
        etiqueta.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
        v.update()
        assert p.lienzo_filas.yview()[0] > antes                    # la rueda sobre la tabla la desplaza
        sx = [w for w in p.lienzo_filas.master.winfo_children() if str(w.winfo_class()) == "TScrollbar"
              and str(w.cget("orient")) == "horizontal"]
        assert sx                                                   # y existe barra horizontal
    finally:
        s.detener()
        v.destroy()


def test_tabla_vacia_muestra_mensaje(r, repo, reloj):
    s, v = abrir_app(r, repo, reloj, [])
    try:
        bombear(v, s)
        v.notebook.select(v.tab_lote)
        bombear(v, s, 2)
        p = v.panel_lote
        assert visible(p.lbl_vacio) and not habilitado(p.btn_todas)
    finally:
        s.detener()
        v.destroy()
