"""Cambio 3: inicio automático con Windows.

Las pruebas del registro usan nombres ÚNICOS de prueba (nunca `FacturExtractV2` ni `FacturExtract`) y un Escritorio
temporal, así que no tocan el inicio real de este PC. Los arranques «como Windows» son procesos reales.
"""
import os
import shutil
import subprocess
import sys
import time
import uuid
import winreg
from pathlib import Path
from tkinter import messagebox

import pytest

import lanzador
from facturextract import app, arranque, config, residente
from facturextract.residente import InicioWindows, comando_inicio, creada_por_la_v2, interprete_sin_consola
from tests.test_convivencia import RAIZ, _entorno, _esperar, _matar
from tests.ui_utils import abrir_app, bombear, clic, visible


def nombre_unico(prefijo="Prueba"):
    return f"FacturExtractV2{prefijo}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


def valor(nombre):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, residente.RUTA_RUN) as k:
            return winreg.QueryValueEx(k, nombre)[0]
    except OSError:
        return None


def poner(nombre, comando):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, residente.RUTA_RUN, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, nombre, 0, winreg.REG_SZ, comando)


def quitar(nombre):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, residente.RUTA_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, nombre)
    except OSError:
        pass


def instalacion_v2(carpeta: Path) -> Path:
    """Una carpeta que parece una instalación de la v2 (con entorno propio en <datos>)."""
    (carpeta / "facturextract").mkdir(parents=True)
    (carpeta / "facturextract" / "residente.py").write_text("")
    (carpeta / "lanzador.py").write_text("")
    (carpeta / "FacturExtract.pyw").write_text("")
    return carpeta


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """Una InicioWindows de prueba (nombres únicos, Escritorio temporal) y limpieza garantizada del registro."""
    escritorio = tmp_path / "Escritorio"
    escritorio.mkdir()
    monkeypatch.setattr(residente, "carpeta_escritorio", lambda: escritorio)
    datos = tmp_path / "datos"
    (datos / "entorno" / "Scripts").mkdir(parents=True)
    (datos / "entorno" / "Scripts" / "pythonw.exe").write_text("")
    raiz = instalacion_v2(tmp_path / "Mi aplicación")
    nombre, legado = nombre_unico(), nombre_unico("Legado")
    ajena = nombre_unico("Ajena")
    poner(ajena, r'"C:\Otro agente\agente.exe" --startup')
    ini = InicioWindows(raiz, datos, nombre=nombre, legado=(legado,), acceso_directo=f"{nombre}.lnk")
    ini.escritorio, ini.ajena, ini.legado_nombre, ini.tmp = escritorio, ajena, legado, tmp_path
    yield ini
    for n in (nombre, legado, ajena):
        quitar(n)


# ================================================================================== el comando
def test_el_comando_va_entre_comillas_con_espacios_tildes_y_otra_unidad():
    c = comando_inicio(Path(r"D:\Facturación Ñandú\Mi aplicación v2"))
    assert c.startswith('"') and c.endswith(r'FacturExtract.pyw" --oculto')
    assert c.count('"') == 4                                           # intérprete y script, ambos entre comillas
    assert r'"D:\Facturación Ñandú\Mi aplicación v2\FacturExtract.pyw"' in c


def test_el_interprete_es_el_del_entorno_propio_y_sin_consola(tmp_path):
    datos = tmp_path / "datos"
    (datos / "entorno" / "Scripts").mkdir(parents=True)
    pyw = datos / "entorno" / "Scripts" / "pythonw.exe"
    pyw.write_text("")
    assert interprete_sin_consola(datos) == str(pyw)                   # el del lanzador, aunque se ejecute con otro
    assert "pythonw.exe" in interprete_sin_consola(None).lower() or Path(sys.executable).name.lower() != "python.exe"
    assert not interprete_sin_consola(datos).lower().endswith("\\python.exe")


