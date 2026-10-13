"""Convivencia con otros programas residentes del mismo PC (por ejemplo, otro agente Python en memoria).

Reglas: (1) instancia única con bloqueo propio en la carpeta de datos (sin puertos, nombres de proceso ni mutex
globales); (2) el lanzador solo instala en su entorno virtual; (3) ningún código cierra ni inspecciona procesos
ajenos; (4) nombres propios y únicos (inicio con Windows, ícono de bandeja, acceso directo).

Las pruebas con procesos reales lanzan intérpretes de Python propios y terminan SOLO los procesos que ellas crean.
"""
import os
import re
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import lanzador
from facturextract import config, residente
from facturextract.residente import InstanciaUnica

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = sorted((RAIZ / "facturextract").glob("*.py")) + [RAIZ / "lanzador.py", RAIZ / "FacturExtract.pyw"]
PUERTO_ANTIGUO = 49731          # el que usaba la versión anterior: ahora nadie debe depender de él


# =========================================================================== (1) bloqueo propio
def test_el_bloqueo_vive_en_la_carpeta_de_datos(tmp_path):
    a, b = InstanciaUnica(tmp_path), InstanciaUnica(tmp_path)
    assert a.tomar() and (tmp_path / "instancia.lock").exists()
    assert not b.tomar() and b.hay_otra() and not a.hay_otra()
    a.liberar()
    assert b.tomar()                                   # al cerrar la primera, se puede volver a abrir
    b.liberar()


def test_cada_carpeta_de_datos_tiene_su_propio_bloqueo(tmp_path):
    a, b = InstanciaUnica(tmp_path / "x"), InstanciaUnica(tmp_path / "y")
    assert a.tomar() and b.tomar()                     # dos carpetas distintas no se estorban
    a.liberar()
    b.liberar()


