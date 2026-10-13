"""Cambio 2: ventanas emergentes bien dimensionadas y posicionadas dentro del ÁREA ÚTIL de la pantalla.

Se prueba con ventanas REALES de Tkinter. La posición se mide con `GetWindowRect` de Win32 (independiente del código
bajo prueba). Las pantallas simuladas se inyectan con `ventanas.proveedor_area`: 1366x768 al 100 %, 125 % y 150 %
(coordenadas lógicas, como las ve un proceso sin soporte de DPI), 125 % «con soporte de DPI», segundo monitor a la
izquierda y arriba (coordenadas negativas) y la ventana principal pegada a las esquinas. Además se prueba contra la
pantalla real (y contra un segundo monitor real si lo hubiera).
"""
import ctypes
from ctypes import wintypes
from datetime import date, datetime
from tkinter import messagebox

import pytest

from facturextract import ventanas
from facturextract.ui import DialogoAvisos, DialogoEditarPago, DialogoPago
from facturextract.ui_vencimientos import DialogoFecha
from tests.factura_ejemplo import factura
from tests.ui_utils import abrir_app, bombear, clic, con_dialogo, ir_a_pestana, procesar, visible

HOY = date(2026, 10, 12)
AHORA = datetime(2026, 10, 12, 9, 30)
TOL = 1

# (nombre, área útil (izq, arriba, der, abajo), escala simulada de Tk)
PANTALLAS = {
    "1366x768-100%": ((0, 0, 1366, 728), 1.0),
    "1366x768-125%": ((0, 0, 1093, 582), 1.0),        # lógico: 1366/1.25 x 768/1.25 menos la barra de tareas
    "1366x768-150%": ((0, 0, 911, 480), 1.0),
    "1366x768-125%-con-DPI": ((0, 0, 1366, 728), 1.25),  # el proceso ve píxeles físicos y Tk escala 1,25
    "1920x1080-100%": ((0, 0, 1920, 1040), 1.0),
}


