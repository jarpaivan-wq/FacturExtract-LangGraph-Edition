import pytest
from PIL import Image

from facturextract import formulario as fm
from facturextract.extractor import ExtractorFalso
from facturextract.modelo import DatosInvalidos, normalizar_campos
from facturextract.servicio import Servicio
from tests.factura_ejemplo import factura


def test_formulario_ida_y_vuelta():
    c = factura()
    form = fm.campos_a_formulario(c)
    assert form["fecha_emision"] == "05-10-2026"
    campos = fm.formulario_a_campos(form, fm.lineas_a_texto(c["lineas"]), c)   # la sugerencia de pago se conserva
    assert normalizar_campos(campos) == c


def test_lineas_mal_formadas_dan_mensaje_claro():
    with pytest.raises(DatosInvalidos, match="línea 2"):
        fm.texto_a_lineas("1 | a | 2 | 0 | 2\nsolo dos | datos")


def test_pago_texto():
    assert fm.pago_texto({"estado_pago": "pendiente"}) == "Pendiente"
    assert fm.pago_texto({"estado_pago": "pagada", "metodo_pago": "cheque", "nro_documento": "55"}) == "Pagada · Cheque N° 55"
    assert fm.pago_texto({"estado_pago": "pagada", "metodo_pago": "contado", "nro_documento": None}) == "Pagada · Contado"


@pytest.fixture
def fotos(tmp_path):
    out = []
    for i in range(3):
        p = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (30, 30), "white").save(p)
        out.append(str(p))
    return out


def drenar(s):
    s.esperar_inactivo()
    out = []
    while not s.eventos.empty():
        out.append(s.eventos.get())
    return out


def resultados(evs):
    return [e.datos["resultado"] for e in evs if e.tipo == "factura"]


def pago_ok(fid, estado="pendiente", nro=None):
    return {"id": fid, "pago": {"estado": estado, "nro_documento": nro, "fecha_alerta": None}}


def test_lote_secuencial_una_falla_no_detiene_a_las_demas(r, repo, reloj, fotos):
    ext = ExtractorFalso([factura(folio="1"), RuntimeError("falla rara"), factura(folio="3")])
    s = Servicio(r, repo, ext, reloj)
    s.iniciar()
    s.procesar_lote([[fotos[0]], [fotos[1]], [fotos[2]], ["no_existe.jpg"]])
    evs = drenar(s)
    s.detener()
    assert resultados(evs) == ["lista", "error", "lista", "error"]
    fin = [e for e in evs if e.tipo == "lote_fin"][0].datos
    assert fin["listas"] == 2 and fin["errores"] == 2 and fin["guardadas"] == 0
    assert repo.listar_guardadas() == []                       # nada se guarda sin confirmar
    assert len(repo.listar_por_estado("error_reintentable")) == 1
    msgs = [e.datos["mensaje"] for e in evs if e.tipo == "factura" and e.datos["resultado"] == "error"]
    assert all("Traceback" not in m and "RuntimeError" not in m for m in msgs)   # lenguaje normal


def test_lote_confirmar_todas_y_fila_a_fila(r, repo, reloj, fotos):
    ext = ExtractorFalso([factura(folio="1"), factura(folio="2"), factura(folio="3")])
    s = Servicio(r, repo, ext, reloj)
    s.iniciar()
    s.procesar_lote([[fotos[0]], [fotos[1]], [fotos[2]]])
    drenar(s)
    ids = [f["id"] for f in repo.listar_por_estado("por_confirmar")]
    assert len(ids) == 3
    s.confirmar_lote([pago_ok(ids[0], "contado"), pago_ok(ids[1], "cheque", "")])   # la 2.ª falla: cheque sin N°
    evs = drenar(s)
    res = [e for e in evs if e.tipo == "lote_confirmado"][0].datos["resultados"]
    assert res[ids[0]]["ok"] and not res[ids[1]]["ok"] and "N° de documento" in res[ids[1]]["mensaje"]
    assert not any(e.tipo in ("aviso", "error") for e in evs)                # un solo resumen, sin popups por fila
    assert [f["id"] for f in repo.listar_guardadas()] == [ids[0]]
    s.confirmar_lote([pago_ok(ids[1], "cheque", "991"), pago_ok(ids[2], "transferencia")])
    evs = drenar(s)
    s.detener()
    assert all(v["ok"] for e in evs if e.tipo == "lote_confirmado" for v in e.datos["resultados"].values())
    assert len(repo.listar_guardadas()) == 3


def test_confirmar_fila_sin_pausa_no_hace_nada(r, repo, reloj):
    s = Servicio(r, repo, ExtractorFalso([]), reloj)
    s.iniciar()
    s.confirmar_lote([pago_ok("no-existe")])
    res = [e for e in drenar(s) if e.tipo == "lote_confirmado"][0].datos["resultados"]
    s.detener()
    assert not res["no-existe"]["ok"]


