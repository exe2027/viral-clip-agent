"""Aprendizaje: del flujo de métricas (9.1) a priority.recalibrate (6B.7).

Contrato que 9.1 debe cumplir al escribir las métricas:
  SQLite ``metrics.db`` (gitignored), tabla ``clip_metrics`` con
  ``clip`` (ruta del clip), ``platform``, ``views_7d`` (vistas a
  7 días) y ``measured_at``. La viralidad, el SEO y el chat de cada
  clip salen de su manifiesto (no se duplican en la tabla).

Comportamiento actual (BETA): pesos fijos 0,5/0,3/0,2 hasta tener
30 clips publicados con datos a 7 días; después, ``recalibrate``
ajusta SOLO los exponentes, máximo ±25% por ciclo, con registro de
fecha y motivo. Nunca toca los filtros duros.

Uso: ``clipagent recalibrate [--db metrics.db]``.
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_DB = os.path.join(BASE_DIR, "metrics.db")


def collect_records(db_path=None, base_dir=None):
    """Registros para recalibrate desde metrics.db + manifiestos.

    Devuelve [{virality, seo, chat_multiple, views}] por clip con
    vistas a 7 días. Sin base de datos (o sin filas), lista vacía:
    el sistema sigue en BETA con los pesos fijos.
    """
    from .publish import load_clip_package
    path = db_path or METRICS_DB
    if not os.path.isfile(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT clip, platform, views_7d FROM clip_metrics "
            "WHERE views_7d IS NOT NULL AND views_7d > 0").fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    records = []
    for clip_path, platform, views in rows:
        clip, _manifest = load_clip_package(clip_path)
        if clip is None:
            continue
        seo_entry = (clip.get("seo_scores") or {}).get(platform) or {}
        seo = seo_entry.get("score") if isinstance(seo_entry, dict) else seo_entry
        virality = (clip.get("viral_score") or {}).get("total")
        chat = (clip.get("viral") or {}).get("viral_multiple")
        if not virality or not seo or not chat:
            continue
        records.append({"virality": virality, "seo": seo,
                        "chat_multiple": chat, "views": views})
    return records


def run_recalibrate(db_path=None, weights_path=None):
    """Recalibra con las métricas disponibles y guarda el resultado.

    Devuelve el informe de priority.recalibrate (con status
    "insuficiente" y motivo BETA si hay menos de 30 clips).
    """
    from .priority import recalibrate_and_save
    records = collect_records(db_path)
    return recalibrate_and_save(records, path=weights_path)