def rect_win32(w):
    """Rectángulo externo REAL de la ventana (con barra de título y bordes), por Win32."""
    r = wintypes.RECT()
    assert ctypes.windll.user32.GetWindowRect(int(w.wm_frame(), 16), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def dentro(rect, area, tol=TOL):
    return (rect[0] >= area[0] - tol and rect[1] >= area[1] - tol and rect[2] <= area[2] + tol
            and rect[3] <= area[3] + tol)


def rect_widget(w):
    w.update_idletasks()
    return w.winfo_rootx(), w.winfo_rooty(), w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height()


def cliente(ventana):
    return rect_widget(ventana)


def comprobar(ventana, area, botones, centrada_en=None, etiqueta=""):
    """La ventana completa dentro del área útil; todos los botones visibles, dentro de la ventana y de la pantalla."""
    ventana.update()
    ext = rect_win32(ventana)
    assert dentro(ext, area), f"{etiqueta}: ventana {ext} fuera del área útil {area}"
    cli = cliente(ventana)
    for nombre, b in botones.items():
        assert visible(b), f"{etiqueta}: el botón «{nombre}» no está visible"
        r = rect_widget(b)
        assert r[0] >= cli[0] - TOL and r[1] >= cli[1] - TOL and r[2] <= cli[2] + TOL and r[3] <= cli[3] + TOL, \
            f"{etiqueta}: el botón «{nombre}» {r} queda fuera de la ventana {cli}"
        assert dentro(r, area), f"{etiqueta}: el botón «{nombre}» {r} queda fuera de la pantalla {area}"
    if centrada_en is not None:
        cx, cy = (ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2
        px_, py_ = (centrada_en[0] + centrada_en[2]) / 2, (centrada_en[1] + centrada_en[3]) / 2
        assert abs(cx - px_) <= 4 and abs(cy - py_) <= 4, f"{etiqueta}: no está centrada ({cx},{cy}) vs ({px_},{py_})"
    return ext


@pytest.fixture
def avisos(monkeypatch):
    vistos = []
    for n in ("showwarning", "showinfo", "showerror"):
        monkeypatch.setattr(messagebox, n, lambda t, m, **k: vistos.append(m))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    return vistos


@pytest.fixture(autouse=True)
def _restaurar_area():
    yield
    ventanas.proveedor_area = None


def sembrar(repo):
    """Una factura pendiente con alerta para hoy (aparecerá en «Vencimientos del día») y una pagada."""
    for fid, pago, folio in (("f1", None, "1"), ("f2", {"estado_pago": "pagada", "metodo_pago": "cheque",
                                                           "nro_documento": "55", "fecha_alerta": None}, "2")):
        repo.crear_factura(fid, [], AHORA)
        repo.guardar_factura(fid, factura(folio=folio, fecha_vencimiento="2026-10-14"), [], HOY, False, pago)


@pytest.fixture
def pantalla(r, repo, reloj, avisos, tmp_path):
    """Abre la aplicación sobre una pantalla simulada: pantalla(nombre) -> (servicio, ventana principal, area)."""
    creados = []
    from PIL import Image
    foto = tmp_path / "f.jpg"
    Image.new("RGB", (300, 420), "white").save(foto)
    mala = factura(folio="9", iva=1, total=1000001)

    def abrir(nombre=None, area=None, escala=1.0, tk_scaling=True):
        area, escala = (PANTALLAS[nombre] if nombre else (area, escala))
        ventanas.proveedor_area = lambda x, y: area
        sembrar(repo)
        s, v = abrir_app(r, repo, reloj, [mala, mala, mala], geometria=None)
        if escala != 1.0 and tk_scaling:
            v.tk.call("tk", "scaling", escala * 96 / 72)
        creados.append((s, v))
        procesar(v, s, [str(foto)])
        return s, v, area
    yield abrir
    for s, v in creados:
        try:
            s.detener()
            v.destroy()
        except Exception:
            pass


def botones_revision(d):
    return {"aprobar": d.btn_aprobar, "corregir": d.btn_corregir, "rechazar": d.btn_rechazar, "cerrar": d.btn_cerrar}


def abrir_revision(v, s):
    p = v.panel_lote
    fid = [f["id"] for f in p.repo.listar_por_estado("en_revision")][0]
    clic(p.filas[fid]["accion"])                      # el botón «Revisar…» real
    bombear(v, s, 3)
    assert p.dialogo is not None and p.dialogo.winfo_exists()
    return p.dialogo


# ============================================================================ ventana «Revisar»
@pytest.mark.parametrize("nombre", list(PANTALLAS))
def test_revisar_cabe_en_la_pantalla_con_los_botones_visibles(pantalla, nombre):
    s, v, area = pantalla(nombre)
    d = abrir_revision(v, s)
    comprobar(d, area, botones_revision(d), etiqueta=nombre)
    ext_p = rect_win32(v)
    assert dentro(ext_p, area), f"{nombre}: la ventana principal {ext_p} no cabe en {area}"


@pytest.mark.parametrize("nombre", ["1366x768-100%", "1366x768-125%", "1366x768-150%"])
def test_revisar_centrada_sobre_la_ventana_principal(pantalla, nombre):
    s, v, area = pantalla(nombre)
    d = abrir_revision(v, s)
    comprobar(d, area, botones_revision(d), centrada_en=rect_win32(v), etiqueta=nombre)


def test_si_el_contenido_no_cabe_se_desplaza_el_contenido_no_los_botones(pantalla):
    s, v, area = pantalla("1366x768-150%")             # 911x480: la columna de datos es más alta que la ventana
    d = abrir_revision(v, s)
    comprobar(d, area, botones_revision(d), etiqueta="150%")
    d.update()
    assert d.derecha.alto_pedido() > d.derecha.lienzo.winfo_height()          # sí hay contenido de sobra
    assert d.derecha.lienzo.yview() != (0.0, 1.0)                              # y hay desplazamiento
    antes = d.derecha.lienzo.yview()[0]
    d.derecha.lienzo.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    d.derecha.lienzo.yview_scroll(3, "units")
    d.update()
    assert d.derecha.lienzo.yview()[0] > antes                                 # el contenido se desplaza...
    comprobar(d, area, botones_revision(d), etiqueta="150% tras desplazar")    # ...y los botones siguen donde estaban
    # el último campo (líneas) es alcanzable desplazando hasta el final
    d.derecha.lienzo.yview_moveto(1.0)
    d.update()
    r = rect_widget(d.txt_lineas)
    assert r[3] <= cliente(d)[3] + TOL and visible(d.txt_lineas)


@pytest.mark.parametrize("pos", ["esquina-superior-izquierda", "esquina-inferior-derecha", "mitad-derecha",
                                 "parcialmente-fuera"])
def test_revisar_con_la_principal_en_distintos_lugares(pantalla, pos):
    s, v, area = pantalla("1366x768-125%")
    ancho, alto = 900, 520
    x, y = {"esquina-superior-izquierda": (0, 0), "esquina-inferior-derecha": (area[2] - ancho, area[3] - alto),
            "mitad-derecha": (area[2] - ancho, 100), "parcialmente-fuera": (area[2] - 300, area[3] - 200)}[pos]
    v.geometry(f"{ancho}x{alto}+{x}+{y}")
    v.update()
    bombear(v, s, 2)
    d = abrir_revision(v, s)
    comprobar(d, area, botones_revision(d), etiqueta=pos)


# ======================================================================= la principal también cabe
@pytest.mark.parametrize("nombre", ["1366x768-125%", "1366x768-150%"])
def test_la_ventana_principal_se_ajusta_a_pantallas_chicas(pantalla, nombre):
    s, v, area = pantalla(nombre)
    v.update()
    ext = rect_win32(v)
    assert dentro(ext, area), f"{nombre}: principal {ext} fuera de {area}"
    assert visible(v.notebook) and visible(v.btn_venc)
    r = rect_widget(v.btn_venc)
    assert dentro(r, area)


# ============================================================================== otros diálogos
def inspeccionar_dialogo(ventana, botones, area, etiqueta, resultado):
    try:
        comprobar(ventana, area, botones, etiqueta=etiqueta)
        resultado[etiqueta] = "ok"
    except AssertionError as e:        # un fallo dentro de un temporizador de Tk no se vería: se guarda y se re-lanza
        resultado[etiqueta] = str(e)


@pytest.mark.parametrize("nombre", list(PANTALLAS))
def test_editar_pago_cabe_y_sus_botones_se_ven(pantalla, nombre):
    s, v, area = pantalla(nombre)
    ir_a_pestana(v, s, "Facturas")
    v.arbol_fac.selection_set("f1")
    v.arbol_fac.update()
    res = {}

    def hacer(d):
        inspeccionar_dialogo(d, {"guardar": d.btn_guardar, "cancelar": d.btn_cancelar}, area, nombre, res)
        clic(d.btn_cancelar)
    con_dialogo(v, s, DialogoEditarPago, hacer, v.btn_editar_pago)
    assert res[nombre] == "ok", res


@pytest.mark.parametrize("nombre", list(PANTALLAS))
def test_avisos_cabe_y_sus_botones_se_ven(pantalla, nombre):
    s, v, area = pantalla(nombre)
    v._ajustes_avisos()
    d = [w for w in ventanas_hijas(v) if isinstance(w, DialogoAvisos)][0]
    comprobar(d, area, {"guardar": d.btn_guardar}, etiqueta=nombre)
    d.destroy()


def ventanas_hijas(v):
    from tests.ui_utils import descendientes
    return list(descendientes(v))


@pytest.mark.parametrize("nombre", list(PANTALLAS))
def test_vencimientos_del_dia_y_sus_dialogos_caben(pantalla, nombre):
    s, v, area = pantalla(nombre)
    s.revisar_vencimientos()
    bombear(v, s, 3)
    w = v._ventana_venc
    assert w is not None and w.winfo_exists() and len(w.filas) == 1
    fila = next(iter(w.filas.values()))
    comprobar(w, area, {"posponer": fila["posponer"], "pagada": fila["pagada"], "cerrar": fila["cerrar"],
                        "cerrar ventana": w.btn_cerrar}, etiqueta=nombre)
    res = {}

    def hacer_pago(d):
        inspeccionar_dialogo(d, {"aceptar": d.btn_ok}, area, nombre + "-pago", res)
        d.destroy()
    con_dialogo(v, s, DialogoPago, hacer_pago, fila["pagada"])
    assert res[nombre + "-pago"] == "ok", res

    def hacer_fecha(d):
        inspeccionar_dialogo(d, {"aceptar": d.btn_ok}, area, nombre + "-fecha", res)
        d.destroy()
    con_dialogo(v, s, DialogoFecha, hacer_fecha, fila["posponer"])
    assert res[nombre + "-fecha"] == "ok", res


def test_vencimientos_con_muchas_alertas_se_desplaza_y_el_boton_de_cierre_sigue_visible(pantalla, repo):
    s, v, area = pantalla("1366x768-150%")
    for i in range(12):
        repo.crear_factura(f"m{i}", [], AHORA)
        repo.guardar_factura(f"m{i}", factura(folio=str(100 + i), fecha_vencimiento="2026-10-14"), [], HOY)
    s.revisar_vencimientos()
    bombear(v, s, 3)
    w = v._ventana_venc
    assert len(w.filas) == 13
    comprobar(w, area, {"cerrar ventana": w.btn_cerrar}, etiqueta="muchas")
    w.update()
    assert w.lienzo.yview()[1] < 1.0                                      # no caben todas: se desplaza el contenido
    w.lienzo.yview_moveto(1.0)
    w.update()
    comprobar(w, area, {"cerrar ventana": w.btn_cerrar}, etiqueta="muchas tras desplazar")


# ================================================================================= segundo monitor
def test_segundo_monitor_a_la_izquierda_con_coordenadas_negativas(pantalla):
    izquierda, principal = (-1920, 0, 0, 1040), (0, 0, 1536, 824)
    area_de = lambda x, y: izquierda if x < 0 else principal
    s, v, _ = pantalla(area=principal)
    ventanas.proveedor_area = area_de
    v.geometry("900x520-1700+150")                                   # la principal en el monitor izquierdo
    v.update()
    bombear(v, s, 2)
    assert v.winfo_rootx() < 0
    d = abrir_revision(v, s)
    comprobar(d, izquierda, botones_revision(d), etiqueta="monitor izquierdo")
    assert rect_win32(d)[0] < 0                                       # aparece en SU monitor, no en el principal


def test_segundo_monitor_arriba_y_la_principal_en_el_borde(pantalla):
    arriba, principal = (0, -1080 + 40, 1920, 0), (0, 0, 1536, 824)
    area_de = lambda x, y: arriba if y < 0 else principal
    s, v, _ = pantalla(area=principal)
    ventanas.proveedor_area = area_de
    v.geometry("900x520+500+-900")
    v.update()
    bombear(v, s, 2)
    d = abrir_revision(v, s)
    comprobar(d, arriba, botones_revision(d), etiqueta="monitor de arriba")


def test_con_la_principal_oculta_en_la_bandeja_el_dialogo_aparece_dentro_del_area(pantalla):
    s, v, area = pantalla("1366x768-125%")
    v.withdraw()
    v.update()
    v._ajustes_avisos()                                              # se abre sobre el monitor del puntero
    d = [w for w in ventanas_hijas(v) if isinstance(w, DialogoAvisos)][0]
    comprobar(d, area, {"guardar": d.btn_guardar}, etiqueta="principal oculta")
    d.destroy()


# =============================================================================== pantalla real
def area_real_primaria():
    """Área útil del monitor principal según Win32 (SPI_GETWORKAREA), independiente del código de la aplicación."""
    r = wintypes.RECT()
    assert ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def monitores_reales():
    lista = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def cb(hmon, hdc, rect, data):
        info = ventanas._MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        lista.append((info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom))
        return 1
    ctypes.windll.user32.EnumDisplayMonitors(None, None, cb, 0)
    return lista


@pytest.fixture
def real(r, repo, reloj, avisos, tmp_path):
    from PIL import Image
    foto = tmp_path / "f.jpg"
    Image.new("RGB", (300, 420), "white").save(foto)
    mala = factura(folio="9", iva=1, total=1000001)
    sembrar(repo)
    s, v = abrir_app(r, repo, reloj, [mala, mala, mala], geometria=None)
    procesar(v, s, [str(foto)])
    yield s, v
    s.detener()
    v.destroy()


def test_en_la_pantalla_real_la_principal_y_los_dialogos_caben_en_el_area_util(real):
    s, v = real
    area = area_real_primaria()
    assert ventanas.area_util(v, *[a + b for a, b in zip(area[:2], (50, 50))]) == area    # coincide con Win32
    for x, y in ((0, 0), (area[2] - 700, area[3] - 400), ((area[0] + area[2]) // 2 - 450, 30), (area[2] - 250, 200)):
        v.geometry(f"900x520+{x}+{y}")
        v.update()
        bombear(v, s, 2)
        d = abrir_revision(v, s)
        comprobar(d, area, botones_revision(d), etiqueta=f"real {x},{y}")
        d.destroy()
        bombear(v, s, 1)


def test_en_un_segundo_monitor_real_si_lo_hay(real):
    monitores = monitores_reales()
    if len(monitores) < 2:
        pytest.skip("este equipo tiene un solo monitor: ver la guía de prueba manual")
    s, v = real
    for area in monitores:
        v.geometry(f"900x520+{area[0] + 80}+{area[1] + 60}")
        v.update()
        bombear(v, s, 2)
        d = abrir_revision(v, s)
        comprobar(d, area, botones_revision(d), etiqueta=f"monitor {area}")
        d.destroy()
        bombear(v, s, 1)


def test_escala_de_pantalla_vista_por_tk_y_ayudas(real):
    s, v = real
    e = ventanas.escala(v)
    assert e >= 1.0
    assert ventanas.px(v, 100) == round(100 * e)