def test_python_exe_en_mayusculas_tambien_pasa_a_pythonw(tmp_path, monkeypatch):
    carpeta = tmp_path / "Scripts"
    carpeta.mkdir()
    (carpeta / "pythonw.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(carpeta / "PYTHON.EXE"))
    assert Path(interprete_sin_consola(None)).name.lower() == "pythonw.exe"      # antes: solo reemplazaba «python.exe»


# =============================================================== una sola entrada, sin duplicados
def test_registrar_muchas_veces_deja_una_sola_entrada(reg):
    for _ in range(4):
        assert reg.activar()
    nombres = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, residente.RUTA_RUN) as k:
        i = 0
        while True:
            try:
                nombres.append(winreg.EnumValue(k, i)[0])
                i += 1
            except OSError:
                break
    assert nombres.count(reg.nombre) == 1
    assert valor(reg.nombre) == reg.comando_deseado() and reg.activo()
    assert valor(reg.ajena) == r'"C:\Otro agente\agente.exe" --startup'      # la ajena, intacta


# =============================================================== carpeta movida o actualizada
def test_reparar_no_crea_la_entrada_por_su_cuenta(reg):
    assert reg.reparar() == "no_registrado" and valor(reg.nombre) is None


def test_reparar_corrige_una_entrada_que_apunta_a_la_ruta_anterior(reg):
    reg.activar()
    poner(reg.nombre, r'"C:\Vieja\entorno\Scripts\pythonw.exe" "C:\Vieja\FacturExtract.pyw" --oculto')
    assert reg.reparar() == "reparado" and valor(reg.nombre) == reg.comando_deseado()
    assert reg.reparar() == "al_dia"                                    # y queda estable (no reescribe cada vez)


def test_al_mover_la_carpeta_de_la_aplicacion_se_actualiza_la_ruta(reg):
    reg.activar()
    nueva = instalacion_v2(reg.tmp / "Nueva ubicación")
    movido = InicioWindows(nueva, reg.datos, nombre=reg.nombre, legado=(reg.legado_nombre,))
    assert movido.reparar() == "reparado"
    assert str(nueva / "FacturExtract.pyw") in valor(reg.nombre)


# =============================================================== entrada antigua: solo si es de la v2
def test_creada_por_la_v2_exige_el_formato_exacto(tmp_path):
    ok = r'"C:\x\entorno\Scripts\pythonw.exe" "C:\x\FacturExtract.pyw" --oculto'
    assert creada_por_la_v2(ok)                                         # carpeta inexistente: huérfana, es nuestra
    assert creada_por_la_v2(ok.upper().replace("--OCULTO", "--oculto"))
    assert not creada_por_la_v2(None) and not creada_por_la_v2("")
    assert not creada_por_la_v2(r'"C:\Otro\agente.exe" --startup')
    assert not creada_por_la_v2(r'"C:\x\pythonw.exe" "C:\x\FacturExtract.pyw"')            # falta --oculto
    assert not creada_por_la_v2(r'"C:\x\pythonw.exe" "C:\x\otro.pyw" --oculto')
    assert not creada_por_la_v2(r'C:\x\pythonw.exe C:\x\FacturExtract.pyw --oculto')       # sin comillas: no es el nuestro
    assert not creada_por_la_v2(r'"C:\x\python.exe" "C:\x\FacturExtract.pyw" --oculto')
    ajena = tmp_path / "Otra"
    ajena.mkdir()
    (ajena / "FacturExtract.pyw").write_text("")                         # existe pero NO es instalación de la v2
    assert not creada_por_la_v2(f'"C:\\x\\pythonw.exe" "{ajena / "FacturExtract.pyw"}" --oculto')
    v2 = instalacion_v2(tmp_path / "Propia")
    assert creada_por_la_v2(f'"C:\\x\\pythonw.exe" "{v2 / "FacturExtract.pyw"}" --oculto')


