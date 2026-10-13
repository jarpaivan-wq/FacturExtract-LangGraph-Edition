from datetime import datetime

import pytest

from facturextract.config import RelojFijo, rutas
from facturextract.db import Repo


@pytest.fixture(autouse=True)
def _aislar_datos(tmp_path, monkeypatch):
    """Ningún test toca la carpeta de datos real."""
    monkeypatch.setenv("FACTUREXTRACT_DATOS", str(tmp_path / "datos"))


@pytest.fixture
def r(tmp_path):
    return rutas(tmp_path / "datos").crear()


@pytest.fixture
def repo(r):
    return Repo(r.facturas_db)


@pytest.fixture
def reloj():
    return RelojFijo(datetime(2026, 10, 12, 9, 30))


@pytest.fixture(autouse=True)
def _gc_en_el_hilo_principal():
    """Tkinter: si el recolector libera variables de Tk desde el hilo trabajador mientras el hilo principal no está
    en mainloop, la llamada espera varios segundos. En las pruebas la recolección se hace aquí, en el hilo principal."""
    import gc

    gc.collect()
    gc.disable()
    yield
    gc.collect()
    gc.enable()


def _tk_con_reintentos():
    """La instalación de Tcl/Tk del Python de la Tienda de Windows falla de forma intermitente al leer sus propios
    archivos (init.tcl / tk.tcl) cuando se crean muchas ventanas seguidas. Solo en pruebas: se reintenta."""
    import time
    import tkinter

    original = tkinter.Tk.__init__

    def init(self, *a, **k):
        for intento in range(6):
            try:
                return original(self, *a, **k)
            except tkinter.TclError as e:
                if ".tcl" not in str(e).lower() or intento == 5:
                    raise
                time.sleep(0.4)
    tkinter.Tk.__init__ = init


_tk_con_reintentos()


@pytest.fixture(autouse=True)
def _tope_de_tiempo_por_prueba():
    """Si una prueba se cuelga (por ejemplo, un proceso o una ventana reales que no responden), a los 300 s se vuelca
    la traza de todos los hilos y se aborta, en vez de quedar esperando para siempre."""
    import faulthandler

    faulthandler.dump_traceback_later(300, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()
