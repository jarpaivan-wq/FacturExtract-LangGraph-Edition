"""Aplicación residente: instancia única, inicio con Windows y ícono en la bandeja del sistema.

Convive con otros programas residentes del mismo equipo: no usa puertos de red, ni nombres de proceso, ni mutex
globales, y nunca mira ni cierra procesos ajenos. Todo lo compartido vive en SU carpeta de datos.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Nombres propios y únicos (no genéricos: en el mismo PC puede haber otras aplicaciones, incluida la v1).
NOMBRE_INICIO = "FacturExtractV2"
NOMBRE_ICONO = "FacturExtractV2.Bandeja"
NOMBRE_ACCESO_DIRECTO = "FacturExtract v2.lnk"
RUTA_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
ARCHIVO_BLOQUEO = "instancia.lock"
ARCHIVO_MOSTRAR = "instancia.mostrar"


# ------------------------------------------------------------ instancia única ----
class InstanciaUnica:
    """Una sola instancia por carpeta de datos, con un bloqueo de archivo propio.

    · El bloqueo lo toma el primer proceso sobre `<datos>/instancia.lock` y el sistema operativo lo libera solo
      si ese proceso termina (aunque sea de golpe): no quedan bloqueos huérfanos.
    · La segunda instancia no toca a la primera: deja un archivo `instancia.mostrar` en la carpeta de datos y
      termina. La primera lo ve (revisa cada medio segundo), lo borra y muestra su ventana.
    """

    def __init__(self, carpeta: Path):
        self.carpeta = Path(carpeta)
        self._f = None

    @property
    def ruta_bloqueo(self) -> Path:
        return self.carpeta / ARCHIVO_BLOQUEO

    @property
    def ruta_mostrar(self) -> Path:
        return self.carpeta / ARCHIVO_MOSTRAR

    def tomar(self) -> bool:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        f = open(self.ruta_bloqueo, "a+b")
        try:
            f.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - la aplicación es para Windows; esto solo ayuda a desarrollar en otros sistemas
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        try:
            self.ruta_mostrar.unlink()      # un pedido viejo de una sesión anterior no cuenta
        except OSError:
            pass
        return True

    def hay_otra(self) -> bool:
        """¿Hay otra instancia con esta carpeta de datos? (la comprueba intentando tomar el bloqueo y soltándolo)."""
        if self._f is not None:
            return False
        if self.tomar():
            self.liberar()
            return False
        return True

    def avisar_a_la_otra(self) -> bool:
        """Pide a la instancia que ya está abierta que muestre su ventana (sin tocarla de ninguna otra forma)."""
        try:
            tmp = self.carpeta / (ARCHIVO_MOSTRAR + ".tmp")
            tmp.write_text("mostrar", encoding="utf-8")
            os.replace(tmp, self.ruta_mostrar)
            return True
        except OSError:
            return False

    def escuchar(self, al_pedir_mostrar: Callable[[], None], cada: float = 0.5) -> None:
        parar = threading.Event()
        self._parar = parar

        def bucle() -> None:
            while self._f is not None and not parar.wait(cada):
                if self.ruta_mostrar.exists():
                    try:
                        self.ruta_mostrar.unlink()
                    except OSError:
                        continue
                    try:
                        al_pedir_mostrar()
                    except Exception:
                        log.exception("Falló al mostrar la ventana")
        threading.Thread(target=bucle, name="instancia-unica", daemon=True).start()

    def liberar(self) -> None:
        f, self._f = self._f, None
        if hasattr(self, "_parar"):
            self._parar.set()
        if f is None:
            return
        try:
            f.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            f.close()
        except OSError:
            pass


# ------------------------------------------------------------ inicio con Windows ----
LEGADO_INICIO = ("FacturExtract",)      # nombre que usó una compilación anterior de la v2 (antes de FacturExtractV2)
# Formato exacto que escribía la v2:  "<ruta>\\pythonw.exe" "<ruta>\\FacturExtract.pyw" --oculto
_FORMATO_V2 = re.compile(r'^"(?P<py>[^"]+\\pythonw\.exe)" "(?P<pyw>[^"]+\\FacturExtract\.pyw)" --oculto$', re.IGNORECASE)


def creada_por_la_v2(valor: str | None) -> bool:
    """¿Este comando de inicio lo escribió FacturExtract v2? Solo entonces se puede retirar una entrada antigua.

    Exige el formato exacto de la v2 y, si la carpeta aún existe, que sea una instalación de la v2
    (`facturextract/residente.py` y `lanzador.py`). Una entrada ajena, o de otra versión, jamás se toca.
    """
    m = _FORMATO_V2.match((valor or "").strip())
    if not m:
        return False
    carpeta = Path(m.group("pyw")).parent
    if carpeta.exists():
        return (carpeta / "facturextract" / "residente.py").exists() and (carpeta / "lanzador.py").exists()
    return True        # la carpeta ya no existe (se borró o se movió): la entrada es nuestra y quedó huérfana


def interprete_sin_consola(datos: Path | None = None) -> str:
    """El Python que debe arrancar con Windows: el del entorno propio del lanzador (`<datos>\\entorno`), sin consola.
    Nunca el global. Si no existe (desarrollo), el actual en su variante `pythonw.exe`."""
    if datos:
        propio = Path(datos) / "entorno" / "Scripts" / "pythonw.exe"
        if propio.exists():
            return str(propio)
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        sin_consola = exe.with_name("pythonw.exe")
        if sin_consola.exists():
            return str(sin_consola)
    return str(exe)


def comando_inicio(raiz, datos: Path | None = None) -> str:
    """Comando de inicio automático: pythonw del entorno + FacturExtract.pyw, oculto en la bandeja. Todo entre
    comillas (rutas con espacios, tildes u otra unidad)."""
    return f'"{interprete_sin_consola(datos)}" "{Path(raiz) / "FacturExtract.pyw"}" --oculto'


def carpeta_escritorio() -> Path:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
    except (OSError, ImportError):
        return Path.home() / "Desktop"


class InicioWindows:
    """Inicio automático con Windows: UNA sola entrada propia en `HKCU\\...\\Run` (sin permisos de administrador).

    · Registrar es idempotente (mismo nombre = mismo valor; nunca se duplica).
    · `reparar()` actualiza la entrada si apunta a otra ruta (carpeta movida o actualizada) y retira la entrada de la
      primera versión (`FacturExtract`) para que no arranquen dos copias.
    · Nunca crea la entrada por su cuenta: solo la activa quien lo pide.
    """

    def __init__(self, raiz: Path, datos: Path | None = None, nombre: str = NOMBRE_INICIO,
                 legado: tuple[str, ...] = LEGADO_INICIO, acceso_directo: str = NOMBRE_ACCESO_DIRECTO):
        self.raiz, self.datos, self.nombre, self.legado = Path(raiz), datos, nombre, tuple(legado)
        self.acceso_directo = acceso_directo

    # ---- registro
    def _abrir(self, escritura: bool = False):
        import winreg

        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUTA_RUN, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
                              if escritura else winreg.KEY_QUERY_VALUE)

    def comando_deseado(self) -> str:
        return comando_inicio(self.raiz, self.datos)

    def valor_actual(self, nombre: str | None = None) -> str | None:
        try:
            import winreg

            with self._abrir() as k:
                return str(winreg.QueryValueEx(k, nombre or self.nombre)[0])
        except (OSError, ImportError):
            return None

    def activo(self) -> bool:
        return self.valor_actual() is not None

    def activar(self) -> bool:
        try:
            import winreg

            self.limpiar_legado()
            with self._abrir(True) as k:
                winreg.SetValueEx(k, self.nombre, 0, winreg.REG_SZ, self.comando_deseado())
            return True
        except (OSError, ImportError):
            log.exception("No se pudo activar el inicio con Windows")
            return False

    def desactivar(self) -> bool:
        """Quita la entrada propia y la de la primera versión. True si no queda ninguna."""
        try:
            import winreg

            self.limpiar_legado()
            with self._abrir(True) as k:
                try:
                    winreg.DeleteValue(k, self.nombre)
                except FileNotFoundError:
                    pass
            return not self.activo()
        except (OSError, ImportError):
            log.exception("No se pudo desactivar el inicio con Windows")
            return False

    def limpiar_legado(self) -> list[str]:
        """Retira entradas de compilaciones anteriores de la v2 SOLO si `creada_por_la_v2` (nunca las ajenas)."""
        quitadas = []
        try:
            import winreg

            for nombre in self.legado:
                valor = self.valor_actual(nombre)
                if valor and creada_por_la_v2(valor):
                    with self._abrir(True) as k:
                        winreg.DeleteValue(k, nombre)
                    quitadas.append(nombre)
        except (OSError, ImportError):
            log.exception("No se pudo limpiar el inicio antiguo")
        return quitadas

    def reparar(self) -> str:
        """'no_registrado' | 'al_dia' | 'reparado' | 'error'. Solo toca el registro si ya había una entrada."""
        quitadas = self.limpiar_legado()
        actual = self.valor_actual()
        if actual is None:
            return "reparado" if quitadas and self.activar() else "no_registrado"
        if actual.strip().lower() == self.comando_deseado().strip().lower():
            return "al_dia"
        return "reparado" if self.activar() else "error"

    def quitar_restos(self) -> list[str]:
        """Desactivación limpia: entradas de inicio (propia y antigua) y el acceso directo `FacturExtract v2.lnk`
        del Escritorio. No toca datos, entorno ni la carpeta de la aplicación."""
        quitado = []
        if self.valor_actual() is not None or any(self.valor_actual(n) for n in self.legado):
            self.desactivar()
            quitado.append("entrada de inicio con Windows")
        lnk = carpeta_escritorio() / self.acceso_directo
        try:
            if lnk.exists():
                lnk.unlink()
                quitado.append(f"acceso directo {lnk.name}")
        except OSError:
            log.exception("No se pudo quitar el acceso directo")
        return quitado


# Funciones sueltas (compatibilidad): operan sobre una entrada por nombre.
def inicio_activo(nombre: str = NOMBRE_INICIO) -> bool:
    return InicioWindows(Path("."), nombre=nombre, legado=()).activo()


def activar_inicio(comando: str, nombre: str = NOMBRE_INICIO) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUTA_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, nombre, 0, winreg.REG_SZ, comando)
        return True
    except (OSError, ImportError):
        log.exception("No se pudo activar el inicio con Windows")
        return False


def desactivar_inicio(nombre: str = NOMBRE_INICIO) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUTA_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, nombre)
        return True
    except (OSError, ImportError):
        return False


# ----------------------------------------------------------------------- bandeja ----
def icono_imagen():
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 4, 58, 60), radius=8, fill=(46, 125, 50, 255))
    d.rectangle((16, 16, 48, 22), fill="white")
    d.rectangle((16, 28, 48, 34), fill="white")
    d.rectangle((16, 40, 36, 46), fill="white")
    return im


class Bandeja:
    """Ícono en la bandeja. Sus callbacks corren en el hilo de pystray: nunca tocan Tk directamente,
    solo piden acciones a la interfaz mediante `pedir(accion)`."""

    def __init__(self, pedir: Callable[[str], None], titulo: str = "FacturExtract",
                 inicio_activo: Callable[[], bool] | None = None):
        import pystray

        self._pystray = pystray
        self.inicio_activo = inicio_activo
        self.pedir = pedir
        self.titulo = titulo
        self.pendientes = 0
        self.icono = pystray.Icon(NOMBRE_ICONO, icono_imagen(), titulo, menu=self._menu())

    def _menu(self):
        p = self._pystray
        return p.Menu(
            p.MenuItem("Abrir FacturExtract", lambda i, it: self.pedir("abrir"), default=True),
            p.MenuItem(lambda it: f"Vencimientos del día ({self.pendientes})", lambda i, it: self.pedir("vencimientos")),
            p.MenuItem("Probar conexión", lambda i, it: self.pedir("conexion")),
            *([p.MenuItem("Iniciar con Windows", lambda i, it: self.pedir("inicio"),
                          checked=lambda it: bool(self.inicio_activo()))] if self.inicio_activo else []),
            p.Menu.SEPARATOR,
            p.MenuItem("Salir", lambda i, it: self.pedir("salir")),
        )

    def iniciar(self) -> None:
        self.icono.run_detached()

    def actualizar(self, pendientes: int) -> None:
        self.pendientes = pendientes
        self.icono.title = f"{self.titulo}: {pendientes} vencimiento(s) pendiente(s)" if pendientes else self.titulo
        try:
            self.icono.update_menu()
        except Exception:
            pass

    def detener(self) -> None:
        try:
            self.icono.stop()
        except Exception:
            pass
