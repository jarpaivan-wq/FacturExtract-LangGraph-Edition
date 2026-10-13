"""Lanzador de FacturExtract (solo biblioteca estándar).

Primera ejecución: busca Python con el lanzador `py`, verifica la versión mínima, crea el entorno
virtual en la carpeta de datos, instala las dependencias con versiones fijadas, pide la clave de
API en una ventana simple (la guarda en el .env de la carpeta de datos), crea las carpetas de
trabajo y abre la aplicación. Ejecuciones siguientes: revisa que todo siga en orden y abre.

Uso:  pyw lanzador.py                (normal)
      py  lanzador.py --solo-preparar (prepara entorno y carpetas; no pide clave ni abre; para pruebas)
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

PYTHON_MINIMO = (3, 10)        # exigido por langgraph, anthropic y pillow (Requires-Python >=3.10)
PYTHON_MAXIMO_PREFERIDO = (3, 13)
SIN_VENTANA = 0x08000000       # CREATE_NO_WINDOW


class ErrorLanzador(Exception):
    """Mensaje en lenguaje normal para el usuario."""


# ---------------------------------------------------------------- utilidades puras ----
def parsear_py_list(salida: str) -> list[str]:
    """Rutas de `py -0p` (líneas como ' -V:3.13 *        C:\\Python313\\python.exe')."""
    rutas = []
    for linea in salida.splitlines():
        partes = linea.strip().split(None, 2)
        for p in reversed(partes):
            if p.lower().endswith(".exe"):
                rutas.append(p)
                break
    return rutas


def version_de(python: str, ejecutar=subprocess.run):
    try:
        r = ejecutar([python, "-c", "import sys;print(sys.version_info[0], sys.version_info[1])"],
                     capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        a, b = r.stdout.split()[:2]
        return int(a), int(b)
    except Exception:
        return None


def elegir_python(candidatos: list[str], version_fn=version_de):
    """Devuelve (ruta, versión) del mejor Python >= mínimo (prefiere el más nuevo hasta el preferido)."""
    validos = []
    for c in candidatos:
        v = version_fn(c)
        if v and v >= PYTHON_MINIMO:
            validos.append((v, c))
    if not validos:
        return None
    preferidos = [x for x in validos if x[0] <= PYTHON_MAXIMO_PREFERIDO] or validos
    v, ruta = max(preferidos)
    return ruta, v


def listar_candidatos(ejecutar=subprocess.run) -> list[str]:
    candidatos: list[str] = []
    try:
        r = ejecutar(["py", "-0p"], capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        candidatos += parsear_py_list(r.stdout)
    except Exception:
        pass
    if not candidatos:
        for alias in ("python", "python3"):
            candidatos.append(alias)
    return candidatos


def huella_requisitos(app: Path, version: tuple) -> str:
    h = hashlib.sha256()
    for nombre in ("requirements.txt", "constraints.txt"):
        h.update((app / nombre).read_bytes())
    h.update(repr(version).encode())
    return h.hexdigest()


def escribir_env(ruta: Path, clave: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(f"ANTHROPIC_API_KEY={clave.strip()}\n", encoding="utf-8")


def clave_con_forma_valida(clave: str) -> bool:
    c = clave.strip()
    return len(c) >= 20 and " " not in c and c.startswith("sk-")


# ------------------------------------------------------------------------ pasos ----
def python_del_entorno(datos: Path) -> Path:
    return datos / "entorno" / "Scripts" / "python.exe"


def pythonw_del_entorno(datos: Path) -> Path:
    return datos / "entorno" / "Scripts" / "pythonw.exe"


def ejecutar_visible_en_log(cmd: list[str], log: Path) -> int:
    # PIP_REQUIRE_VIRTUALENV: red de seguridad: pip se niega a instalar si por error no fuera el entorno propio.
    entorno = dict(os.environ, PIP_REQUIRE_VIRTUALENV="1", PIP_USER="0", PYTHONNOUSERSITE="1")
    with open(log, "a", encoding="utf-8", errors="replace") as f:
        f.write("\n$ " + " ".join(cmd) + "\n")
        f.flush()
        return subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, creationflags=SIN_VENTANA, env=entorno)


def preparar_entorno(datos: Path, log: Path, avisar=lambda t: None) -> Path:
    """Crea el entorno y instala dependencias si hace falta. Devuelve el python del entorno."""
    avisar("Buscando Python en este equipo…")
    elegido = elegir_python(listar_candidatos())
    if not elegido:
        raise ErrorLanzador(
            "No se encontró Python 3.10 o superior en este equipo.\n\n"
            "Instálelo desde https://www.python.org/downloads/ (marque la opción «py launcher» y "
            "«Add python.exe to PATH») y vuelva a abrir FacturExtract.")
    ruta_python, version = elegido
    py_entorno = python_del_entorno(datos)
    marca = datos / "entorno" / ".huella"
    huella = huella_requisitos(APP, version)
    if py_entorno.exists() and marca.exists() and marca.read_text().strip() == huella:
        return py_entorno
    if not py_entorno.exists():
        avisar(f"Creando el entorno de trabajo (Python {version[0]}.{version[1]})…")
        if ejecutar_visible_en_log([ruta_python, "-m", "venv", str(datos / "entorno")], log) != 0:
            raise ErrorLanzador("No se pudo crear el entorno de trabajo. Revise que haya espacio en disco.")
    avisar("Instalando componentes (la primera vez puede tardar varios minutos)…")
    base = [str(py_entorno), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(APP / "requirements.txt"),
            "-c", str(APP / "constraints.txt")]
    if ejecutar_visible_en_log(base + ["--use-feature=truststore"], log) != 0:   # certificados de Windows
        if ejecutar_visible_en_log(base, log) != 0:
            raise ErrorLanzador(
                "No se pudieron descargar los componentes. Revise la conexión a internet "
                "(o consulte con quien administra la red) y vuelva a intentar.")
    marca.write_text(huella)
    return py_entorno


def pedir_clave(env_ruta: Path) -> bool:
    """Ventana simple para la clave de API. Devuelve True si quedó guardada. Nunca muestra ni registra la clave."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    resultado = {"ok": False}
    raiz = tk.Tk()
    raiz.title("FacturExtract – clave de acceso")
    raiz.resizable(False, False)
    f = ttk.Frame(raiz, padding=16)
    f.pack()
    ttk.Label(f, text="Pegue aquí la clave de API de Anthropic que le entregaron.\n"
                      "Se guarda solo en este equipo, en la carpeta de datos.", justify="left").pack(anchor="w")
    var = tk.StringVar()
    ent = ttk.Entry(f, textvariable=var, show="•", width=60)
    ent.pack(pady=10)
    ent.focus_set()

    def guardar() -> None:
        if not clave_con_forma_valida(var.get()):
            messagebox.showwarning("Clave", "Esa clave no parece válida (debe comenzar con «sk-»). Revise y vuelva a pegarla.",
                                   parent=raiz)
            return
        escribir_env(env_ruta, var.get())
        resultado["ok"] = True
        raiz.destroy()

    b = ttk.Frame(f)
    b.pack(anchor="e")
    ttk.Button(b, text="Guardar", command=guardar).pack(side="left", padx=4)
    ttk.Button(b, text="Cancelar", command=raiz.destroy).pack(side="left")
    raiz.bind("<Return>", lambda e: guardar())
    raiz.mainloop()
    return resultado["ok"]