def test_no_usa_red_en_absoluto(tmp_path, monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("la instancia única no debe abrir sockets")
    monkeypatch.setattr(socket, "socket", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)
    a, b = InstanciaUnica(tmp_path), InstanciaUnica(tmp_path)
    assert a.tomar() and not b.tomar()
    b.avisar_a_la_otra()
    a.escuchar(lambda: None)
    a.liberar()
    codigo = (RAIZ / "facturextract" / "residente.py").read_text(encoding="utf-8")
    assert "import socket" not in codigo and "PUERTO" not in codigo and str(PUERTO_ANTIGUO) not in codigo


def test_el_aviso_para_mostrar_viaja_por_un_archivo_propio(tmp_path):
    a, b = InstanciaUnica(tmp_path), InstanciaUnica(tmp_path)
    pedidos = []
    assert a.tomar()
    a.escuchar(lambda: pedidos.append(1), cada=0.05)
    assert not b.tomar() and b.avisar_a_la_otra()
    for _ in range(80):
        if pedidos:
            break
        time.sleep(0.05)
    assert pedidos == [1] and not (tmp_path / "instancia.mostrar").exists()      # la primera lo vio y lo consumió
    a.liberar()


def test_un_pedido_viejo_de_una_sesion_anterior_no_cuenta(tmp_path):
    (tmp_path / "instancia.mostrar").write_text("mostrar")
    a = InstanciaUnica(tmp_path)
    pedidos = []
    assert a.tomar()
    a.escuchar(lambda: pedidos.append(1), cada=0.05)
    time.sleep(0.3)
    assert pedidos == []
    a.liberar()


def test_avisar_sin_carpeta_o_sin_otra_instancia_no_falla(tmp_path):
    assert InstanciaUnica(tmp_path / "no_existe").avisar_a_la_otra() in (True, False)


def test_el_bloqueo_se_libera_solo_si_el_proceso_muere(tmp_path):
    """Un cierre brusco no deja un bloqueo huérfano que impida volver a abrir la aplicación."""
    hijo = _python(f"""
        import sys, time
        from pathlib import Path
        from facturextract.residente import InstanciaUnica
        u = InstanciaUnica(Path(r'{tmp_path}'))
        assert u.tomar()
        print('listo', flush=True)
        time.sleep(120)
    """)
    try:
        assert hijo.stdout.readline().strip() == "listo"
        mia = InstanciaUnica(tmp_path)
        assert not mia.tomar()                          # mientras vive, está tomado
        hijo.kill()                                     # muere de golpe (como un corte de luz o un cierre forzado)
        hijo.wait(10)
        assert mia.tomar()                              # y queda libre
        mia.liberar()
    finally:
        _matar(hijo)


# ============================================================================== (2) lanzador
def test_el_lanzador_instala_solo_en_su_entorno_virtual(tmp_path, monkeypatch):
    datos = tmp_path / "datos"
    (datos / "entorno").mkdir(parents=True)
    ordenes = []

    def falso(cmd, log):
        ordenes.append(cmd)
        return 0
    monkeypatch.setattr(lanzador, "ejecutar_visible_en_log", falso)
    monkeypatch.setattr(lanzador, "listar_candidatos", lambda *a, **k: [r"C:\Python\GLOBAL\python.exe"])
    monkeypatch.setattr(lanzador, "elegir_python", lambda cands, *a, **k: (cands[0], (3, 13)))
    lanzador.preparar_entorno(datos, tmp_path / "log.txt")
    entorno = str(datos / "entorno")
    venv = [c for c in ordenes if "venv" in c]
    pip = [c for c in ordenes if "pip" in c]
    assert venv and pip
    assert all(c[:3] == [r"C:\Python\GLOBAL\python.exe", "-m", "venv"] and c[3] == entorno for c in venv)   # solo crea
    for c in pip:
        assert c[0].startswith(entorno), "pip debe ejecutarse con el Python del entorno propio"
        assert c[0] != r"C:\Python\GLOBAL\python.exe" and "install" in c and "--user" not in c
    assert not any("--user" in c or "uninstall" in c or "--target" in c for c in ordenes)


def test_pip_se_niega_a_instalar_fuera_de_un_entorno_virtual(tmp_path, monkeypatch):
    capturado = {}

    def fake_call(cmd, **kw):
        capturado.update(kw.get("env", {}))
        return 0
    monkeypatch.setattr(lanzador.subprocess, "call", fake_call)
    lanzador.ejecutar_visible_en_log(["x"], tmp_path / "l.txt")
    assert capturado["PIP_REQUIRE_VIRTUALENV"] == "1" and capturado["PIP_USER"] == "0"


def test_el_lanzador_ignora_la_clave_de_otro_programa(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-otro-programa")
    env = tmp_path / ".env"
    assert not lanzador.hay_clave(env)                  # una variable global NO cuenta como clave propia
    lanzador.escribir_env(env, "sk-" + "p" * 30)
    assert lanzador.hay_clave(env)


def test_la_aplicacion_usa_su_propia_clave_y_no_la_heredada(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-otro-programa")
    monkeypatch.setattr(config, "modo_desarrollo", lambda: False)
    r = config.rutas(tmp_path / "d").crear()
    r.env_datos.write_text("ANTHROPIC_API_KEY=clave-propia\n", encoding="utf-8")
    assert config.cargar_env(r) is True
    assert os.environ["ANTHROPIC_API_KEY"] == "clave-propia"


# ================================================================ (3) no toca procesos ajenos
PROHIBIDO = [r"\bpsutil\b", r"taskkill", r"\bos\.kill\b", r"os\.killpg", r"Stop-Process", r"\btasklist\b", r"\bwmic\b",
             r"Get-Process", r"Get-CimInstance", r"process_iter", r"EnumWindows", r"FindWindow", r"CreateMutex",
             r"EnumProcesses", r"OpenProcess", r"TerminateProcess", r"\.terminate\(", r"\.kill\(", r"pkill",
             r"killall", r"CreateToolhelp32Snapshot", r"\bsignal\.", r"import socket"]


@pytest.mark.parametrize("fuente", FUENTES, ids=lambda p: p.name)
def test_ningun_codigo_cierra_ni_inspecciona_procesos_ajenos(fuente):
    texto = fuente.read_text(encoding="utf-8")
    for patron in PROHIBIDO:
        assert not re.search(patron, texto), f"{fuente.name} usa {patron}"


def test_lo_unico_que_se_lanza_es_la_propia_aplicacion_o_herramientas_inofensivas():
    usos = []
    for f in FUENTES:
        for n, linea in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"subprocess\.(run|call|Popen)\(", linea):
                usos.append(f"{f.name}:{n}")
    assert set(u.split(":")[0] for u in usos) == {"lanzador.py"}            # solo el lanzador, nadie más


# ====================================================================== (4) nombres propios y únicos
def test_nombres_propios_y_unicos():
    assert residente.NOMBRE_INICIO == "FacturExtractV2" and residente.NOMBRE_INICIO != "FacturExtract"
    assert residente.NOMBRE_ICONO == "FacturExtractV2.Bandeja"
    assert residente.NOMBRE_ACCESO_DIRECTO == "FacturExtract v2.lnk"
    assert "FacturExtract v2.lnk" in (RAIZ / "lanzador.py").read_text(encoding="utf-8")
    assert "'FacturExtract.lnk'" not in (RAIZ / "lanzador.py").read_text(encoding="utf-8")   # no pisa el de la v1
    assert "pystray.Icon(NOMBRE_ICONO" in (RAIZ / "facturextract" / "residente.py").read_text(encoding="utf-8")


def test_el_inicio_con_windows_solo_toca_su_propia_entrada():
    import winreg
    ajena = "OtroAgentePrueba_" + str(os.getpid())
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, residente.RUTA_RUN, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, ajena, 0, winreg.REG_SZ, r'"C:\otro\agente.exe"')
    propia = "FacturExtractV2Prueba_" + str(os.getpid())
    try:
        assert residente.activar_inicio(r'"C:\x\pythonw.exe" "C:\x\FacturExtract.pyw" --oculto', propia)
        assert residente.desactivar_inicio(propia) and not residente.inicio_activo(propia)
        assert residente.inicio_activo(ajena)                              # la entrada ajena quedó intacta
    finally:
        residente.desactivar_inicio(ajena)
        residente.desactivar_inicio(propia)


# ======================================================== procesos reales: otro agente residente
def _entorno():
    sp = str(Path(sys.prefix) / "Lib" / "site-packages")
    return dict(os.environ, PYTHONPATH=os.pathsep.join([sp, str(RAIZ)]), PYTHONIOENCODING="utf-8")


def _python(codigo, entorno=None, **kw):
    """Intérprete real (no el lanzador intermedio del entorno virtual): se puede terminar exactamente."""
    base = getattr(sys, "_base_executable", sys.executable)
    return subprocess.Popen([base, "-c", textwrap.dedent(codigo)], env=entorno or _entorno(), cwd=str(RAIZ),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def _matar(p):
    if p is not None and p.poll() is None:
        p.kill()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            pass


AGENTE_AJENO = f"""
    import socket, time, sys
    s = socket.socket()
    try:                                  # ocupa el puerto que usaba la versión anterior, como haría otro programa
        s.bind(('127.0.0.1', {PUERTO_ANTIGUO})); s.listen(5)
    except OSError:
        pass
    print('listo', flush=True)
    while True:
        time.sleep(0.2)
"""


def _lanzar_app(datos, oculto=True):
    env = _entorno()
    env["FACTUREXTRACT_DATOS"] = str(datos)
    env.pop("FACTUREXTRACT_FECHA_SIMULADA", None)
    args = "['--oculto']" if oculto else "[]"
    return _python(f"""
        import sys
        from facturextract.app import main
        sys.exit(main({args}))
    """, env)


def _esperar(condicion, segundos=40):
    fin = time.time() + segundos
    while time.time() < fin:
        if condicion():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def agente_ajeno():
    p = _python(AGENTE_AJENO)
    assert p.stdout.readline().strip() == "listo"
    yield p
    _matar(p)


def _puerto_responde():
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_ANTIGUO), timeout=1):
            return True
    except OSError:
        return False


