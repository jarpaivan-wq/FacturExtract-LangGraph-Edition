"""Extractor: interfaz con dos implementaciones (real con visión y falsa para pruebas).

El modelo SOLO extrae (salida estructurada, sin herramientas). La imagen se lee del disco
dentro de la implementación real y se envía únicamente en la llamada de extracción.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, Callable, Protocol

from . import config
from .modelo import TIPOS_DOCUMENTO, normalizar_campos

MAX_LADO_PX = 2000


class ErrorExtractor(Exception):
    """Error con mensaje apto para el usuario (lenguaje normal)."""

    def __init__(self, mensaje: str):
        super().__init__(mensaje)
        self.mensaje = mensaje


class ErrorTransitorio(ErrorExtractor):
    """Red o API momentáneamente caída: se reintenta con espera (política de LangGraph)."""


class ErrorPermanente(ErrorExtractor):
    """Clave inválida, imagen ilegible, etc.: no se reintenta."""


class Extractor(Protocol):
    def extraer(self, rutas: list[str], hallazgos_previos: list[dict] | None = None,
                campos_previos: dict | None = None) -> dict: ...


# -------------------------------------------------------------------- falso ----
class ExtractorFalso:
    """Devuelve respuestas predefinidas en orden. Un elemento puede ser un dict (campos crudos),
    una Exception (se lanza) o un callable(rutas, hallazgos_previos) -> dict."""

    def __init__(self, respuestas: list[Any]):
        self.respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    def extraer(self, rutas, hallazgos_previos=None, campos_previos=None):
        self.llamadas.append({"rutas": list(rutas), "hallazgos_previos": hallazgos_previos,
                              "campos_previos": campos_previos})
        if not self.respuestas:
            raise ErrorPermanente("El extractor falso no tiene más respuestas.")
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 or not callable(self.respuestas[0]) else self.respuestas[0]
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(rutas, hallazgos_previos)
        return normalizar_campos(r)


# --------------------------------------------------------------------- real ----
def _anulable(tipo: str) -> dict:
    return {"type": [tipo, "null"]}


ESQUEMA_EXTRACCION: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tipo_documento": {"type": "string", "enum": list(TIPOS_DOCUMENTO)},
        "folio": _anulable("string"),
        "rut_emisor": _anulable("string"),
        "razon_social_emisor": _anulable("string"),
        "rut_receptor": _anulable("string"),
        "razon_social_receptor": _anulable("string"),
        "fecha_emision": _anulable("string"),
        "fecha_vencimiento": _anulable("string"),
        "medio_pago": _anulable("string"),
        "pago_sugerido": {"type": "string", "enum": ["pendiente", "contado", "transferencia", "cheque", "no_claro"]},
        "monto_neto": _anulable("integer"),
        "monto_exento": _anulable("integer"),
        "iva": _anulable("integer"),
        "otros_impuestos": _anulable("integer"),
        "nombre_otros_impuestos": _anulable("string"),
        "total": _anulable("integer"),
        "observaciones": _anulable("string"),
        "lineas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cantidad": {"type": "number"},
                    "detalle": {"type": "string"},
                    "precio_unitario": {"type": "number"},
                    "descuento": {"type": "integer"},
                    "total_linea": {"type": "integer"},
                },
                "required": ["cantidad", "detalle", "precio_unitario", "descuento", "total_linea"],
            },
        },
    },
    "required": ["tipo_documento", "folio", "rut_emisor", "razon_social_emisor", "rut_receptor",
                 "razon_social_receptor", "fecha_emision", "fecha_vencimiento", "medio_pago", "pago_sugerido", "monto_neto",
                 "monto_exento", "iva", "otros_impuestos", "nombre_otros_impuestos", "total", "observaciones",
                 "lineas"],
}

PROMPT_SISTEMA = """Eres un extractor de datos de documentos tributarios electrónicos chilenos (formato SII).
Recibes una o dos fotos del MISMO documento (por ejemplo frente y reverso) y devuelves sus datos en el formato pedido.

Reglas:
- REGLA DE ORO: transcribe exactamente lo que está impreso. Lo que no está impreso se devuelve nulo (o 0 en un
  monto): nunca inventes, deduzcas, calcules ni ajustes un valor para que las cifras cuadren.
- Si los montos impresos no cuadran entre sí (neto + IVA ≠ total), devuélvelos tal como están impresos y explica
  la diferencia en observaciones. Jamás pongas la diferencia en otros_impuestos ni en otra partida.
- tipo_documento: factura (afecta), factura_exenta, nota_credito, nota_debito, guia_despacho u otro.
- RUT con puntos y guion tal como aparece (ej. 76.123.456-7). Fechas en formato AAAA-MM-DD.
- Montos en pesos chilenos como números enteros, sin puntos ni símbolos. monto_neto, monto_exento, iva,
  otros_impuestos (suma de impuestos adicionales que figuren como línea propia en el documento, p. ej. específicos)
  y total. Si no hay un impuesto adicional impreso, otros_impuestos es 0 y nombre_otros_impuestos es nulo.
- nombre_otros_impuestos: el nombre del impuesto adicional tal como está impreso (p. ej. «Impuesto específico
  combustibles»); nulo si no hay ninguno impreso. Nunca pongas un nombre inventado o genérico para justificar un monto.
- medio_pago: el texto tal como aparece (contado, cheque, crédito, etc.); nulo si no aparece.
- fecha_vencimiento: solo si aparece; nulo en caso contrario.
- pago_sugerido: SOLO una sugerencia que luego confirma una persona. «contado», «transferencia» o «cheque» si el
  documento indica claramente esa forma de pago; «pendiente» si indica crédito o plazo; «no_claro» si no está claro.