def test_retira_la_entrada_antigua_de_la_v2_y_nunca_una_ajena(reg):
    antigua = f'"{reg.datos}\\entorno\\Scripts\\pythonw.exe" "{reg.raiz}\\FacturExtract.pyw" --oculto'
    poner(reg.legado_nombre, antigua)
    assert reg.limpiar_legado() == [reg.legado_nombre] and valor(reg.legado_nombre) is None
    # con el MISMO nombre antiguo pero un comando ajeno: no se toca
    poner(reg.legado_nombre, r'"C:\OtroPrograma\x.exe" /silent')
    assert reg.limpiar_legado() == [] and valor(reg.legado_nombre) == r'"C:\OtroPrograma\x.exe" /silent'
    # ni siquiera si menciona FacturExtract.pyw pero no tiene el formato de la v2
    poner(reg.legado_nombre, r'cmd /c start FacturExtract.pyw')
    assert reg.limpiar_legado() == [] and valor(reg.legado_nombre) is not None


def test_con_solo_la_entrada_antigua_se_migra_a_la_nueva_para_no_perder_el_inicio(reg):
    poner(reg.legado_nombre, f'"C:\\x\\entorno\\Scripts\\pythonw.exe" "C:\\x\\FacturExtract.pyw" --oculto')
    assert reg.reparar() == "reparado"
    assert valor(reg.legado_nombre) is None and valor(reg.nombre) == reg.comando_deseado()


def test_activar_retira_la_antigua_para_que_no_arranquen_dos(reg):
    poner(reg.legado_nombre, r'"C:\x\pythonw.exe" "C:\x\FacturExtract.pyw" --oculto')
    reg.activar()
    assert valor(reg.legado_nombre) is None and valor(reg.nombre) is not None


# ====================================================================== desactivar sin dejar restos
def test_desactivar_quita_lo_propio_y_deja_lo_ajeno(reg):
    reg.activar()
    poner(reg.legado_nombre, r'"C:\x\pythonw.exe" "C:\x\FacturExtract.pyw" --oculto')
    assert reg.desactivar() is True
    assert valor(reg.nombre) is None and valor(reg.legado_nombre) is None and not reg.activo()
    assert valor(reg.ajena) is not None
    assert reg.desactivar() is True                                      # idempotente


def test_quitar_restos_borra_entrada_y_acceso_directo_pero_no_datos(reg):
    reg.activar()
    lnk = reg.escritorio / reg.acceso_directo
    lnk.write_text("x")
    otro = reg.escritorio / "Otro programa.lnk"
    otro.write_text("x")
    (reg.datos / "facturas.db").write_text("datos del usuario")
    quitado = reg.quitar_restos()
    assert any("entrada" in q for q in quitado) and any(reg.acceso_directo in q for q in quitado)
    assert valor(reg.nombre) is None and not lnk.exists()
    assert otro.exists() and (reg.datos / "facturas.db").read_text() == "datos del usuario"
    assert reg.quitar_restos() == []                                     # y de nuevo: no hay nada que quitar


def test_quitar_inicio_por_linea_de_comandos(reg, tmp_path, monkeypatch):
    reg.activar()
    (reg.escritorio / reg.acceso_directo).write_text("x")
    monkeypatch.setenv("FACTUREXTRACT_DATOS", str(tmp_path / "datos_main"))
    monkeypatch.setattr(residente, "InicioWindows", lambda raiz, datos: reg)
    assert app.main(["--quitar-inicio"]) == 0
    assert valor(reg.nombre) is None and not (reg.escritorio / reg.acceso_directo).exists()
    log = (tmp_path / "datos_main" / "logs" / "arranque.log").read_text(encoding="utf-8")
    assert "quitar inicio" in log and "entrada de inicio" in log


