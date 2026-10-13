"""Rutas, modo desarrollo/cliente, carga del .env y reloj.

El valor de la clave de API nunca se imprime ni se registra.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MODELO_POR_DEFECTO = "claude-sonnet-4-6"  # modelo de visión de la v1; configurable con FACTUREXTRACT_MODEL
HORA_ALERTA_POR_DEFECTO = "09:00"
INTERVALO_MINUTOS_POR_DEFECTO = 15


def modo_desarrollo() -> bool:
    return os.environ.get("FACTUREXTRACT_DEV") == "1" or (RAIZ / ".git").exists()


def carpeta_datos() -> Path:
    forzada = os.environ.get("FACTUREXTRACT_DATOS")
    if forzada:
        return Path(forzada)
    if modo_desarrollo():
        return RAIZ / "datos"
    return Path.home() / "FacturExtract_datos"


@dataclass(frozen=True)
class Rutas:
    datos: Path

    @property
    def facturas_db(self) -> Path:
        return self.datos / "facturas.db"

    @property
    def checkpoints_db(self) -> Path:
        return self.datos / "checkpoints.db"

    @property
    def imagenes(self) -> Path:
        return self.datos / "imagenes"

    @property
    def excel(self) -> Path:
        return self.datos / "Facturas"

    @property
    def logs(self) -> Path:
        return self.datos / "logs"

    @property
    def env_datos(self) -> Path:
        return self.datos / ".env"

    @property
    def env_raiz(self) -> Path:
        return RAIZ / ".env"

    def crear(self) -> "Rutas":
        for d in (self.datos, self.imagenes, self.excel, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        return self


def rutas(datos: Path | None = None) -> Rutas:
    return Rutas(Path(datos) if datos else carpeta_datos())


def archivo_env(r: Rutas | None = None) -> Path:
    """Desarrollo: .env de la raíz del proyecto. Cliente: .env de la carpeta de datos."""
    r = r or rutas()
    if modo_desarrollo() and r.env_raiz.exists():
        return r.env_raiz
    return r.env_datos


def cargar_env(r: Rutas | None = None) -> bool:
    """Carga el .env correspondiente al entorno. Devuelve si existe una clave (sin exponerla)."""
    from dotenv import load_dotenv

    path = archivo_env(r)
    if path.exists():
        # override=True: FacturExtract usa SU clave (la de su .env), nunca una variable de entorno heredada que
        # pudiera ser de otro programa del mismo equipo.
        load_dotenv(path, override=True)
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def modelo() -> str:
    return os.environ.get("FACTUREXTRACT_MODEL", MODELO_POR_DEFECTO)


def usar_almacen_windows() -> None:
    """Certificados del sistema (nunca verify=False)."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # pragma: no cover - sin truststore se usa el almacén por defecto de Python
        pass


class Reloj:
    """Reloj inyectable: `ahora()` y `hoy()`. Las pruebas usan RelojFijo."""

    def ahora(self) -> datetime:
        return datetime.now()

    def hoy(self):
        return self.ahora().date()


class RelojFijo(Reloj):
    def __init__(self, ahora: datetime):
        self._ahora = ahora

    def ahora(self) -> datetime:
        return self._ahora

    def fijar(self, ahora: datetime) -> None:
        self._ahora = ahora

    def avanzar(self, **kw) -> None:
        from datetime import timedelta

        self._ahora = self._ahora + timedelta(**kw)


# ------------------------------------------------------------ fecha simulada (solo desarrollo) ----
VAR_FECHA_SIMULADA = "FACTUREXTRACT_FECHA_SIMULADA"
ARG_FECHA_SIMULADA = "--fecha-simulada"


class RelojSimulado(Reloj):
    """«Hoy» fijado a mano para probar alertas. Avanza con el tiempo real desde la fecha elegida y se puede
    adelantar (`avanzar`). Solo existe en desarrollo; la ventana principal lo muestra de forma permanente."""

    simulado = True

    def __init__(self, inicio: datetime, real: "callable | None" = None):
        self._real = real or datetime.now
        self._offset = inicio - self._real()

    def ahora(self) -> datetime:
        return self._real() + self._offset

    def avanzar(self, **kw) -> None:
        from datetime import timedelta

        self._offset += timedelta(**kw)


def parsear_fecha_simulada(texto: str, ahora_real: datetime | None = None) -> datetime:
    """'2026-10-14' (con la hora real de hoy) o '2026-10-14 09:00' / '2026-10-14T09:00'."""
    t = texto.strip().replace("T", " ")
    ahora_real = ahora_real or datetime.now()
    for fmt, con_hora in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%d", False), ("%d-%m-%Y %H:%M", True), ("%d-%m-%Y", False)):
        try:
            d = datetime.strptime(t, fmt)
        except ValueError:
            continue
        return d if con_hora else d.replace(hour=ahora_real.hour, minute=ahora_real.minute, second=ahora_real.second)
    raise ValueError(f"Fecha simulada no válida: «{texto}». Use AAAA-MM-DD o AAAA-MM-DD HH:MM.")


def valor_fecha_simulada(argv: list[str] | None = None, env: dict | None = None) -> str | None:
    env = os.environ if env is None else env
    for i, a in enumerate(argv or []):
        if a.startswith(ARG_FECHA_SIMULADA + "="):
            return a.split("=", 1)[1]
        if a == ARG_FECHA_SIMULADA and i + 1 < len(argv or []):
            return argv[i + 1]
    return env.get(VAR_FECHA_SIMULADA) or None


def reloj_para_ejecucion(argv: list[str] | None = None, env: dict | None = None) -> Reloj:
    """Reloj real, salvo que se pida una fecha simulada Y estemos en desarrollo. En el uso normal del cliente
    (sin carpeta .git ni FACTUREXTRACT_DEV=1) la petición se ignora."""
    valor = valor_fecha_simulada(argv, env)
    if not valor:
        return Reloj()
    if not modo_desarrollo():
        import logging

        logging.getLogger(__name__).warning("Se ignoró la fecha simulada: solo existe en modo desarrollo.")
        return Reloj()
    return RelojSimulado(parsear_fecha_simulada(valor))
