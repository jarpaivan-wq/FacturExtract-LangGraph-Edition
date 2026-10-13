"""Planificador: código (no un grafo) que decide qué seguimientos tocan y lanza un run del grafo 2 por cada uno.

Todo recibe «ahora» como parámetro (reloj inyectable). Idempotente: el hilo es estable
(alerta + id + fecha), así que revisar varias veces el mismo día no duplica popups.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Callable

from . import grafo2
from .config import HORA_ALERTA_POR_DEFECTO
from .db import Repo

log = logging.getLogger(__name__)
CLAVE_HORA = "hora_alerta"
CLAVE_ULTIMA_APERTURA = "ultima_apertura_ventana"


def parse_hora(texto: str | None) -> time:
    try:
        h, m = (texto or HORA_ALERTA_POR_DEFECTO).split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        h, m = HORA_ALERTA_POR_DEFECTO.split(":")
        return time(int(h), int(m))


class Planificador:
    def __init__(self, repo: Repo, grafo, hora_por_defecto: str = HORA_ALERTA_POR_DEFECTO):
        self.repo, self.grafo = repo, grafo
        self.hora_por_defecto = hora_por_defecto

    def hora_alerta(self) -> time:
        return parse_hora(self.repo.ajuste(CLAVE_HORA, self.hora_por_defecto))

    def revisar(self, ahora: datetime, abrir_si_hay_nuevas: bool = False) -> dict:
        """Lanza un run por cada seguimiento pendiente con próxima alerta <= hoy (si ya pasó la hora).

        Devuelve {'lanzados': n, 'esperando': n, 'abrir_ventana': bool}. La ventana se abre sola solo la
        primera vez del día que haya pendientes.
        """
        hoy = ahora.date()
        lanzados = 0
        if ahora.time() >= self.hora_alerta():
            for s in self.repo.seguimientos_a_alertar(hoy):
                hilo = grafo2.hilo_alerta(s["id"], hoy)
                try:
                    grafo2.lanzar(self.grafo, s["id"], s["factura_id"], hilo)
                    lanzados += 1
                except Exception:
                    log.exception("No se pudo lanzar la alerta %s", s["id"])
        esperando = len(self.repo.seguimientos_esperando())
        abrir = False
        pasada_la_hora = ahora.time() >= self.hora_alerta()
        primera_del_dia = self.repo.ajuste(CLAVE_ULTIMA_APERTURA) != hoy.isoformat()
        # La ventana se abre sola la primera vez del día con pendientes; además, si la persona acaba de cargar una
        # factura cuya alerta toca hoy (vencimiento cercano o vencida), se muestra aunque ya se haya abierto antes.
        if esperando and pasada_la_hora and (primera_del_dia or (abrir_si_hay_nuevas and lanzados)):
            self.repo.set_ajuste(CLAVE_ULTIMA_APERTURA, hoy.isoformat())
            abrir = True
        return {"lanzados": lanzados, "esperando": esperando, "abrir_ventana": abrir}

    def retomar_al_iniciar(self, hoy: date) -> int:
        """Los checkpoints son desechables: lo que esperaba una pausa que ya no existe vuelve a «pendiente»."""
        vivos = {s["hilo"] for s in self.repo.seguimientos_esperando()
                 if s["hilo"] and grafo2.hilo_en_pausa(self.grafo, s["hilo"])}
        return self.repo.liberar_esperando_sin_hilo(vivos, hoy)

    def decidir(self, seguimiento_id: int, decision: dict) -> dict:
        """Reanuda el run del seguimiento con la decisión del usuario. Devuelve el estado final o la re-pregunta."""
        seg = self.repo.obtener_seguimiento(seguimiento_id)
        if not seg or seg["estado"] != "esperando_decision" or not seg["hilo"]:
            return {"resultado": "no_esperando"}
        return grafo2.decidir(self.grafo, seg["hilo"], decision)