def test_dudosa_va_a_revision_y_despues_a_confirmar(r, repo, reloj, fotos):
    mala = factura(folio="7", iva=1, total=1000001)
    ext = ExtractorFalso([mala, mala, mala, factura(folio="8")])
    s = Servicio(r, repo, ext, reloj)
    s.iniciar()
    s.procesar_lote([[fotos[0]], [fotos[1]]])
    evs = drenar(s)
    assert resultados(evs) == ["revision", "lista"]
    fid = repo.listar_por_estado("en_revision")[0]["id"]
    s.confirmar_lote([pago_ok(fid)])                                # aún no se puede confirmar: falta la revisión
    res = [e for e in drenar(s) if e.tipo == "lote_confirmado"][0].datos["resultados"]
    assert not res[fid]["ok"]
    s.decidir_revision(fid, {"accion": "aprobar"})
    drenar(s)
    assert repo.obtener(fid)["estado"] == "por_confirmar"
    s.confirmar_lote([pago_ok(fid)])
    drenar(s)
    s.detener()
    assert len(repo.listar_guardadas()) == 1 and repo.listar_guardadas()[0]["aprobada_manual"] == 1


def test_repregunta_de_revision_llega_como_aviso(r, repo, reloj, fotos):
    mala = factura(iva=1, total=1000001)
    s = Servicio(r, repo, ExtractorFalso([mala, mala, mala]), reloj)
    s.iniciar()
    s.procesar_lote([[fotos[0]]])
    drenar(s)
    fid = repo.listar_por_estado("en_revision")[0]["id"]
    s.decidir_revision(fid, {"accion": "corregir", "campos": {"total": "xx"}})
    evs = drenar(s)
    s.detener()
    assert any(e.tipo == "aviso" and e.datos.get("repregunta") for e in evs)


def test_pausas_pendientes_se_retoman_al_reabrir_la_aplicacion(r, repo, reloj, fotos):
    s1 = Servicio(r, repo, ExtractorFalso([factura(folio="1"), factura(folio="2")]), reloj)
    s1.iniciar()
    s1.procesar_lote([[fotos[0]], [fotos[1]]])
    drenar(s1)
    s1.detener()                                                    # «se cierra la aplicación»
    s2 = Servicio(r, repo, ExtractorFalso([]), reloj)               # reabre: no se vuelve a llamar al modelo
    s2.iniciar()
    drenar(s2)
    assert len(repo.listar_por_estado("por_confirmar")) == 2 and repo.listar_guardadas() == []
    ids = [f["id"] for f in repo.listar_por_estado("por_confirmar")]
    s2.confirmar_lote([pago_ok(i, "contado") for i in ids])
    drenar(s2)
    s2.detener()
    assert len(repo.listar_guardadas()) == 2 and s2.extractor.llamadas == []


def test_pagar_desde_la_vista_con_metodo(r, repo, reloj, fotos):
    s = Servicio(r, repo, ExtractorFalso([factura()]), reloj)
    s.iniciar()
    s.procesar_lote([[fotos[0]]])
    drenar(s)
    fid = repo.listar_por_estado("por_confirmar")[0]["id"]
    s.confirmar_lote([pago_ok(fid, "pendiente")])
    drenar(s)
    s.pagar(fid, "cheque", "")            # cheque sin número: se rechaza con mensaje
    assert any(e.tipo == "aviso" for e in drenar(s))
    s.pagar(fid, "cheque", "123")
    evs = drenar(s)
    s.detener()
    assert any(e.tipo == "pago" and e.datos["excel"] == "ok" for e in evs)
    f = repo.obtener(fid)
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"]) == ("pagada", "cheque", "123")


def test_esquema_de_extraccion_respeta_limites_de_la_api():
    """La API rechazó antes (a) más de 16 campos con unión y (b) un enum que incluía null en un tipo [string, null]."""
    from facturextract.extractor import ESQUEMA_EXTRACCION

    uniones, enums_incoherentes = 0, []

    def recorrer(o):
        nonlocal uniones
        if isinstance(o, dict):
            if isinstance(o.get("type"), list) or "anyOf" in o:
                uniones += 1
            if "enum" in o and isinstance(o.get("type"), list):
                enums_incoherentes.append(o)
            for v in o.values():
                recorrer(v)
        elif isinstance(o, list):
            for v in o:
                recorrer(v)
    recorrer(ESQUEMA_EXTRACCION)
    assert uniones <= 16 and enums_incoherentes == []
    assert ESQUEMA_EXTRACCION["properties"]["pago_sugerido"]["enum"] == [
        "pendiente", "contado", "transferencia", "cheque", "no_claro"]
