"""Registro de cada arranque (solo biblioteca estándar; nunca lanza excepciones).

Con `pythonw` no hay consola: si algo falla al iniciar con Windows, no se ve nada. Este archivo
(`<datos>/logs/arranque.log`) deja constancia de cada arranque —hora, intérprete, ruta, carpeta de datos, resultado—
para poder diagnosticar en el PC del cliente sin ir en persona. No contiene claves ni datos de facturas.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback
from pathlib import Path

MAX_BYTES = 256 * 1024
CONSERVAR = 96 * 1024


def carpeta_datos_segura() -> Path:
    """La carpeta de datos, sin depender de que el resto de la aplicación importe bien."""
    try:
        from . import config

        return config.carpeta_datos()
    except Exception:
        forzada = os.environ.get("FACTUREXTRACT_DATOS")
        return Path(forzada) if forzada else Path.home() / "FacturExtract_datos"


def ruta_log(datos: Path | None = None) -> Path:
    return (Path(datos) if datos else carpeta_datos_segura()) / "logs" / "arranque.log"


def _recortar(ruta: Path) -> None:
    try:
        if ruta.stat().st_size > MAX_BYTES:
            datos = ruta.read_bytes()[-CONSERVAR:]
            ruta.write_bytes(b"... (registro recortado)\n" + datos[datos.find(b"\n") + 1:])
    except OSError:
        pass


def anotar(evento: str, datos: Path | None = None, **detalles) -> None:
    """Agrega una línea: `AAAA-MM-DD HH:MM:SS | pid | evento | clave=valor ...`. Nunca falla."""
    try:
        ruta = ruta_log(datos)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _recortar(ruta)
        extra = " ".join(f"{k}={v}" for k, v in detalles.items())
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | pid {os.getpid()} | {evento}" + (f" | {extra}" if extra else "") + "\n")
    except Exception:
        pass


def anotar_excepcion(evento: str, datos: Path | None = None) -> None:
    anotar(evento, datos, error=traceback.format_exc().strip().replace("\n", " ⏎ "))


def descripcion_proceso() -> dict:
    """Datos que ayudan a diagnosticar: intérprete, script, carpeta de trabajo y argumentos."""
    return {"python": sys.executable, "version": sys.version.split()[0],
            "script": os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else "?", "cwd": os.getcwd(),
            "args": " ".join(sys.argv[1:]) or "(ninguno)"}


def huella_requisitos(app: Path, version: tuple) -> str:
    """Misma huella que usa el lanzador para saber si hay que reinstalar dependencias."""
    h = hashlib.sha256()
    for nombre in ("requirements.txt", "constraints.txt"):
        h.update((Path(app) / nombre).read_bytes())
    h.update(repr(version).encode())
    return h.hexdigest()


def dependencias_al_dia(app: Path, datos: Path) -> bool | None:
    """True/False según el entorno propio coincida con requirements; None si no hay forma de saberlo."""
    try:
        marca = Path(datos) / "entorno" / ".huella"
        if not marca.exists():
            return None
        return marca.read_text().strip() == huella_requisitos(app, tuple(sys.version_info[:2]))
    except Exception:
        return None
