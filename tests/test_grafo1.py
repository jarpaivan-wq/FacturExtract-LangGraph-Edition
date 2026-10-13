import json

import pytest
from langgraph.types import RetryPolicy
from PIL import Image

from facturextract import excel as excel_mod
from facturextract.excel import ExcelMensual
from facturextract.extractor import ErrorPermanente, ErrorTransitorio, ExtractorFalso
from facturextract.grafo1 import (Dependencias, abrir_checkpointer, config_hilo, construir_grafo1, correr,
                                  pausa_pendiente, reanudar)
from tests.factura_ejemplo import factura

RAPIDO = RetryPolicy(max_attempts=3, initial_interval=0.01, backoff_factor=1.0, jitter=False,
                     retry_on=ErrorTransitorio)


@pytest.fixture
def fotos(tmp_path):
    out = []
    for i in range(2):
        p = tmp_path / f"foto{i}.jpg"
        Image.new("RGB", (40, 30), (200, 200, 200)).save(p)
        out.append(str(p))
    return out


class Montaje:
    def __init__(self, repo, r, reloj, extractor):
        self.repo, self.r, self.reloj = repo, r, reloj
        self.deps = Dependencias(repo, extractor, ExcelMensual(repo, r.excel, reloj), reloj, r)
        self.cp = abrir_checkpointer(r.checkpoints_db)
        self.grafo = construir_grafo1(self.deps, self.cp, RAPIDO)


@pytest.fixture
def montar(repo, r, reloj):
    return lambda respuestas: Montaje(repo, r, reloj, ExtractorFalso(respuestas))


def MALA(**kw):
    return factura(iva=1, total=1000001, **kw)


def tipo_pausa(out):
    return out["__interrupt__"][0].value["tipo"]


def confirmar(m, fid, estado="pendiente", nro=None, alerta=None):
    pago = {"estado": estado, "nro_documento": nro, "fecha_alerta": alerta}
    return reanudar(m.grafo, fid, {"accion": "confirmar", "pago": pago})


def sin_nada_guardado(repo, r, fid):
    f = repo.obtener(fid)
    assert f["estado"] != "guardada"
    assert repo.lineas_de(fid) == [] and repo._leer("SELECT * FROM seguimientos") == []
    assert repo.listar_guardadas() == [] and not list(r.excel.glob("*.xlsx"))


def test_toda_factura_se_detiene_en_configurar_pago_y_no_se_guarda_nada(montar, fotos, repo, r):
    m = montar([factura()])
    out = correr(m.grafo, "f1", fotos[:1])
    assert tipo_pausa(out) == "configurar_pago"
    assert repo.obtener("f1")["estado"] == "por_confirmar"
    sin_nada_guardado(repo, r, "f1")


def test_confirmar_pendiente_guarda_y_crea_alerta(montar, fotos, repo):
    m = montar([factura(fecha_vencimiento="2026-11-04")])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1", "pendiente")                               # alerta por defecto: venc − 3 días
    assert out["resultado"] == "guardada"
    f = repo.obtener("f1")
    assert (f["estado"], f["estado_pago"], f["metodo_pago"], f["fecha_pago"]) == ("guardada", "pendiente", None, None)
    assert f["fecha_vencimiento"] == "2026-11-04"
    assert repo._leer("SELECT proxima_alerta FROM seguimientos")[0]["proxima_alerta"] == "2026-11-01"
    assert m.deps.excel.ruta_mes("2026-10").exists()
    assert pausa_pendiente(m.grafo, "f1") is None


def test_alerta_editada_por_el_usuario(montar, fotos, repo):
    m = montar([factura(fecha_vencimiento="2026-11-04")])
    correr(m.grafo, "f1", fotos[:1])
    confirmar(m, "f1", "pendiente", alerta="20-10-2026")
    assert repo._leer("SELECT proxima_alerta FROM seguimientos")[0]["proxima_alerta"] == "2026-10-20"
    assert repo.obtener("f1")["fecha_vencimiento"] == "2026-11-04"      # el vencimiento no cambia