# ======================================================================= casilla en la aplicación
def test_la_casilla_iniciar_con_windows_activa_y_desactiva(r, repo, reloj, reg):
    s, v = abrir_app(r, repo, reloj, [], inicio=reg)
    try:
        assert visible(v.chk_inicio) and v.var_inicio.get() is False and valor(reg.nombre) is None
        clic(v.chk_inicio)
        assert v.var_inicio.get() is True and valor(reg.nombre) == reg.comando_deseado()
        assert '"' in valor(reg.nombre) and valor(reg.nombre).endswith("--oculto")
        clic(v.chk_inicio)
        assert v.var_inicio.get() is False and valor(reg.nombre) is None
        log = (r.logs / "arranque.log").read_text(encoding="utf-8")
        assert "accion=activar" in log and "accion=desactivar" in log
    finally:
        s.detener()
        v.destroy()


def test_la_casilla_vuelve_a_su_estado_si_el_registro_falla(r, repo, reloj, reg, monkeypatch):
    avisos = []
    monkeypatch.setattr(messagebox, "showwarning", lambda t, m, **k: avisos.append(m))
    monkeypatch.setattr(reg, "activar", lambda: False)
    s, v = abrir_app(r, repo, reloj, [], inicio=reg)
    try:
        clic(v.chk_inicio)
        assert v.var_inicio.get() is False and valor(reg.nombre) is None
        assert any("No se pudo cambiar" in a for a in avisos)
    finally:
        s.detener()
        v.destroy()


def test_el_menu_de_la_bandeja_alterna_el_inicio(r, repo, reloj, reg):
    s, v = abrir_app(r, repo, reloj, [], inicio=reg)
    try:
        v.pedir("inicio")
        bombear(v, s, 2)
        assert valor(reg.nombre) == reg.comando_deseado() and v.var_inicio.get() is True
        v.pedir("inicio")
        bombear(v, s, 2)
        assert valor(reg.nombre) is None and v.var_inicio.get() is False
    finally:
        s.detener()
        v.destroy()


def test_el_menu_de_la_bandeja_ofrece_la_casilla_marcada_segun_el_estado():
    estado = {"activo": False}
    b = residente.Bandeja(lambda a: None, inicio_activo=lambda: estado["activo"])
    item = [i for i in b.icono.menu.items if str(i.text) == "Iniciar con Windows"][0]
    assert item.checked is False
    estado["activo"] = True
    assert item.checked is True
    sin = residente.Bandeja(lambda a: None)
    assert not [i for i in sin.icono.menu.items if str(i.text) == "Iniciar con Windows"]


def test_la_primera_ejecucion_ofrece_el_inicio_y_no_vuelve_a_preguntar(r, repo, reloj, reg, monkeypatch):
    preguntas = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: preguntas.append(a) or True)
    monkeypatch.setattr(config, "modo_desarrollo", lambda: False)
    s, v = abrir_app(r, repo, reloj, [])
    try:
        app.ofrecer_inicio_con_windows(v, repo, reg)
        assert len(preguntas) == 1 and valor(reg.nombre) == reg.comando_deseado()
        app.ofrecer_inicio_con_windows(v, repo, reg)
        assert len(preguntas) == 1
        assert "primera ejecución" in (r.logs / "arranque.log").read_text(encoding="utf-8")
    finally:
        s.detener()
        v.destroy()


def test_en_desarrollo_nunca_se_ofrece_el_inicio(r, repo, reloj, reg, monkeypatch):
    preguntas = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: preguntas.append(a) or True)
    monkeypatch.setattr(config, "modo_desarrollo", lambda: True)
    s, v = abrir_app(r, repo, reloj, [])
    try:
        app.ofrecer_inicio_con_windows(v, repo, reg)
        assert preguntas == [] and valor(reg.nombre) is None
    finally:
        s.detener()
        v.destroy()


# =============================================================================== registro de arranque
def test_el_registro_de_arranque_anota_hora_ruta_y_resultado(tmp_path):
    arranque.anotar("arranque", tmp_path, modo="cliente", **arranque.descripcion_proceso())
    arranque.anotar("ventana", tmp_path, estado="oculta en la bandeja")
    lineas = (tmp_path / "logs" / "arranque.log").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    assert lineas[0][4] == "-" and "| pid" in lineas[0] and "| arranque |" in lineas[0]
    assert "python=" in lineas[0] and "script=" in lineas[0] and "cwd=" in lineas[0] and "args=" in lineas[0]
    assert "estado=oculta en la bandeja" in lineas[1]


