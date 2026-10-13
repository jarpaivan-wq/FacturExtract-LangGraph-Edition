"""Servicio: un único hilo trabajador que ejecuta los grafos y todas las escrituras.

La interfaz nunca toca los grafos: encola trabajos y recibe eventos por una cola (queue.Queue)
que ella misma vacía desde su hilo. Los errores llegan como eventos con texto en lenguaje normal;
el detalle técnico va al registro (logs/app.log).
"""
from __future__ import annotations

import logging
from datetime import timedelta
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import grafo1, grafo2
from .config import INTERVALO_MINUTOS_POR_DEFECTO, Reloj, Rutas
from .db import DecisionInvalida, Repo
from .excel import ExcelMensual
from .extractor import ErrorExtractor, Extractor
from .planificador import Planificador

log = logging.getLogger(__name__)
MENSAJE_GENERICO = "Ocurrió un problema inesperado. La factura quedó guardada para reintentar; si se repite, avise al soporte."


@dataclass
class Evento:
    tipo: str                     # progreso | factura | lote_fin | aviso | error | revision | alertas | excel
    datos: dict[str, Any] = field(default_factory=dict)


class Servicio:
    def __init__(self, rutas: Rutas, repo: Repo, extractor: Extractor, reloj: Reloj,
                 eventos: "queue.Queue[Evento] | None" = None, politica=None):
        self.rutas, self.repo, self.extractor, self.reloj = rutas, repo, extractor, reloj
        self.eventos: queue.Queue[Evento] = eventos or queue.Queue()
        self.excel = ExcelMensual(repo, rutas.excel, reloj)
        self._politica = politica
        self._trabajos: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._hilo = threading.Thread(target=self._bucle, name="trabajador", daemon=True)
        self._grafo1 = None
        self._grafo2 = None
        self.planificador: Planificador | None = None
        self._cp = None
        self._listo = threading.Event()
        self._parar = threading.Event()
        self._reloj_hilo: threading.Thread | None = None

    # --------------------------------------------------------------- ciclo de vida
    def iniciar(self) -> None:
        self._hilo.start()
        self.encolar(self._al_iniciar)

    def detener(self) -> None:
        self._parar.set()
        self._trabajos.put(None)
        self._hilo.join(timeout=10)

    def encolar(self, fn: Callable[[], None]) -> None:
        self._trabajos.put(fn)

    def esperar_inactivo(self) -> None:
        """Bloquea hasta que el trabajador termine lo encolado (para pruebas y cierre ordenado)."""
        hecho = threading.Event()
        self._trabajos.put(hecho.set)
        hecho.wait()

    def emitir(self, tipo: str, **datos: Any) -> None:
        self.eventos.put(Evento(tipo, datos))

    def _bucle(self) -> None:
        self._cp = grafo1.abrir_checkpointer(self.rutas.checkpoints_db)  # conexión propia de este hilo
        deps = grafo1.Dependencias(self.repo, self.extractor, self.excel, self.reloj, self.rutas)
        self._grafo1 = grafo1.construir_grafo1(deps, self._cp, self._politica)
        self._grafo2 = grafo2.construir_grafo2(deps, self._cp)
        self.planificador = Planificador(self.repo, self._grafo2)
        self._listo.set()
        while True:
            trabajo = self._trabajos.get()
            if trabajo is None:
                break
            try:
                trabajo()
            except Exception:
                log.exception("Falló un trabajo del servicio")
                self.emitir("error", mensaje=MENSAJE_GENERICO)
        self._cp.conn.close()

    # ------------------------------------------------------------------ al iniciar
    def _al_iniciar(self) -> None:
        """Retoma pausas pendientes y reinicia extracciones interrumpidas; reintenta Excel marcados."""
        for f in self.repo.listar_por_estado("registrada", "extrayendo"):
            self._ejecutar_factura(f["id"], f["imagenes"], reinicio=True)
        for f in self.repo.listar_por_estado("en_revision", "por_confirmar"):
            if grafo1.pausa_pendiente(self._grafo1, f["id"]) is None:   # checkpoint perdido: se reextrae
                self._ejecutar_factura(f["id"], f["imagenes"], reinicio=True)
        self.planificador.retomar_al_iniciar(self.reloj.hoy())
        self._tick()
        self.emitir("revision")

    # ------------------------------------------------------------- planificador
    def _tick(self, abrir_si_hay_nuevas: bool = False) -> None:
        """Revisión periódica (y al iniciar/despertar): Excel pendientes + vencimientos que tocan."""
        try:
            estado = self.excel.reintentar_pendientes()
            if estado:
                self.emitir("excel", estado=estado)
            r = self.planificador.revisar(self.reloj.ahora(), abrir_si_hay_nuevas)
        except Exception:      # un fallo de la revisión periódica se registra; no molesta al usuario cada 15 minutos
            log.exception("Falló la revisión de vencimientos")
            return
        self.emitir("alertas", **r)

    def revisar_vencimientos(self) -> None:
        self.encolar(self._tick)

    def intervalo_minutos(self) -> float:
        try:
            return max(float(self.repo.ajuste("intervalo_minutos", str(INTERVALO_MINUTOS_POR_DEFECTO))), 0.01)
        except (TypeError, ValueError):
            return INTERVALO_MINUTOS_POR_DEFECTO

    def iniciar_planificador(self, intervalo_minutos: float | None = None, paso_segundos: float = 20.0) -> None:
        """Hilo liviano que solo encola una revisión cada N minutos (la ejecuta el trabajador).

        Compara el reloj de pared (inyectable) con la última revisión cada `paso_segundos`: así, al despertar el PC
        de la suspensión —o si se adelanta la fecha simulada— la revisión ocurre de inmediato y no hay que esperar
        a que venza un temporizador que pudo quedar congelado. El intervalo se lee de los ajustes en cada vuelta.
        """
        def bucle() -> None:
            ultimo = self.reloj.ahora()
            while not self._parar.wait(paso_segundos):
                ahora = self.reloj.ahora()
                cada = timedelta(minutes=intervalo_minutos if intervalo_minutos is not None else self.intervalo_minutos())
                if ahora - ultimo >= cada or ahora < ultimo:
                    ultimo = ahora
                    self.revisar_vencimientos()
        self._reloj_hilo = threading.Thread(target=bucle, name="planificador", daemon=True)
        self._reloj_hilo.start()

    def ajustes_avisos(self) -> tuple[str, float]:
        """(hora de los avisos «HH:MM», intervalo de revisión en minutos)."""
        return f"{self.planificador.hora_alerta():%H:%M}", self.intervalo_minutos()

    def guardar_ajustes_avisos(self, hora: str, intervalo: str) -> str | None:
        """Valida y guarda la hora de los avisos y el intervalo. Devuelve un mensaje de error o None."""
        try:
            h, m = hora.strip().split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
            iv = float(intervalo.replace(",", "."))
            if not (1 <= iv <= 240):
                raise ValueError
        except ValueError:
            return "Escriba la hora como HH:MM (por ejemplo 09:00) y un intervalo entre 1 y 240 minutos."

        def trabajo() -> None:
            self.repo.set_ajuste("hora_alerta", f"{int(h):02d}:{int(m):02d}")
            self.repo.set_ajuste("intervalo_minutos", str(iv))
            self._tick()
        self.encolar(trabajo)
        return None

    def decidir_vencimiento(self, seguimiento_id: int, decision: dict) -> None:
        def trabajo() -> None:
            try:
                salida = self.planificador.decidir(seguimiento_id, decision)
            except Exception:
                log.exception("Error atendiendo el vencimiento %s", seguimiento_id)
                self.emitir("error", mensaje=MENSAJE_GENERICO)
                return
            pregunta = (salida.get("__interrupt__") or [None])[0]
            if pregunta is not None and pregunta.value.get("error"):
                self.emitir("aviso", mensaje=pregunta.value["error"], repregunta=True)
            elif salida.get("excel") not in (None, "ok"):
                self.emitir("excel", estado={"mes": salida["excel"]})
            self.emitir("alertas")
        self.encolar(trabajo)

    # ---------------------------------------------------------------------- lote
    def procesar_lote(self, grupos: list[list[str]]) -> None:
        """Cada grupo (1 o 2 fotos) es UNA factura. Secuencial; una falla no detiene las demás."""
        def trabajo() -> None:
            resumen = {"guardadas": 0, "listas": 0, "revision": 0, "duplicadas": 0, "errores": 0}
            for i, rutas in enumerate(grupos, start=1):
                nombre = " + ".join(Path(r).name for r in rutas)
                self.emitir("progreso", texto=f"({i}/{len(grupos)}) Procesando {nombre}…")
                faltan = [r for r in rutas if not Path(r).is_file()]
                if faltan:
                    resumen["errores"] += 1
                    self.emitir("factura", nombre=nombre, resultado="error",
                                mensaje=f"No se encontró la foto {Path(faltan[0]).name}.")
                    continue
                res = self._ejecutar_factura(uuid.uuid4().hex, rutas, nombre=nombre)
                clave = {"guardada": "guardadas", "lista": "listas", "revision": "revision",
                         "duplicada": "duplicadas"}.get(res, "errores")
                resumen[clave] += 1
            self.emitir("lote_fin", **resumen)
            self.emitir("revision")
        self.encolar(trabajo)

    def _ejecutar_factura(self, fid: str, rutas: list[str], nombre: str | None = None,
                          reinicio: bool = False) -> str:
        """Corre el grafo 1 para una factura. Devuelve guardada|revision|duplicada|rechazada|error."""
        nombre = nombre or " + ".join(Path(r).name for r in rutas)
        try:
            salida = grafo1.correr(self._grafo1, fid, rutas)
        except ErrorExtractor as e:   # transitorio agotado
            self.repo.set_estado(fid, "error_reintentable", e.mensaje)
            self.emitir("factura", id=fid, nombre=nombre, resultado="error", mensaje=e.mensaje)
            return "error"
        except Exception:
            log.exception("Error procesando %s", fid)
            self.repo.set_estado(fid, "error_reintentable", MENSAJE_GENERICO)
            self.emitir("factura", id=fid, nombre=nombre, resultado="error", mensaje=MENSAJE_GENERICO)
            return "error"
        return self._emitir_resultado(fid, nombre, salida)

    def _emitir_resultado(self, fid: str, nombre: str, salida: dict) -> str:
        if salida.get("__interrupt__"):
            tipo = salida["__interrupt__"][0].value.get("tipo")
            self.emitir("factura", id=fid, nombre=nombre, resultado="revision" if tipo == "revision" else "lista",
                        mensaje=("Tiene hallazgos: revísela en la pestaña «Lote»." if tipo == "revision" else
                                 "Lista para configurar pago y confirmar en la pestaña «Lote»."))
            return "revision" if tipo == "revision" else "lista"
        res = salida.get("resultado") or "error"
        self.emitir("factura", id=fid, nombre=nombre, resultado=res, mensaje=salida.get("mensaje") or "",
                    mes=salida.get("mes"))
        return res

    # ------------------------------------------------------------------- revisión
    def decidir_revision(self, fid: str, decision: dict) -> None:
        def trabajo() -> None:
            f = self.repo.obtener(fid)
            nombre = f["imagenes"][0].split("\\")[-1] if f and f["imagenes"] else fid
            try:
                salida = grafo1.reanudar(self._grafo1, fid, decision)
            except Exception:
                log.exception("Error reanudando %s", fid)
                self.emitir("error", mensaje=MENSAJE_GENERICO)
                return
            pregunta = (salida.get("__interrupt__") or [None])[0]
            if pregunta is not None and pregunta.value.get("error"):
                self.emitir("aviso", mensaje=pregunta.value["error"], factura_id=fid, repregunta=True)
            else:
                self._emitir_resultado(fid, nombre, salida)
            self.emitir("revision")
        self.encolar(trabajo)

    def confirmar_lote(self, items: list[dict]) -> None:
        """Confirma (o rechaza) filas de la ventana del lote. Cada fila reanuda SU propio run del grafo 1;
        la decisión se valida dentro del grafo y, si falla, la fila vuelve a preguntar. Un solo evento resume
        el resultado de todas (nada de un aviso por factura)."""
        def trabajo() -> None:
            resultados: dict[str, dict] = {}
            for it in items:
                fid = it["id"]
                pausa = grafo1.pausa_pendiente(self._grafo1, fid)
                if pausa is None or pausa.get("tipo") != "configurar_pago":
                    resultados[fid] = {"ok": False, "mensaje": "Esta factura no está lista para confirmar."}
                    continue
                decision = ({"accion": "rechazar"} if it.get("accion") == "rechazar"
                            else {"accion": "confirmar", "pago": it.get("pago")})
                try:
                    salida = grafo1.reanudar(self._grafo1, fid, decision)
                except Exception:
                    log.exception("Error confirmando %s", fid)
                    resultados[fid] = {"ok": False, "mensaje": MENSAJE_GENERICO}
                    continue
                pregunta = (salida.get("__interrupt__") or [None])[0]
                if pregunta is not None:
                    resultados[fid] = {"ok": False, "mensaje": pregunta.value.get("error") or "Falta confirmar."}
                else:
                    resultados[fid] = {"ok": True, "resultado": salida.get("resultado"),
                                       "mensaje": salida.get("mensaje") or ""}
            self.emitir("lote_confirmado", resultados=resultados)
            self.emitir("revision")
            if any(r["ok"] and r.get("resultado") == "guardada" for r in resultados.values()):
                self._tick(abrir_si_hay_nuevas=True)   # una alerta que toca hoy (vencimiento cercano o vencida) salta de inmediato
            else:
                self.emitir("alertas")
        self.encolar(trabajo)

    # ----------------------------------------------------------------------- pagos
    def pagar(self, fid: str, metodo: str, nro_documento: str | None) -> None:
        """Marcar pagada desde la vista de facturas (mismo registro de pago que usa el grafo 2)."""
        def trabajo() -> None:
            try:
                mes = self.repo.registrar_pago(fid, self.reloj.hoy(), metodo, nro_documento)
            except DecisionInvalida as e:
                self.emitir("aviso", mensaje=str(e))
                return
            estado = self.excel.regenerar(mes)
            self.emitir("pago", factura_id=fid, excel=estado)
            self.emitir("alertas")
        self.encolar(trabajo)

    def corregir_pago(self, fid: str, estado: str, metodo: str | None = None, nro_documento: str | None = None,
                      fecha_pago: str | None = None, fecha_vencimiento: str | None = None) -> None:
        """Corrige el pago de una factura guardada (pestaña Facturas): una transacción en la base, luego regenera el
        Excel del mes de emisión y refresca listas y ventana de vencimientos. Si queda pendiente y su alerta toca
        hoy, la revisión de alertas se hace al instante."""
        def trabajo() -> None:
            try:
                r = self.repo.corregir_pago(fid, self.reloj.hoy(), self.reloj.ahora(), estado, metodo, nro_documento,
                                            fecha_pago, fecha_vencimiento)
            except DecisionInvalida as e:
                self.emitir("aviso", mensaje=str(e))
                return
            if not r["cambio"]:
                return
            excel = self.excel.regenerar(r["mes"])
            self.emitir("pago", factura_id=fid, excel=excel)
            if estado == "pendiente":
                self._tick(abrir_si_hay_nuevas=True)
            else:
                self.emitir("alertas")
        self.encolar(trabajo)

    def reintentar(self, fid: str) -> None:
        def trabajo() -> None:
            f = self.repo.obtener(fid)
            if f and f["estado"] == "error_reintentable":
                self._ejecutar_factura(fid, f["imagenes"])
            self.emitir("revision")
        self.encolar(trabajo)
