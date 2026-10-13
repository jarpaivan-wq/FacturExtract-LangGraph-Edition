"""Evaluación con el modelo real. SOLO desarrollo y SOLO facturas sintéticas.

    .venv\\Scripts\\python -m evaluacion.ejecutar --n 10
    .venv\\Scripts\\python -m evaluacion.ejecutar --n 10 --langsmith     (requiere LANGSMITH_API_KEY)

Sin --langsmith nada sale de este equipo salvo las llamadas al modelo. LangSmith queda apagado salvo
que se pida explícitamente aquí; nunca se activa en la aplicación del cliente.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

from facturextract import config
from facturextract.extractor import ExtractorClaude
from facturextract.validacion import bloqueantes, validar_todo

from . import evaluadores, sinteticas

SALIDA = Path(__file__).resolve().parent / "salida"
NOMBRE_DATASET = "facturextract-sinteticas"
HOY_EVALUACION = date(2026, 10, 12)


def extraer_semilla(extractor: ExtractorClaude, semilla: int) -> tuple[dict, dict, float]:
    esperado, imagen = sinteticas.generar(semilla)
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / f"sintetica_{semilla}.jpg"
    ruta.write_bytes(sinteticas.a_jpeg(imagen))
    t0 = time.perf_counter()
    obtenido = extractor.extraer([str(ruta)])
    return esperado, obtenido, time.perf_counter() - t0


def correr_local(n: int, inicio: int) -> dict:
    extractor = ExtractorClaude()
    filas, puntajes, aprobadas, tokens_in, tokens_out, latencias = [], [], 0, 0, 0, []
    for semilla in range(inicio, inicio + n):
        esperado, obtenido, seg = extraer_semilla(extractor, semilla)
        p = evaluadores.evaluar(esperado, obtenido)
        hall = validar_todo(obtenido, HOY_EVALUACION, lambda *a: False)
        sin_reintento = not bloqueantes(hall)
        aprobadas += sin_reintento
        uso = extractor.ultimo_uso
        tokens_in += getattr(uso, "input_tokens", 0) or 0
        tokens_out += getattr(uso, "output_tokens", 0) or 0
        latencias.append(seg)
        puntajes.append(p)
        filas.append({"semilla": semilla, "segundos": round(seg, 1), "aprobada_sin_reintento": sin_reintento,
                      "campos_exactos_ok": evaluadores.campos_exactos_correctos(p),
                      "fallos": [k for k, v in p.items() if v < (1.0 if k.startswith(("exacto", "cantidad")) else
                                                                 evaluadores.UMBRAL_APROXIMADO)]})
    res = {
        "modelo": config.modelo(), "n": n,
        "promedios": evaluadores.resumen(puntajes),
        "tasa_aprobacion_sin_reintento": aprobadas / n,
        "tasa_revision_humana_estimada": 1 - aprobadas / n,   # lo que no pasa V01-V07 irá a reintento/revisión
        "tokens_entrada_total": tokens_in, "tokens_salida_total": tokens_out,
        "latencia_media_s": sum(latencias) / n, "detalle": filas,
    }
    (SALIDA / "resultados.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def correr_langsmith(n: int, inicio: int) -> None:
    """Dataset sintético + experimento con evaluadores por campo. El dataset guarda solo la semilla y los
    campos esperados (la imagen se regenera de forma determinista): no se sube ninguna imagen ni dato real."""
    if not os.environ.get("LANGSMITH_API_KEY"):
        raise SystemExit("Falta LANGSMITH_API_KEY en el entorno (solo desarrollo).")
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    from langsmith import Client, evaluate

    cliente = Client()
    if not cliente.has_dataset(dataset_name=NOMBRE_DATASET):
        ds = cliente.create_dataset(NOMBRE_DATASET, description="Facturas sintéticas (datos inventados)")
        cliente.create_examples(
            dataset_id=ds.id,
            examples=[{"inputs": {"semilla": s}, "outputs": {"campos": sinteticas.datos_esperados(s)}}
                      for s in range(1, 31)])
    extractor = ExtractorClaude()

    def objetivo(entrada: dict) -> dict:
        _, obtenido, _ = extraer_semilla(extractor, entrada["semilla"])
        return {"campos": obtenido}

    def hacer(clave: str):
        def ev(outputs: dict, reference_outputs: dict) -> dict:
            return {"key": clave, "score": evaluadores.evaluar(reference_outputs["campos"], outputs["campos"])[clave]}
        ev.__name__ = clave
        return ev

    claves = list(evaluadores.evaluar(sinteticas.datos_esperados(1), sinteticas.datos_esperados(1)))
    evaluate(objetivo, data=cliente.list_examples(dataset_name=NOMBRE_DATASET, limit=n, offset=inicio - 1),
             evaluators=[hacer(k) for k in claves], experiment_prefix="extraccion", metadata={"modelo": config.modelo()})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--inicio", type=int, default=1, help="primera semilla")
    ap.add_argument("--langsmith", action="store_true")
    a = ap.parse_args()
    config.cargar_env()
    if a.langsmith:
        correr_langsmith(a.n, a.inicio)
        return
    r = correr_local(a.n, a.inicio)
    print(f"Modelo {r['modelo']} · {r['n']} facturas sintéticas")
    for k, v in r["promedios"].items():
        print(f"  {k:28s} {v:5.2f}")
    print(f"Aprobadas sin reintento: {r['tasa_aprobacion_sin_reintento']:.0%} · "
          f"latencia media {r['latencia_media_s']:.1f}s · tokens {r['tokens_entrada_total']}+{r['tokens_salida_total']}")
    for f in r["detalle"]:
        if f["fallos"]:
            print(f"  semilla {f['semilla']}: fallos en {', '.join(f['fallos'])}")


if __name__ == "__main__":
    main()