def test_el_registro_nunca_lanza_excepciones_ni_guarda_claves(tmp_path):
    archivo = tmp_path / "es_un_archivo"
    archivo.write_text("x")
    arranque.anotar("arranque", archivo)                                   # la «carpeta» es un archivo: no debe fallar
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        arranque.anotar_excepcion("ERROR", tmp_path)
    texto = (tmp_path / "logs" / "arranque.log").read_text(encoding="utf-8")
    assert "RuntimeError: boom" in texto and len(texto.strip().splitlines()) == 1    # traza en una sola línea
    assert "sk-" not in texto


def test_el_registro_no_crece_sin_limite(tmp_path):
    for i in range(6000):
        arranque.anotar("evento", tmp_path, n=i, relleno="x" * 60)
    ruta = tmp_path / "logs" / "arranque.log"
    assert ruta.stat().st_size < arranque.MAX_BYTES + 4096
    assert "n=5999" in ruta.read_text(encoding="utf-8")                     # lo reciente se conserva


def test_dependencias_al_dia_compara_con_la_huella_del_lanzador(tmp_path):
    app_dir, datos = tmp_path / "app", tmp_path / "datos"
    app_dir.mkdir()
    (app_dir / "requirements.txt").write_text("a==1\n")
    (app_dir / "constraints.txt").write_text("a==1\n")
    assert arranque.dependencias_al_dia(app_dir, datos) is None             # sin entorno: no se sabe
    (datos / "entorno").mkdir(parents=True)
    huella = lanzador.huella_requisitos(app_dir, tuple(sys.version_info[:2]))
    (datos / "entorno" / ".huella").write_text(huella)
    assert arranque.dependencias_al_dia(app_dir, datos) is True            # misma fórmula que el lanzador
    (app_dir / "requirements.txt").write_text("a==2\n")                     # «actualización»: cambian las versiones
    assert arranque.dependencias_al_dia(app_dir, datos) is False