- lineas: UNA entrada por cada línea de detalle del documento (no resumas ni agrupes), con cantidad,
  detalle, precio_unitario, descuento (monto en pesos, 0 si no hay) y total_linea (si un número de la línea no se lee, 0).
- No guardas ni ejecutas nada: solo devuelves los datos."""


def preparar_imagen(ruta: str | Path) -> tuple[str, str]:
    """Lee la imagen del disco; la reduce si es muy grande. Devuelve (base64, media_type)."""
    from PIL import Image, ImageOps

    try:
        with Image.open(ruta) as im:
            im = ImageOps.exif_transpose(im)
            im.load()
            if max(im.size) > MAX_LADO_PX:
                im.thumbnail((MAX_LADO_PX, MAX_LADO_PX))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=90)
    except Exception as e:
        raise ErrorPermanente(f"No se pudo abrir la foto «{Path(ruta).name}». ¿Es una imagen válida?") from e
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def mensaje_retroalimentacion(hallazgos: list[dict], campos_previos: dict | None) -> str:
    lista = "\n".join(f"- {h['mensaje']}" for h in hallazgos)
    texto = ("Una extracción anterior de este mismo documento no pasó la validación automática:\n"
             f"{lista}\n")
    if campos_previos is not None:
        texto += f"\nResultado anterior:\n{json.dumps(campos_previos, ensure_ascii=False)}\n"
    texto += ("\nVuelve a leer la imagen con atención y corrige solo lo que realmente hayas leído mal. "
              "Si, tras releerla, los montos impresos siguen sin cuadrar, devuelve EXACTAMENTE lo que está impreso "
              "(aunque no sume) y explica la diferencia en observaciones. NO agregues, inventes ni ajustes partidas "
              "(en especial otros_impuestos) para que las cifras cuadren: lo que no está impreso va nulo o en 0. "
              "Devuelve el documento completo.")
    return texto


class ExtractorClaude:
    def __init__(self, cliente: Any = None, modelo: str | None = None):
        self._cliente = cliente
        self.modelo = modelo or config.modelo()
        self.ultimo_uso = None   # tokens de la última llamada (para medir costo en evaluación)

    def _cliente_api(self):
        if self._cliente is None:
            import anthropic

            config.usar_almacen_windows()
            self._cliente = anthropic.Anthropic(max_retries=0, timeout=120.0)  # los reintentos los hace LangGraph
        return self._cliente

    def extraer(self, rutas, hallazgos_previos=None, campos_previos=None):
        import anthropic

        contenido: list[dict] = []
        for ruta in rutas:
            datos, tipo = preparar_imagen(ruta)
            contenido.append({"type": "image", "source": {"type": "base64", "media_type": tipo, "data": datos}})
        contenido.append({"type": "text", "text": (
            "Extrae los datos del documento." if not hallazgos_previos
            else mensaje_retroalimentacion(hallazgos_previos, campos_previos))})
        try:
            resp = self._cliente_api().messages.create(
                model=self.modelo, max_tokens=8000, system=PROMPT_SISTEMA,
                messages=[{"role": "user", "content": contenido}],
                output_config={"format": {"type": "json_schema", "schema": ESQUEMA_EXTRACCION}},
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ErrorPermanente("La clave de API no es válida o no tiene permiso. Revise la clave en el archivo "
                                  ".env de la carpeta de datos.") from e
        except anthropic.BadRequestError as e:
            raise ErrorPermanente("El servicio rechazó la solicitud (¿foto demasiado grande o dañada?).") from e
        except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError) as e:
            raise ErrorTransitorio("No hay conexión con el servicio de lectura o está saturado.") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 or e.status_code == 529:
                raise ErrorTransitorio("El servicio de lectura tuvo un problema momentáneo.") from e
            raise ErrorPermanente(f"El servicio de lectura respondió con un error ({e.status_code}).") from e
        self.ultimo_uso = getattr(resp, "usage", None)
        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            return normalizar_campos(json.loads(texto))
        except (json.JSONDecodeError, ValueError) as e:
            raise ErrorTransitorio("La lectura de la factura salió incompleta; se reintentará.") from e


def probar_conexion(cliente: Any = None, modelo: str | None = None) -> tuple[bool, str]:
    """Verifica internet + clave de API con una llamada mínima. Mensaje en lenguaje normal."""
    import anthropic

    if not __import__("os").environ.get("ANTHROPIC_API_KEY"):
        return False, "No se encontró la clave de API. Revise el archivo .env de la carpeta de datos."
    try:
        config.usar_almacen_windows()
        c = cliente or anthropic.Anthropic(max_retries=1, timeout=30.0)
        c.messages.create(model=modelo or config.modelo(), max_tokens=8,
                          messages=[{"role": "user", "content": "Responde solo: ok"}])
        return True, "Conexión correcta: hay internet y la clave de API funciona."
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return False, "La clave de API no es válida o no tiene permiso."
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        return False, "No se pudo conectar a internet o al servicio. Revise la red del equipo."
    except anthropic.NotFoundError:
        return False, "La clave funciona, pero el modelo configurado no existe. Avise al soporte."
    except anthropic.RateLimitError:
        return True, "La clave funciona, pero el servicio está saturado en este momento."
    except anthropic.APIError as e:
        return False, f"El servicio respondió con un problema ({getattr(e, 'status_code', 'sin código')})."