@pytest.mark.parametrize("metodo", ["contado", "transferencia"])
def test_pagada_contado_o_transferencia_no_pide_numero_ni_crea_alerta(montar, fotos, repo, metodo):
    m = montar([factura(fecha_vencimiento="2026-11-04")])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1", metodo, nro="ignorado")
    assert out["resultado"] == "guardada"
    f = repo.obtener("f1")
    assert (f["estado_pago"], f["metodo_pago"], f["fecha_pago"], f["nro_documento"]) == (
        "pagada", metodo, "2026-10-12", None)
    assert f["fecha_vencimiento"] == "2026-11-04"                       # pagar no cambia el vencimiento
    assert repo._leer("SELECT * FROM seguimientos") == []


def test_cheque_exige_numero_y_vuelve_a_preguntar(montar, fotos, repo, r):
    m = montar([factura()])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1", "cheque", nro="  ")
    assert out["__interrupt__"][0].value["error"] == "Un pago con cheque requiere el N° de documento."
    sin_nada_guardado(repo, r, "f1")
    out = confirmar(m, "f1", "cheque", nro="8821")
    assert out["resultado"] == "guardada"
    f = repo.obtener("f1")
    assert (f["estado_pago"], f["metodo_pago"], f["nro_documento"]) == ("pagada", "cheque", "8821")


def test_alerta_anterior_a_hoy_se_rechaza(montar, fotos):
    m = montar([factura()])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1", "pendiente", alerta="11-10-2026")
    assert "anterior a hoy" in out["__interrupt__"][0].value["error"]
    assert confirmar(m, "f1", "pendiente", alerta="12-10-2026")["resultado"] == "guardada"   # hoy sí vale


def test_pendiente_sin_vencimiento_se_pide_o_se_cambia_a_pagada(montar, fotos, repo):
    m = montar([factura(fecha_vencimiento=None, pago_sugerido="contado")])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1", "pendiente")
    assert "vencimiento" in out["__interrupt__"][0].value["error"]
    out = confirmar(m, "f1", "contado")
    assert out["resultado"] == "guardada" and repo.obtener("f1")["estado_pago"] == "pagada"


def test_decisiones_de_pago_invalidas_vuelven_a_preguntar(montar, fotos):
    m = montar([factura()])
    correr(m.grafo, "f1", fotos[:1])
    for dec in ({"accion": "quizas"}, {"accion": "confirmar"}, {"accion": "confirmar", "pago": {"estado": "efectivo"}},
                {"accion": "confirmar", "pago": {"estado": "pendiente", "fecha_alerta": "no es fecha"}}):
        out = reanudar(m.grafo, "f1", dec)
        assert out["__interrupt__"][0].value["error"], dec
    assert confirmar(m, "f1")["resultado"] == "guardada"


def test_nota_de_credito_no_genera_alerta_ni_pregunta_pago(montar, fotos, repo):
    m = montar([factura(tipo_documento="nota_credito", fecha_vencimiento=None)])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1", "cheque")                                  # el estado no aplica: se ignora
    assert out["resultado"] == "guardada"
    assert repo.obtener("f1")["estado_pago"] == "no aplica" and repo._leer("SELECT * FROM seguimientos") == []


def test_factura_de_mes_anterior_pendiente_genera_alerta(montar, fotos, repo):
    m = montar([factura(fecha_emision="2026-09-20", fecha_vencimiento="2026-10-20")])
    correr(m.grafo, "f1", fotos[:1])
    assert confirmar(m, "f1", "pendiente")["resultado"] == "guardada"
    f = repo.obtener("f1")
    assert f["estado_pago"] == "pendiente" and f["mes_emision"] == "2026-09" and f["fecha_vencimiento"] == "2026-10-20"
    assert repo._leer("SELECT proxima_alerta FROM seguimientos")[0]["proxima_alerta"] == "2026-10-17"
    assert m.deps.excel.ruta_mes("2026-09").exists() and not m.deps.excel.ruta_mes("2026-10").exists()   # Excel por emisión


def test_rechazar_desde_configurar_pago(montar, fotos, repo, r):
    m = montar([factura()])
    correr(m.grafo, "f1", fotos[:1])
    out = reanudar(m.grafo, "f1", {"accion": "rechazar"})
    assert out["resultado"] == "rechazada" and repo.obtener("f1")["estado"] == "rechazada"
    assert repo.listar_guardadas() == []