def hay_clave(env_ruta: Path) -> bool:
    """¿Existe una clave configurada? (sin leer su valor más allá de comprobar que no esté vacía)."""
    # Solo cuenta la clave del .env propio: una variable global de Windows podría ser de otro programa.
    try:
        for linea in env_ruta.read_text(encoding="utf-8").splitlines():
            if linea.startswith("ANTHROPIC_API_KEY=") and linea.split("=", 1)[1].strip():
                return True
    except OSError:
        pass
    return False


def crear_acceso_directo(datos: Path) -> None:
    """Acceso directo en el Escritorio (mejor esfuerzo, una sola vez)."""
    marca = datos / ".acceso_directo"
    destino = APP / "Iniciar FacturExtract.bat"
    try:
        if marca.exists() and marca.read_text().strip() == str(destino):
            return                       # ya existe y apunta a esta carpeta; si la carpeta se movió, se recrea
    except OSError:
        pass
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
          "[IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'FacturExtract v2.lnk'));"
          f"$s.TargetPath='{str(destino).replace(chr(39), chr(39) * 2)}';"
          f"$s.WorkingDirectory='{str(APP).replace(chr(39), chr(39) * 2)}';$s.WindowStyle=7;$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=30, creationflags=SIN_VENTANA,
                       capture_output=True)
        marca.write_text(str(destino))
    except Exception:
        pass


def abrir_aplicacion(datos: Path) -> None:
    pyw = pythonw_del_entorno(datos)
    subprocess.Popen([str(pyw), str(APP / "FacturExtract.pyw")], cwd=str(APP),
                     creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED_PROCESS | NEW_PROCESS_GROUP


# ------------------------------------------------------------------------- main ----
def main(argv: list[str]) -> int:
    from facturextract import config  # solo biblioteca estándar a nivel de módulo

    solo_preparar = "--solo-preparar" in argv
    r = config.rutas().crear()
    log = r.logs / "instalacion.log"

    # Si la aplicación ya está abierta, solo se le pide mostrarse (no se instala nada).
    if not solo_preparar:
        try:
            from facturextract.residente import InstanciaUnica

            otra = InstanciaUnica(r.datos)
            if otra.hay_otra():
                otra.avisar_a_la_otra()
                return 0
        except Exception:
            pass

    estado: dict = {"texto": "Preparando FacturExtract…", "error": None, "py": None, "listo": False}

    def trabajo() -> None:
        try:
            estado["py"] = preparar_entorno(r.datos, log, lambda t: estado.update(texto=t))
        except ErrorLanzador as e:
            estado["error"] = str(e)
        except Exception:
            with open(log, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            estado["error"] = ("Ocurrió un problema inesperado al preparar el programa. "
                               f"Envíe el archivo {log} a quien le dio el programa.")
        estado["listo"] = True

    hilo = threading.Thread(target=trabajo, daemon=True)
    hilo.start()
    try:
        import tkinter as tk
        from tkinter import ttk

        ventana = tk.Tk()
        ventana.title("FacturExtract")
        ventana.resizable(False, False)
        f = ttk.Frame(ventana, padding=20)
        f.pack()
        etiqueta = ttk.Label(f, text=estado["texto"], width=60)
        etiqueta.pack()
        barra = ttk.Progressbar(f, mode="indeterminate", length=380)
        barra.pack(pady=10)
        barra.start(12)

        def vigilar() -> None:
            etiqueta.config(text=estado["texto"])
            if estado["listo"]:
                ventana.destroy()
            else:
                ventana.after(200, vigilar)
        vigilar()
        ventana.mainloop()
    except Exception:
        hilo.join()
    hilo.join()

    if estado["error"]:
        try:
            from tkinter import messagebox
            messagebox.showerror("FacturExtract", estado["error"])
        except Exception:
            print(estado["error"])
        return 1
    if solo_preparar:
        return 0

    if not hay_clave(config.archivo_env(r)):
        if not pedir_clave(r.env_datos):
            return 1
    crear_acceso_directo(r.datos)
    abrir_aplicacion(r.datos)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