# ============================================================================ acceso directo del lanzador
def test_el_acceso_directo_se_recrea_si_la_carpeta_cambio_de_lugar(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(lanzador.subprocess, "run", lambda *a, **k: llamadas.append(a[0][-1]))
    datos = tmp_path / "datos"
    datos.mkdir()
    monkeypatch.setattr(lanzador, "APP", tmp_path / "App uno")
    lanzador.crear_acceso_directo(datos)
    lanzador.crear_acceso_directo(datos)
    assert len(llamadas) == 1                                               # misma carpeta: una sola vez
    monkeypatch.setattr(lanzador, "APP", tmp_path / "App dos")             # se movió o actualizó en otra ruta
    lanzador.crear_acceso_directo(datos)
    assert len(llamadas) == 2 and "App dos" in llamadas[1]
    monkeypatch.setattr(lanzador, "APP", tmp_path / "O'Brien")
    lanzador.crear_acceso_directo(datos)
    assert "O''Brien" in llamadas[2]                                        # el apóstrofe no rompe el comando


# ============================================================== arranques reales «como Windows»
def _pyw(datos, args=(), cwd=r"C:\Windows\System32", script=None, entorno=None):
    base = getattr(sys, "_base_executable", sys.executable)
    env = dict(entorno or _entorno())
    env["FACTUREXTRACT_DATOS"] = str(datos)
    env.pop("FACTUREXTRACT_FECHA_SIMULADA", None)
    return subprocess.Popen([base, str(script or RAIZ / "FacturExtract.pyw"), *args], env=env, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def log_de(datos, nombre):
    ruta = Path(datos) / "logs" / nombre
    return ruta.read_text(encoding="utf-8") if ruta.exists() else ""


def test_arranca_desde_otra_carpeta_de_trabajo_y_sin_red(tmp_path):
    """Como lo lanza Windows: carpeta de trabajo distinta, sin consola, y sin red (proxy muerto)."""
    datos = tmp_path / "datos"
    env = _entorno()
    env.update(HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", NO_PROXY="")
    p = _pyw(datos, ["--oculto"], entorno=env)
    try:
        assert _esperar(lambda: "ventana" in log_de(datos, "arranque.log")), "no arrancó: " + (
            p.stderr.read() if p.poll() is not None else log_de(datos, "arranque.log"))
        assert p.poll() is None
        texto = log_de(datos, "arranque.log")
        assert "| arranque |" in texto and "cwd=C:\\Windows\\System32" in texto and "args=--oculto" in texto
        assert str(datos) in texto and "script=" in texto and "FacturExtract.pyw" in texto
        assert "| servicio |" in texto and "clave_api=" in texto and "estado=oculta en la bandeja" in texto
        assert (datos / "facturas.db").exists() and (datos / "instancia.lock").exists()   # datos junto a la carpeta de datos
    finally:
        _matar(p)


def test_un_arranque_oculto_con_otra_copia_abierta_no_hace_aparecer_su_ventana(tmp_path):
    datos = tmp_path / "datos"
    a = _pyw(datos, ["--oculto"])
    b = c = None
    try:
        assert _esperar(lambda: "estado=oculta" in log_de(datos, "arranque.log"))
        b = _pyw(datos, ["--oculto"])                                       # p. ej. la entrada antigua + la nueva
        assert _esperar(lambda: b.poll() is not None, 40) and b.returncode == 0
        time.sleep(1.5)
        assert not (datos / "instancia.mostrar").exists()                   # no se pidió mostrar nada
        assert "Comando de la bandeja: abrir" not in log_de(datos, "app.log")
        assert "arranque oculto, no se muestra nada" in log_de(datos, "arranque.log")
        assert a.poll() is None
        c = _pyw(datos, [])                                                 # abierta a mano (acceso directo): sí muestra
        assert _esperar(lambda: c.poll() is not None, 40) and c.returncode == 0
        assert _esperar(lambda: "Comando de la bandeja: abrir" in log_de(datos, "app.log"), 15)
        assert "se le pidió mostrar su ventana" in log_de(datos, "arranque.log")
    finally:
        for p in (c, b, a):
            _matar(p)


def test_un_arranque_roto_deja_constancia_en_el_registro(tmp_path):
    """Actualización a medias: falta un archivo. Antes: código 1 sin ningún rastro (pythonw no tiene consola)."""
    copia = tmp_path / "App rota"
    shutil.copytree(RAIZ / "facturextract", copia / "facturextract", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(RAIZ / "FacturExtract.pyw", copia / "FacturExtract.pyw")
    (copia / "facturextract" / "pago.py").unlink()
    datos = tmp_path / "datos"
    p = _pyw(datos, ["--oculto"], script=copia / "FacturExtract.pyw")
    assert _esperar(lambda: p.poll() is not None, 40)
    assert p.returncode == 1
    texto = log_de(datos, "arranque.log")
    assert "ERROR fatal" in texto and "ModuleNotFoundError" in texto and "pago" in texto
    assert "FacturExtract.pyw" in texto or "cwd=" in texto                  # dónde estaba, para diagnosticar a distancia


def test_el_arranque_registra_la_clave_como_presente_o_ausente_sin_mostrarla(tmp_path):
    datos = tmp_path / "datos"
    p = _pyw(datos, ["--oculto"])
    try:
        assert _esperar(lambda: "clave_api=" in log_de(datos, "arranque.log"))
        texto = log_de(datos, "arranque.log")
        assert "clave_api=presente" in texto or "clave_api=AUSENTE" in texto
        assert "sk-" not in texto
    finally:
        _matar(p)