def test_imagenes_copiadas_y_estado_sin_imagen(montar, fotos, r):
    m = montar([factura()])
    out = correr(m.grafo, "f1", fotos)        # dos fotos = una factura
    estado = m.grafo.get_state(config_hilo("f1")).values
    assert len(estado["rutas_imagen"]) == 2 and all(str(r.imagenes) in p for p in estado["rutas_imagen"])
    assert len(json.dumps(estado)) < 20000    # nada de bytes de imagen en el estado
    assert not any(isinstance(v, (bytes, bytearray)) for v in estado.values())
    assert tipo_pausa(out) == "configurar_pago"


def test_reintento_con_retroalimentacion_luego_correcto(montar, fotos):
    m = montar([MALA(), factura()])
    out = correr(m.grafo, "f1", fotos[:1])
    assert tipo_pausa(out) == "configurar_pago"                  # la 2.ª lectura pasó: sin revisión individual
    llamadas = m.deps.extractor.llamadas
    assert llamadas[0]["hallazgos_previos"] is None
    assert any(h["codigo"] == "V06" for h in llamadas[1]["hallazgos_previos"])


def test_reintentos_agotados_revision_aprobar_y_luego_configurar_pago(montar, fotos, repo, r):
    m = montar([MALA(), MALA(), MALA()])
    out = correr(m.grafo, "f1", fotos[:1])
    assert tipo_pausa(out) == "revision" and len(m.deps.extractor.llamadas) == 3   # 1 + tope de 2 reintentos
    assert repo.obtener("f1")["estado"] == "en_revision"
    assert repo.obtener("f1")["extraido"]["folio"] == "1001"
    sin_nada_guardado(repo, r, "f1")
    out = reanudar(m.grafo, "f1", {"accion": "aprobar"})
    assert tipo_pausa(out) == "configurar_pago" and repo.obtener("f1")["estado"] == "por_confirmar"
    sin_nada_guardado(repo, r, "f1")
    out = confirmar(m, "f1")
    f = repo.obtener("f1")
    assert out["resultado"] == "guardada" and f["aprobada_manual"] == 1
    assert f["estado_validacion"].startswith("Aprobada manualmente")
    assert len(m.deps.extractor.llamadas) == 3                   # no volvió a llamar al modelo


def test_corregir_valida_de_nuevo_y_pasa_a_configurar_pago(montar, fotos, repo):
    m = montar([MALA(), MALA(), MALA()])
    correr(m.grafo, "f1", fotos[:1])
    out = reanudar(m.grafo, "f1", {"accion": "corregir", "campos": factura()})
    assert tipo_pausa(out) == "configurar_pago"
    assert confirmar(m, "f1")["resultado"] == "guardada" and repo.obtener("f1")["aprobada_manual"] == 0


def test_corregir_con_errores_vuelve_a_revision_sin_reextraer(montar, fotos):
    m = montar([MALA(), MALA(), MALA()])
    correr(m.grafo, "f1", fotos[:1])
    out = reanudar(m.grafo, "f1", {"accion": "corregir", "campos": MALA(folio="55")})
    assert tipo_pausa(out) == "revision" and len(m.deps.extractor.llamadas) == 3


def test_rechazar_en_revision(montar, fotos, repo):
    m = montar([MALA(), MALA(), MALA()])
    correr(m.grafo, "f1", fotos[:1])
    out = reanudar(m.grafo, "f1", {"accion": "rechazar"})
    assert out["resultado"] == "rechazada" and repo.obtener("f1")["estado"] == "rechazada"


def test_decision_de_revision_invalida_vuelve_a_preguntar(montar, fotos):
    m = montar([MALA(), MALA(), MALA()])
    correr(m.grafo, "f1", fotos[:1])
    out = reanudar(m.grafo, "f1", {"accion": "quizas"})
    assert out["__interrupt__"][0].value["error"]
    out = reanudar(m.grafo, "f1", {"accion": "corregir"})
    assert out["__interrupt__"][0].value["error"]
    out = reanudar(m.grafo, "f1", {"accion": "corregir", "campos": {"total": "abc"}})
    assert "no es un número" in out["__interrupt__"][0].value["error"]
    assert tipo_pausa(reanudar(m.grafo, "f1", {"accion": "aprobar"})) == "configurar_pago"