def test_arranca_mientras_otro_agente_python_corre_en_segundo_plano(tmp_path, agente_ajeno):
    datos = tmp_path / "datos"
    app = _lanzar_app(datos)
    try:
        assert _esperar(lambda: (datos / "facturas.db").exists() and (datos / "instancia.lock").exists()), \
            "FacturExtract no arrancó: " + (app.stderr.read() if app.poll() is not None else "")
        time.sleep(2)
        assert app.poll() is None                                   # FacturExtract sigue vivo y sirviendo
        assert agente_ajeno.poll() is None                          # el otro agente no fue afectado
        assert _puerto_responde()                                   # y conserva su puerto: no hubo choque
        assert not InstanciaUnica(datos).tomar()                   # el bloqueo propio está tomado por la app
        assert (datos / "checkpoints.db").exists()
    finally:
        _matar(app)
    assert agente_ajeno.poll() is None                              # al cerrar FacturExtract, el otro sigue igual
    mia = InstanciaUnica(datos)
    assert mia.tomar()                                              # y su bloqueo se liberó al morir
    mia.liberar()


def test_la_segunda_instancia_se_detiene_sin_afectar_a_ninguna_otra(tmp_path, agente_ajeno):
    datos = tmp_path / "datos"
    primera = _lanzar_app(datos)
    ajena_otra_carpeta = None
    segunda = None
    try:
        assert _esperar(lambda: (datos / "instancia.lock").exists() and (datos / "facturas.db").exists()), \
            "la primera instancia no arrancó"
        time.sleep(2)
        assert primera.poll() is None
        segunda = _lanzar_app(datos)                                # misma carpeta de datos: debe detenerse sola
        assert _esperar(lambda: segunda.poll() is not None, 40), "la segunda instancia no terminó"
        assert segunda.returncode == 0                              # salió limpia, sin error
        assert primera.poll() is None, "la segunda instancia afectó a la primera"
        assert agente_ajeno.poll() is None, "la segunda instancia afectó a otro programa"
        assert _puerto_responde()
        # la primera recibió el aviso «mostrar» (y lo consumió): se comunican solo por la carpeta de datos
        assert _esperar(lambda: not (datos / "instancia.mostrar").exists(), 10)
        # una instancia con OTRA carpeta de datos es independiente: no se detiene ni molesta
        ajena_otra_carpeta = _lanzar_app(tmp_path / "otros_datos")
        assert _esperar(lambda: (tmp_path / "otros_datos" / "facturas.db").exists(), 40)
        time.sleep(2)
        assert ajena_otra_carpeta.poll() is None and primera.poll() is None and agente_ajeno.poll() is None
    finally:
        for p in (segunda, ajena_otra_carpeta, primera):
            _matar(p)
    assert agente_ajeno.poll() is None
