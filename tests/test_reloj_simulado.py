from datetime import datetime, timedelta

import pytest

from facturextract import app, config
from facturextract.config import Reloj, RelojSimulado
from facturextract.extractor import ExtractorFalso
from tests.factura_ejemplo import factura
from tests.ui_utils import abrir_app, bombear, clic, visible


def test_parsear_formatos():
    ref = datetime(2026, 1, 1, 15, 45, 10)
    assert config.parsear_fecha_simulada("2026-10-14", ref) == datetime(2026, 10, 14, 15, 45, 10)   # hora real
    assert config.parsear_fecha_simulada("2026-10-14 09:00", ref) == datetime(2026, 10, 14, 9, 0)
    assert config.parsear_fecha_simulada("2026-10-14T09:30", ref) == datetime(2026, 10, 14, 9, 30)
    assert config.parsear_fecha_simulada("14-10-2026", ref).date().isoformat() == "2026-10-14"
    with pytest.raises(ValueError, match="Fecha simulada no válida"):
        config.parsear_fecha_simulada("pasado mañana")


def test_reloj_simulado_avanza_con_el_tiempo_real_y_se_puede_adelantar():
    real = [datetime(2026, 1, 1, 10, 0)]
    r = RelojSimulado(datetime(2026, 10, 14, 9, 0), real=lambda: real[0])
    assert r.ahora() == datetime(2026, 10, 14, 9, 0) and r.hoy().isoformat() == "2026-10-14"
    real[0] += timedelta(minutes=30)
    assert r.ahora() == datetime(2026, 10, 14, 9, 30)               # sigue corriendo
    r.avanzar(days=1)
    assert r.hoy().isoformat() == "2026-10-15" and r.ahora().hour == 9
    assert r.simulado is True and not hasattr(Reloj(), "simulado")


def test_en_desarrollo_la_variable_o_el_argumento_activan_la_fecha(monkeypatch):
    monkeypatch.setattr(config, "modo_desarrollo", lambda: True)
    r = config.reloj_para_ejecucion([], {config.VAR_FECHA_SIMULADA: "2026-10-14 09:00"})
    assert isinstance(r, RelojSimulado) and r.hoy().isoformat() == "2026-10-14"
    r = config.reloj_para_ejecucion(["--fecha-simulada=2026-11-01"], {config.VAR_FECHA_SIMULADA: "2026-10-14"})
    assert r.hoy().isoformat() == "2026-11-01"                      # el argumento manda
    r = config.reloj_para_ejecucion(["--fecha-simulada", "2026-12-24"], {})
    assert r.hoy().isoformat() == "2026-12-24"
    assert type(config.reloj_para_ejecucion([], {})) is Reloj        # sin pedirla: reloj real


def test_en_el_uso_normal_del_cliente_no_se_puede_activar(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "modo_desarrollo", lambda: False)    # cliente: sin carpeta .git ni FACTUREXTRACT_DEV
    assert type(config.reloj_para_ejecucion(["--fecha-simulada=2026-10-14"], {})) is Reloj
    assert type(config.reloj_para_ejecucion([], {config.VAR_FECHA_SIMULADA: "2026-10-14"})) is Reloj
    servicio, _, _ = app.construir_servicio(extractor=ExtractorFalso([]), argv=["--fecha-simulada=2026-10-14"])
    assert not getattr(servicio.reloj, "simulado", False)
    assert servicio.reloj.hoy() == datetime.now().date()


def test_modo_desarrollo_depende_de_git_o_de_la_variable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RAIZ", tmp_path)                    # carpeta sin .git, como en el cliente
    monkeypatch.delenv("FACTUREXTRACT_DEV", raising=False)
    assert config.modo_desarrollo() is False
    monkeypatch.setenv("FACTUREXTRACT_DEV", "1")
    assert config.modo_desarrollo() is True


def test_fecha_mal_escrita_en_desarrollo_da_error_claro(monkeypatch):
    monkeypatch.setattr(config, "modo_desarrollo", lambda: True)
    with pytest.raises(ValueError, match="AAAA-MM-DD"):
        config.reloj_para_ejecucion(["--fecha-simulada=ayer"], {})


# ------------------------------------------------------------------------------ interfaz real
def test_banner_permanente_cuando_la_fecha_es_simulada(r, repo):
    reloj = RelojSimulado(datetime(2026, 10, 14, 9, 0))
    s, v = abrir_app(r, repo, reloj, [])
    try:
        assert visible(v.banner) and "FECHA SIMULADA" in v.lbl_banner.cget("text")
        assert "14-10-2026" in v.lbl_banner.cget("text") and "FECHA SIMULADA" in v.title()
        for nombre in ("Procesar", "Lote", "Facturas"):              # sigue visible en cualquier pestaña
            v.notebook.select([i for i in v.notebook.tabs() if v.notebook.tab(i, "text").startswith(nombre)][0])
            v.update()
            assert visible(v.banner)
    finally:
        s.detener()
        v.destroy()


def test_sin_fecha_simulada_no_hay_banner(r, repo, reloj):
    from facturextract.config import Reloj
    s, v = abrir_app(r, repo, Reloj(), [])
    try:
        assert not hasattr(v, "banner") and "SIMULADA" not in v.title()
    finally:
        s.detener()
        v.destroy()


def test_botones_del_banner_adelantan_el_dia_y_disparan_la_alerta(r, repo):
    """+1 día mueve «hoy» y revisa los vencimientos: una alerta de 3 días antes salta sola."""
    reloj = RelojSimulado(datetime(2026, 10, 12, 9, 30))
    repo.crear_factura("a", [], reloj.ahora())
    repo.guardar_factura("a", factura(fecha_vencimiento="2026-10-17"), [], reloj.hoy())   # alerta el 14-oct
    s, v = abrir_app(r, repo, reloj, [])
    try:
        bombear(v, s)
        assert repo.seguimientos_esperando() == []                  # el 12 aún no toca
        botones = [b for b in v.banner.winfo_children() if b.winfo_class() == "Button"]
        mas_dia = [b for b in botones if b.cget("text") == "+1 día"][0]
        clic(mas_dia)
        bombear(v, s)
        assert repo.seguimientos_esperando() == []                  # 13-oct: todavía no
        assert "13-10-2026" in v.lbl_banner.cget("text")
        clic(mas_dia)
        bombear(v, s)
        assert "14-10-2026" in v.lbl_banner.cget("text")
        assert len(repo.seguimientos_esperando()) == 1              # 14-oct = 17 − 3: salta la alerta
        assert v._ventana_venc.winfo_exists()
    finally:
        s.detener()
        v.destroy()