def test_duplicado_no_pasa_por_la_pausa(montar, fotos, repo):
    m = montar([factura(), factura()])
    correr(m.grafo, "f1", fotos[:1])
    confirmar(m, "f1")
    out = correr(m.grafo, "f2", fotos[:1])
    assert out["resultado"] == "duplicada" and repo.obtener("f2")["estado"] == "duplicada"
    assert len(repo.listar_guardadas()) == 1 and len(repo._leer("SELECT * FROM seguimientos")) == 1


def test_duplicado_confirmado_a_la_vez_lo_frena_la_restriccion_unica(montar, fotos, repo):
    m = montar([factura(), factura()])
    correr(m.grafo, "f1", fotos[:1])
    correr(m.grafo, "f2", fotos[:1])                              # ninguna guardada aún: ambas llegan a la pausa
    assert confirmar(m, "f1")["resultado"] == "guardada"
    assert confirmar(m, "f2")["resultado"] == "duplicada"          # última defensa
    assert len(repo.listar_guardadas()) == 1


def test_error_transitorio_se_reintenta(montar, fotos):
    m = montar([ErrorTransitorio("red"), ErrorTransitorio("red"), factura()])
    assert tipo_pausa(correr(m.grafo, "f1", fotos[:1])) == "configurar_pago"
    assert len(m.deps.extractor.llamadas) == 3


def test_error_transitorio_agotado_propaga_y_se_puede_reanudar(montar, fotos, repo):
    m = montar([ErrorTransitorio("red")] * 3 + [factura()])
    with pytest.raises(ErrorTransitorio):
        correr(m.grafo, "f1", fotos[:1])
    assert repo.obtener("f1")["estado"] == "extrayendo"
    out = m.grafo.invoke(None, config_hilo("f1"))
    assert tipo_pausa(out) == "configurar_pago"


def test_error_permanente_marca_error_reintentable_sin_reintentar(montar, fotos, repo):
    m = montar([ErrorPermanente("La clave de API no es válida."), factura()])
    out = correr(m.grafo, "f1", fotos[:1])
    assert out["resultado"] == "error" and len(m.deps.extractor.llamadas) == 1
    f = repo.obtener("f1")
    assert f["estado"] == "error_reintentable" and "clave" in f["mensaje"]


def test_reanudacion_de_la_pausa_de_pago_tras_reiniciar_el_proceso(repo, r, reloj, fotos):
    m1 = Montaje(repo, r, reloj, ExtractorFalso([factura()]))
    out = correr(m1.grafo, "f1", fotos[:1])
    assert tipo_pausa(out) == "configurar_pago"
    m1.cp.conn.close()
    del m1                                                      # «se cierra la aplicación»
    m2 = Montaje(repo, r, reloj, ExtractorFalso([]))           # proceso nuevo: grafo y conexión nuevos
    sin_nada_guardado(repo, r, "f1")                            # mientras no se confirma, nada se guarda
    assert pausa_pendiente(m2.grafo, "f1")["tipo"] == "configurar_pago"
    out = confirmar(m2, "f1", "cheque", nro="123")
    assert out["resultado"] == "guardada" and repo.obtener("f1")["nro_documento"] == "123"


def test_reanudacion_de_la_pausa_de_revision_tras_reiniciar(repo, r, reloj, fotos):
    m1 = Montaje(repo, r, reloj, ExtractorFalso([MALA(), MALA(), MALA()]))
    assert tipo_pausa(correr(m1.grafo, "f1", fotos[:1])) == "revision"
    m1.cp.conn.close()
    m2 = Montaje(repo, r, reloj, ExtractorFalso([]))
    assert tipo_pausa(reanudar(m2.grafo, "f1", {"accion": "aprobar"})) == "configurar_pago"


def test_excel_bloqueado_no_pierde_la_factura(montar, fotos, repo, monkeypatch):
    monkeypatch.setattr(excel_mod.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    m = montar([factura()])
    correr(m.grafo, "f1", fotos[:1])
    out = confirmar(m, "f1")
    assert out["resultado"] == "guardada" and "bloqueado" in out["mensaje"]
    assert repo.meses_desactualizados() == ["2026-10"]
