"""Persistencia en facturas.db (fuente de verdad del negocio).

Una conexión por operación (seguro entre hilos); las escrituras se serializan con un candado
y cada una es una sola transacción.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .modelo import METODOS_PAGO, TIPOS_CON_ALERTA, DatosInvalidos, parse_fecha
from .pago import alerta_por_defecto, por_defecto

ESTADOS_FACTURA = ("registrada", "extrayendo", "en_revision", "por_confirmar", "guardada", "rechazada",
                   "duplicada", "error_reintentable")
VERSION_ESQUEMA = 2   # 2: metodo_pago separado (reemplaza con_cheque); estado_pago = pendiente | pagada | no aplica

ESQUEMA = """
CREATE TABLE IF NOT EXISTS facturas (
    id TEXT PRIMARY KEY,
    estado TEXT NOT NULL,
    creada_en TEXT NOT NULL,
    imagenes TEXT NOT NULL DEFAULT '[]',
    extraido TEXT,
    hallazgos TEXT,
    mensaje TEXT,
    tipo_documento TEXT, rut_emisor TEXT, razon_social_emisor TEXT,
    rut_receptor TEXT, razon_social_receptor TEXT, folio TEXT,
    fecha_emision TEXT, fecha_vencimiento TEXT, medio_pago TEXT,
    monto_neto INTEGER, monto_exento INTEGER, iva INTEGER, otros_impuestos INTEGER, nombre_otros_impuestos TEXT, total INTEGER,
    observaciones TEXT,
    aprobada_manual INTEGER NOT NULL DEFAULT 0,
    estado_validacion TEXT,
    mes_emision TEXT,
    estado_pago TEXT,
    metodo_pago TEXT,                 -- contado | transferencia | cheque (solo si estado_pago = pagada)
    fecha_pago TEXT,
    nro_documento TEXT                -- solo cheque
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_factura_identidad
    ON facturas (rut_emisor, folio, tipo_documento) WHERE estado = 'guardada';
CREATE TABLE IF NOT EXISTS lineas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factura_id TEXT NOT NULL REFERENCES facturas(id),
    n INTEGER NOT NULL,
    cantidad REAL, detalle TEXT, precio_unitario REAL, descuento INTEGER, total_linea INTEGER
);
CREATE TABLE IF NOT EXISTS seguimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factura_id TEXT NOT NULL UNIQUE REFERENCES facturas(id),
    estado TEXT NOT NULL,            -- pendiente | esperando_decision | pagada
    proxima_alerta TEXT NOT NULL,
    hilo TEXT
);
CREATE TABLE IF NOT EXISTS excel_meses (
    mes TEXT PRIMARY KEY,
    desactualizado INTEGER NOT NULL DEFAULT 0,
    actualizado_en TEXT
);
CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS correcciones_pago (   -- registro mínimo de cada corrección hecha desde la pestaña Facturas
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factura_id TEXT NOT NULL REFERENCES facturas(id),
    cuando TEXT NOT NULL,
    anterior TEXT NOT NULL,                      -- JSON: estado_pago, metodo_pago, nro_documento, fecha_pago, fecha_vencimiento
    nuevo TEXT NOT NULL
);
"""


class Duplicada(Exception):
    """El documento ya existe (mismo RUT emisor, folio y tipo)."""


class DecisionInvalida(ValueError):
    """Datos de pago/decisión inválidos; el mensaje va al usuario."""


def estado_validacion_texto(hallazgos: list[dict], aprobada_manual: bool) -> str:
    adv = [h["mensaje"] for h in hallazgos if h.get("gravedad") == "advertencia"]
    blo = [h["mensaje"] for h in hallazgos if h.get("gravedad") != "advertencia"]
    if aprobada_manual:
        base = "Aprobada manualmente"
        detalle = blo + adv
    elif adv:
        base, detalle = "Válida con advertencias", adv
    else:
        return "Válida"
    return base + (": " + " | ".join(detalle) if detalle else "")


class Repo:
    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._candado = threading.RLock()
        with self._tx() as con:
            self._migrar(con)
            con.executescript(ESQUEMA)
            self._agregar_columnas(con)
            con.execute(f"PRAGMA user_version={VERSION_ESQUEMA}")

    @staticmethod
    def _agregar_columnas(con: sqlite3.Connection) -> None:
        """Columnas añadidas después de la primera versión (idempotente, sin tocar los datos existentes)."""
        cols = {r[1] for r in con.execute("PRAGMA table_info(facturas)")}
        if "nombre_otros_impuestos" not in cols:
            con.execute("ALTER TABLE facturas ADD COLUMN nombre_otros_impuestos TEXT")

    @staticmethod
    def _migrar(con: sqlite3.Connection) -> None:
        """v1 -> v2: con_cheque pasa a metodo_pago; el antiguo estado «contado» pasa a pagada + contado."""
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='facturas'").fetchone():
            return
        if con.execute("PRAGMA user_version").fetchone()[0] >= VERSION_ESQUEMA:
            return
        cols = {r[1] for r in con.execute("PRAGMA table_info(facturas)")}
        if "metodo_pago" not in cols:
            con.execute("ALTER TABLE facturas ADD COLUMN metodo_pago TEXT")
        if "con_cheque" in cols:
            con.execute("UPDATE facturas SET metodo_pago='cheque' WHERE estado_pago='pagada' AND con_cheque=1")
            con.execute("UPDATE facturas SET metodo_pago='transferencia' "
                        "WHERE estado_pago='pagada' AND metodo_pago IS NULL")
            con.execute("ALTER TABLE facturas DROP COLUMN con_cheque")
        con.execute("UPDATE facturas SET metodo_pago='contado', estado_pago='pagada', "
                    "fecha_pago=COALESCE(fecha_pago, substr(creada_en,1,10)) WHERE estado_pago='contado'")

    # ------------------------------------------------------------ infraestructura
    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._candado:
            con = self._conectar()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()

    def _leer(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        con = self._conectar()
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    # ------------------------------------------------------------------ facturas
    def crear_factura(self, factura_id: str, imagenes: list[str], ahora: datetime) -> None:
        with self._tx() as con:
            con.execute("INSERT OR IGNORE INTO facturas (id, estado, creada_en, imagenes) VALUES (?,?,?,?)",
                        (factura_id, "registrada", ahora.isoformat(timespec="seconds"), json.dumps(imagenes)))

    def actualizar_imagenes(self, factura_id: str, imagenes: list[str]) -> None:
        with self._tx() as con:
            con.execute("UPDATE facturas SET imagenes=? WHERE id=?", (json.dumps(imagenes), factura_id))

    def set_estado(self, factura_id: str, estado: str, mensaje: str | None = None) -> None:
        assert estado in ESTADOS_FACTURA, estado
        with self._tx() as con:
            con.execute("UPDATE facturas SET estado=?, mensaje=? WHERE id=?", (estado, mensaje, factura_id))

    def guardar_borrador(self, factura_id: str, campos: dict, hallazgos: list[dict],
                         estado: str = "en_revision") -> None:
        """Espejo de la pausa para la interfaz (en_revision o por_confirmar). NO es una factura guardada:
        sin líneas, sin seguimiento, sin Excel y sin restricción de duplicado hasta que el usuario confirme."""
        assert estado in ("en_revision", "por_confirmar")
        with self._tx() as con:
            con.execute("UPDATE facturas SET estado=?, extraido=?, hallazgos=?, mensaje=NULL WHERE id=?",
                        (estado, json.dumps(campos), json.dumps(hallazgos), factura_id))

    def obtener(self, factura_id: str) -> dict | None:
        filas = self._leer("SELECT * FROM facturas WHERE id=?", (factura_id,))
        return self._factura_dict(filas[0]) if filas else None

    @staticmethod
    def _factura_dict(fila: sqlite3.Row) -> dict:
        d = dict(fila)
        for k in ("imagenes", "extraido", "hallazgos"):
            d[k] = json.loads(d[k]) if d.get(k) else ([] if k != "extraido" else None)
        return d

    def listar_por_estado(self, *estados: str) -> list[dict]:
        marcas = ",".join("?" * len(estados))
        return [self._factura_dict(f) for f in
                self._leer(f"SELECT * FROM facturas WHERE estado IN ({marcas}) ORDER BY creada_en", estados)]

    def existe_guardada(self, rut_emisor: str, folio: str, tipo: str) -> bool:
        return bool(self._leer(
            "SELECT 1 FROM facturas WHERE estado='guardada' AND rut_emisor=? AND folio=? AND tipo_documento=?",
            (rut_emisor, folio, tipo)))

    def guardar_factura(self, factura_id: str, campos: dict, hallazgos: list[dict], hoy: date,
                        aprobada_manual: bool = False, pago: dict | None = None) -> dict:
        """UNA transacción: factura, líneas, pago y seguimiento (si corresponde). Duplicado -> Duplicada.

        `pago` es la configuración ya validada (pago.validar_configuracion). Sin ella (solo pruebas y
        evaluación) se usa la configuración por defecto; en la aplicación siempre decide el usuario.
        """
        pago = pago or por_defecto(campos, hoy)
        emision = parse_fecha(campos["fecha_emision"])
        mes = f"{emision:%Y-%m}"
        tipo = campos["tipo_documento"]
        pagada = pago["estado_pago"] == "pagada"
        crea = bool(pago.get("fecha_alerta")) and pago["estado_pago"] == "pendiente"
        try:
            with self._tx() as con:
                con.execute(
                    """UPDATE facturas SET estado='guardada', mensaje=NULL, extraido=NULL, hallazgos=?,
                       tipo_documento=?, rut_emisor=?, razon_social_emisor=?, rut_receptor=?, razon_social_receptor=?,
                       folio=?, fecha_emision=?, fecha_vencimiento=?, medio_pago=?, monto_neto=?, monto_exento=?,
                       iva=?, otros_impuestos=?, nombre_otros_impuestos=?, total=?, observaciones=?, aprobada_manual=?, estado_validacion=?,
                       mes_emision=?, estado_pago=?, metodo_pago=?, nro_documento=?, fecha_pago=? WHERE id=?""",
                    (json.dumps(hallazgos), tipo, campos["rut_emisor"], campos["razon_social_emisor"],
                     campos["rut_receptor"], campos["razon_social_receptor"], campos["folio"],
                     campos["fecha_emision"], campos["fecha_vencimiento"], campos["medio_pago"],
                     campos["monto_neto"], campos["monto_exento"], campos["iva"], campos["otros_impuestos"],
                     campos.get("nombre_otros_impuestos"), campos["total"], campos["observaciones"], int(aprobada_manual),
                     estado_validacion_texto(hallazgos, aprobada_manual), mes, pago["estado_pago"],
                     pago["metodo_pago"], pago["nro_documento"], hoy.isoformat() if pagada else None, factura_id))
                con.execute("DELETE FROM lineas WHERE factura_id=?", (factura_id,))
                for n, ln in enumerate(campos["lineas"], start=1):
                    con.execute("INSERT INTO lineas (factura_id, n, cantidad, detalle, precio_unitario, descuento,"
                                " total_linea) VALUES (?,?,?,?,?,?,?)",
                                (factura_id, n, ln["cantidad"], ln["detalle"], ln["precio_unitario"],
                                 ln["descuento"], ln["total_linea"]))
                if crea:   # la fecha de vencimiento queda intacta; solo se programa el recordatorio
                    con.execute("INSERT OR IGNORE INTO seguimientos (factura_id, estado, proxima_alerta)"
                                " VALUES (?, 'pendiente', ?)", (factura_id, pago["fecha_alerta"]))
        except sqlite3.IntegrityError as e:
            if "ux_factura_identidad" in str(e) or "UNIQUE" in str(e):
                raise Duplicada(str(campos.get("folio"))) from e
            raise
        return {"mes": mes, "seguimiento": crea}

    def lineas_de(self, factura_id: str) -> list[dict]:
        return [dict(f) for f in self._leer("SELECT * FROM lineas WHERE factura_id=? ORDER BY n", (factura_id,))]

    def listar_guardadas(self, mes: str | None = None, estado_pago: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM facturas WHERE estado='guardada'", []
        if mes:
            sql += " AND mes_emision=?"
            params.append(mes)
        if estado_pago:
            sql += " AND estado_pago=?"
            params.append(estado_pago)
        sql += " ORDER BY fecha_emision DESC, folio"
        return [self._factura_dict(f) for f in self._leer(sql, tuple(params))]

    # --------------------------------------------------------------------- pagos
    def registrar_pago(self, factura_id: str, hoy: date, metodo: str, nro_documento: str | None) -> str:
        """Una transacción: marca la factura pagada con su método y cierra su seguimiento. Devuelve el mes.

        Cheque exige N° de documento; contado y transferencia no lo piden. El vencimiento no se toca.
        """
        if metodo not in METODOS_PAGO:
            raise DecisionInvalida("Elija el método de pago: contado, transferencia o cheque.")
        nro = (nro_documento or "").strip() or None
        if metodo == "cheque" and not nro:
            raise DecisionInvalida("Para un pago con cheque hay que indicar el N° de documento.")
        with self._tx() as con:
            fila = con.execute("SELECT mes_emision, estado, estado_pago FROM facturas WHERE id=?",
                               (factura_id,)).fetchone()
            if fila is None or fila["estado"] != "guardada":
                raise DecisionInvalida("La factura no existe o no está guardada.")
            if fila["estado_pago"] != "pendiente":
                raise DecisionInvalida("Esta factura no tiene un pago pendiente.")
            con.execute("UPDATE facturas SET estado_pago='pagada', metodo_pago=?, fecha_pago=?, nro_documento=?"
                        " WHERE id=?", (metodo, hoy.isoformat(), nro if metodo == "cheque" else None, factura_id))
            con.execute("UPDATE seguimientos SET estado='pagada', hilo=NULL WHERE factura_id=?", (factura_id,))
            return fila["mes_emision"]

    def corregir_pago(self, factura_id: str, hoy: date, ahora: datetime, estado: str, metodo: str | None = None,
                      nro_documento: str | None = None, fecha_pago: str | date | None = None,
                      fecha_vencimiento: str | date | None = None) -> dict:
        """Corrige el estado de pago de una factura YA guardada, todo en UNA transacción (cambio de estado, seguimiento
        y registro de la corrección). Devuelve {mes, cambio, estado, seguimiento}. Errores de datos -> DecisionInvalida.

        · pendiente -> pagada: método (+ N° si cheque) y fecha de pago (por defecto hoy); el seguimiento se cierra.
        · pagada -> pendiente: se borran método, N° y fecha de pago; se recrea el seguimiento con la regla vigente
          (vencimiento − 3 días corridos, o hoy si ya pasó). Sin vencimiento hay que entregarlo (`fecha_vencimiento`).
        · pagada -> pagada con otro método: si deja de ser cheque se limpia el N° de documento.
        Solo las facturas tienen estado de pago y seguimiento (no notas de crédito/débito ni guías).
        """
        if estado not in ("pendiente", "pagada"):
            raise DecisionInvalida("El estado debe ser pendiente o pagada.")
        nro = (nro_documento or "").strip() or None
        try:
            f_pago = parse_fecha(fecha_pago) if fecha_pago not in (None, "") else hoy
            f_venc_nueva = parse_fecha(fecha_vencimiento) if fecha_vencimiento not in (None, "") else None
        except DatosInvalidos as e:
            raise DecisionInvalida(str(e)) from e
        if estado == "pagada":
            if metodo not in METODOS_PAGO:
                raise DecisionInvalida("Elija el método de pago: contado, transferencia o cheque.")
            if metodo == "cheque" and not nro:
                raise DecisionInvalida("Para un pago con cheque hay que indicar el N° de documento.")
            if f_pago > hoy:
                raise DecisionInvalida("La fecha de pago no puede ser posterior a hoy.")
        with self._tx() as con:
            f = con.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
            if f is None or f["estado"] != "guardada":
                raise DecisionInvalida("La factura no existe o no está guardada.")
            if f["estado_pago"] not in ("pendiente", "pagada"):
                raise DecisionInvalida("Este documento no tiene estado de pago (solo las facturas lo tienen).")
            venc = f["fecha_vencimiento"]
            if estado == "pendiente":
                if not venc and f_venc_nueva is None:
                    raise DecisionInvalida("Para dejarla pendiente hace falta la fecha de vencimiento: indíquela.")
                if not venc:
                    emision = parse_fecha(f["fecha_emision"])
                    if emision and f_venc_nueva < emision:
                        raise DecisionInvalida("El vencimiento no puede ser anterior a la emisión.")
                    venc = f_venc_nueva.isoformat()
            antes = {"estado_pago": f["estado_pago"], "metodo_pago": f["metodo_pago"], "nro_documento": f["nro_documento"],
                     "fecha_pago": f["fecha_pago"], "fecha_vencimiento": f["fecha_vencimiento"]}
            if estado == "pagada":
                despues = {"estado_pago": "pagada", "metodo_pago": metodo,
                           "nro_documento": nro if metodo == "cheque" else None, "fecha_pago": f_pago.isoformat(),
                           "fecha_vencimiento": f["fecha_vencimiento"]}
            else:
                despues = {"estado_pago": "pendiente", "metodo_pago": None, "nro_documento": None, "fecha_pago": None,
                           "fecha_vencimiento": venc}
            mes = f["mes_emision"]
            if despues == antes:
                return {"mes": mes, "cambio": False, "estado": estado, "seguimiento": None}
            con.execute("UPDATE facturas SET estado_pago=?, metodo_pago=?, nro_documento=?, fecha_pago=?, "
                        "fecha_vencimiento=? WHERE id=?",
                        (despues["estado_pago"], despues["metodo_pago"], despues["nro_documento"],
                         despues["fecha_pago"], despues["fecha_vencimiento"], factura_id))
            seg = None
            fila = con.execute("SELECT * FROM seguimientos WHERE factura_id=?", (factura_id,)).fetchone()
            if estado == "pagada":
                if fila is not None:   # se cierra (también si estaba «esperando decisión» en una ventana abierta)
                    con.execute("UPDATE seguimientos SET estado='pagada', hilo=NULL WHERE factura_id=?", (factura_id,))
                seg = "cerrado" if fila is not None else None
            elif f["tipo_documento"] in TIPOS_CON_ALERTA:
                alerta = alerta_por_defecto(venc, hoy).isoformat()
                if fila is None:
                    con.execute("INSERT INTO seguimientos (factura_id, estado, proxima_alerta) VALUES (?, 'pendiente', ?)",
                                (factura_id, alerta))
                elif fila["estado"] == "pagada":       # se reutiliza la misma fila: nunca hay dos seguimientos
                    con.execute("UPDATE seguimientos SET estado='pendiente', proxima_alerta=?, hilo=NULL "
                                "WHERE factura_id=?", (alerta, factura_id))
                seg = "recreado"
            self._registrar_correccion(con, factura_id, ahora, antes, despues)
            return {"mes": mes, "cambio": True, "estado": estado, "seguimiento": seg}

    @staticmethod
    def _registrar_correccion(con: sqlite3.Connection, factura_id: str, ahora: datetime, antes: dict, despues: dict) -> None:
        con.execute("INSERT INTO correcciones_pago (factura_id, cuando, anterior, nuevo) VALUES (?,?,?,?)",
                    (factura_id, ahora.isoformat(timespec="seconds"), json.dumps(antes), json.dumps(despues)))

    def correcciones_de(self, factura_id: str) -> list[dict]:
        out = []
        for f in self._leer("SELECT * FROM correcciones_pago WHERE factura_id=? ORDER BY id", (factura_id,)):
            d = dict(f)
            d["anterior"], d["nuevo"] = json.loads(d["anterior"]), json.loads(d["nuevo"])
            out.append(d)
        return out

    # -------------------------------------------------------------- seguimientos
    def seguimientos_a_alertar(self, hoy: date) -> list[dict]:
        return [dict(f) for f in self._leer(
            "SELECT * FROM seguimientos WHERE estado='pendiente' AND proxima_alerta<=? ORDER BY proxima_alerta, id",
            (hoy.isoformat(),))]

    def seguimientos_esperando(self) -> list[dict]:
        return [dict(f) for f in self._leer(
            """SELECT s.*, f.rut_emisor, f.razon_social_emisor, f.folio, f.total, f.fecha_vencimiento,
                      f.fecha_emision, f.imagenes
               FROM seguimientos s JOIN facturas f ON f.id = s.factura_id
               WHERE s.estado='esperando_decision' ORDER BY f.fecha_vencimiento, s.id""")]

    def obtener_seguimiento(self, seg_id: int) -> dict | None:
        filas = self._leer("SELECT * FROM seguimientos WHERE id=?", (seg_id,))
        return dict(filas[0]) if filas else None

    def marcar_esperando(self, seg_id: int, hilo: str) -> None:
        """Idempotente: solo cambia si todavía no está pagada."""
        with self._tx() as con:
            con.execute("UPDATE seguimientos SET estado='esperando_decision', hilo=? WHERE id=? AND estado!='pagada'",
                        (hilo, seg_id))

    def reprogramar(self, seg_id: int, fecha: date) -> None:
        with self._tx() as con:
            con.execute("UPDATE seguimientos SET estado='pendiente', proxima_alerta=?, hilo=NULL "
                        "WHERE id=? AND estado!='pagada'", (fecha.isoformat(), seg_id))

    def liberar_esperando_sin_hilo(self, hilos_vivos: set[str], hoy: date) -> int:
        """Si checkpoints.db se perdió, devuelve a «pendiente» lo que esperaba una pausa que ya no existe."""
        n = 0
        for s in self.seguimientos_esperando():
            if s["hilo"] not in hilos_vivos:
                self.reprogramar(s["id"], hoy)
                n += 1
        return n

    # --------------------------------------------------------------------- Excel
    def filas_excel(self, mes: str) -> list[dict]:
        """Una fila por línea, con los datos de la factura repetidos."""
        filas = []
        for f in self.listar_guardadas(mes=mes):
            lineas = self.lineas_de(f["id"]) or [None]
            for ln in lineas:
                filas.append({"factura": f, "linea": ln})
        filas.sort(key=lambda r: (r["factura"]["fecha_emision"] or "", r["factura"]["folio"] or ""))
        return filas

    def marcar_mes_desactualizado(self, mes: str) -> None:
        with self._tx() as con:
            con.execute("INSERT INTO excel_meses (mes, desactualizado) VALUES (?,1) "
                        "ON CONFLICT(mes) DO UPDATE SET desactualizado=1", (mes,))

    def marcar_mes_actualizado(self, mes: str, ahora: datetime) -> None:
        with self._tx() as con:
            con.execute("INSERT INTO excel_meses (mes, desactualizado, actualizado_en) VALUES (?,0,?) "
                        "ON CONFLICT(mes) DO UPDATE SET desactualizado=0, actualizado_en=excluded.actualizado_en",
                        (mes, ahora.isoformat(timespec="seconds")))

    def meses_desactualizados(self) -> list[str]:
        return [f["mes"] for f in self._leer("SELECT mes FROM excel_meses WHERE desactualizado=1 ORDER BY mes")]

    # ------------------------------------------------------------------- ajustes
    def ajuste(self, clave: str, por_defecto: str | None = None) -> str | None:
        filas = self._leer("SELECT valor FROM ajustes WHERE clave=?", (clave,))
        return filas[0]["valor"] if filas else por_defecto

    def set_ajuste(self, clave: str, valor: str) -> None:
        with self._tx() as con:
            con.execute("INSERT INTO ajustes (clave, valor) VALUES (?,?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, valor))
