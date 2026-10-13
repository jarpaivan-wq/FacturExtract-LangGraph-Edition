"""Punto de entrada de la aplicación residente (instancia única, bandeja, inicio con Windows)."""
from __future__ import annotations

import logging
import os
import sys
from tkinter import messagebox

from . import arranque, config, residente
from .db import Repo
from .extractor import ExtractorClaude
from .servicio import Servicio

CLAVE_PRIMERA_EJECUCION = "primera_ejecucion_hecha"


def configurar_registro(r: config.Rutas) -> None:
    r.logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=r.logs / "app.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def construir_servicio(extractor=None, reloj=None, argv: list[str] | None = None) -> tuple[Servicio, Repo, config.Rutas]:
    r = config.rutas().crear()
    configurar_registro(r)
    config.cargar_env(r)
    repo = Repo(r.facturas_db)
    servicio = Servicio(r, repo, extractor or ExtractorClaude(), reloj or config.reloj_para_ejecucion(argv))
    return servicio, repo, r


def ofrecer_inicio_con_windows(ventana, repo: Repo, inicio: residente.InicioWindows) -> None:
    """Primera ejecución (solo instalación de cliente): se ofrece iniciar con Windows. Después se puede cambiar
    cuando se quiera con la casilla «Iniciar con Windows» (ventana principal y menú de la bandeja)."""
    if repo.ajuste(CLAVE_PRIMERA_EJECUCION):
        return
    repo.set_ajuste(CLAVE_PRIMERA_EJECUCION, "1")
    if config.modo_desarrollo() or inicio.activo():
        return
    if messagebox.askyesno(
            "Iniciar con Windows",
            "¿Quiere que FacturExtract se abra solo al encender el computador?\n\n"
            "Así los avisos de vencimiento le llegan sin tener que acordarse de abrirlo. "
            "Se queda en la bandeja del sistema (junto al reloj). Puede cambiarlo cuando quiera con la casilla "
            "«Iniciar con Windows».", parent=ventana):
        ok = inicio.activar()
        arranque.anotar("inicio con Windows", accion="activar (primera ejecución)", resultado="ok" if ok else "error",
                        comando=inicio.comando_deseado())
        if not ok:
            messagebox.showwarning("Iniciar con Windows", "No se pudo activar el inicio automático.", parent=ventana)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    oculto = "--oculto" in argv
    datos = config.rutas().crear().datos
    inicio = residente.InicioWindows(config.RAIZ, datos)
    arranque.anotar("arranque", datos, modo="desarrollo" if config.modo_desarrollo() else "cliente", datos_dir=datos,
                    raiz=config.RAIZ, **arranque.descripcion_proceso())

    if "--quitar-inicio" in argv:           # desactivación limpia (también sirve para desinstalar)
        quitado = inicio.quitar_restos()
        arranque.anotar("quitar inicio", datos, quitado="; ".join(quitado) or "nada que quitar")
        return 0

    unica = residente.InstanciaUnica(datos)   # bloqueo propio en la carpeta de datos
    if not unica.tomar():
        if oculto:
            # Arranque automático con otra copia ya abierta: no se muestra nada (antes se le pedía mostrarse a la
            # primera y la ventana aparecía sola al iniciar sesión).
            arranque.anotar("segunda instancia", datos, resultado="ya hay una abierta; arranque oculto, no se muestra nada")
        else:
            unica.avisar_a_la_otra()          # abierta a mano: se le pide mostrarse y esta termina
            arranque.anotar("segunda instancia", datos, resultado="ya hay una abierta; se le pidió mostrar su ventana")
        return 0

    try:
        return _ejecutar(argv, oculto, datos, inicio, unica)
    except SystemExit:
        raise
    except BaseException:
        arranque.anotar_excepcion("ERROR al arrancar", datos)
        unica.liberar()
        if not oculto:
            try:
                messagebox.showerror("FacturExtract", "FacturExtract no pudo iniciar. Envíe el archivo "
                                     f"{arranque.ruta_log(datos)} a quien le dio el programa.")
            except Exception:
                pass
        return 1


def _ejecutar(argv: list[str], oculto: bool, datos, inicio: residente.InicioWindows, unica) -> int:
    from .ui import VentanaPrincipal

    try:
        servicio, repo, r = construir_servicio(argv=argv)
    except ValueError as e:        # fecha simulada mal escrita (solo desarrollo)
        messagebox.showerror("FacturExtract", str(e))
        unica.liberar()
        return 2
    arranque.anotar("servicio", datos, clave_api="presente" if os.environ.get("ANTHROPIC_API_KEY") else "AUSENTE",
                    fecha_simulada=getattr(servicio.reloj, "simulado", False))
    al_dia = arranque.dependencias_al_dia(config.RAIZ, datos)
    if al_dia is False:
        arranque.anotar("AVISO dependencias desactualizadas", datos,
                        ayuda="ejecute «Iniciar FacturExtract.bat» para completar la actualización")
    if not config.modo_desarrollo():        # el cliente: se mantiene al día una entrada ya existente
        arranque.anotar("inicio con Windows", datos, estado=inicio.reparar(), comando=inicio.comando_deseado())
    servicio.iniciar()
    servicio.iniciar_planificador()
    ventana = VentanaPrincipal(servicio, repo, r, inicio=inicio)
    try:
        ventana.bandeja = residente.Bandeja(ventana.pedir, inicio_activo=inicio.activo)
        ventana.bandeja.iniciar()
        arranque.anotar("bandeja", datos, resultado="ok")
    except Exception:
        arranque.anotar_excepcion("bandeja", datos)
        logging.getLogger(__name__).exception("No se pudo crear el ícono de la bandeja")
        ventana.bandeja = None
    unica.escuchar(lambda: ventana.pedir("abrir"))
    if oculto and ventana.bandeja is not None:
        ventana.withdraw()
        arranque.anotar("ventana", datos, estado="oculta en la bandeja")
    else:
        arranque.anotar("ventana", datos, estado="visible")
        ventana.after(600, lambda: ofrecer_inicio_con_windows(ventana, repo, inicio))
    try:
        ventana.mainloop()
    finally:
        unica.liberar()
        servicio.detener()
        arranque.anotar("salida", datos, resultado="normal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
