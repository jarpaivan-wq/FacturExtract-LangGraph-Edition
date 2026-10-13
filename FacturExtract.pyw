"""Arranque sin consola de FacturExtract (lo usan el lanzador y el inicio con Windows).

Con pythonw no hay consola: cualquier fallo al iniciar se anota en <datos>/logs/arranque.log.
"""
import os
import sys
import time
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))


def _anotar_fallo() -> None:
    """Último recurso: funciona aunque el paquete esté incompleto (por ejemplo, una actualización a medias)."""
    try:
        try:
            from facturextract import arranque

            arranque.anotar_excepcion("ERROR fatal al importar/arrancar")
            return
        except Exception:
            pass
        datos = os.environ.get("FACTUREXTRACT_DATOS") or str(Path.home() / "FacturExtract_datos")
        ruta = Path(datos) / "logs" / "arranque.log"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | pid {os.getpid()} | ERROR fatal al importar/arrancar | "
                    f"python={sys.executable} script={__file__} cwd={os.getcwd()} | "
                    + traceback.format_exc().strip().replace("\n", " | ") + "\n")
    except Exception:
        pass


try:
    from facturextract.app import main

    codigo = main()
except SystemExit:
    raise
except BaseException:
    _anotar_fallo()
    codigo = 1
sys.exit(codigo)
