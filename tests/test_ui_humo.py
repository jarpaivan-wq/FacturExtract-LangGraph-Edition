"""Pruebas de humo de la interfaz (Tkinter real; se omiten si no hay pantalla)."""
import time
import tkinter
from datetime import datetime

import pytest
from PIL import Image

from facturextract import pago as pg
from facturextract.extractor import ExtractorFalso
from facturextract.servicio import Servicio
from tests.factura_ejemplo import factura


def bombear(v, s, veces=6):
    for _ in range(veces):
        v.update()
        s.esperar_inactivo()
        v.update()
        time.sleep(0.05)


@pytest.fixture
def fotos(tmp_path):
    out = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (200, 300), "white").save(p)
        out.append(str(p))
    return out


def abrir(r, repo, reloj, respuestas):
    from facturextract.ui import VentanaPrincipal

    s = Servicio(r, repo, ExtractorFalso(respuestas), reloj)
    s.iniciar()
    try:
        v = VentanaPrincipal(s, repo, r)
    except tkinter.TclError:
        s.detener()
        pytest.skip("sin pantalla disponible")
    return s, v


def procesar(v, s, fotos):
    v.grupos = [[f] for f in fotos]
    v._pintar_grupos()
    v._procesar()
    for _ in range(200):
        bombear(v, s, 1)
        if not v._procesando:
            break
    assert not v._procesando
    bombear(v, s)


def test_humo_ventana_vencimientos(r, repo, reloj):
    from facturextract.ui import VentanaPrincipal

    repo.crear_factura("a", [], datetime(2026, 10, 12))
    repo.guardar_factura("a", factura(fecha_vencimiento="2026-10-01"), [], reloj.hoy())
    s = Servicio(r, repo, ExtractorFalso([]), reloj)
    s.iniciar()
    try:
        v = VentanaPrincipal(s, repo, r)
    except tkinter.TclError:
        s.detener()
        pytest.skip("sin pantalla disponible")
    try:
        bombear(v, s)
        assert v._ventana_venc.winfo_exists()                       # se abrió sola la primera vez del día
        assert "(1)" in v.btn_venc.cget("text")
        sid = repo.seguimientos_esperando()[0]["id"]
        v._ventana_venc.decidir(sid, {"accion": "cerrar"})
        bombear(v, s)
        assert repo.seguimientos_esperando() == []
        assert "(" not in v.btn_venc.cget("text")
    finally:
        s.detener()
        v.destroy()
