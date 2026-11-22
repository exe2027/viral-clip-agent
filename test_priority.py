"""Tests del algoritmo de prioridad de la Cola (aprobado 2026-11-16).

Fórmula POTENTE (media geométrica ponderada):
Prioridad = viralidad^0,5 x SEO^0,3 x chat_norm^0,2.
Referencia: Coker (78, 95, 13x) ~86; IlloJuan verificado (51, 84, 6,44x) ~61.
Aprendizaje: recalibración de pesos solo con 30+ clips a 7 días,
máx. ±25% por ciclo, con registro; filtros duros nunca cambian.
"""

import random

from clipagent.priority import (DEFAULT_WEIGHTS, MIN_CLIPS_LEARN, SEO_GATE,
                      VIRAL_MIN_PUBLISH, age_penalty, chat_normalized,
                      load_weights, moment_key, order_queue, priority,
                      publish_status, recalibrate, recalibrate_and_save,
                      should_auto_archive)


def test_chat_normalized_scale():
    assert chat_normalized(0) == 0.0
    assert chat_normalized(1.5) == 15.0
    assert chat_normalized(2.0) == 30.0  # salto de banda en 2x (base 3,0)
    assert chat_normalized(6.44) == 58.1  # IlloJuan verificado
    assert chat_normalized(13.0) == 93.0  # pico fuerte
    assert chat_normalized(100.0) == 100.0  # tope


def test_reference_coker():
    p = priority(78, 95, 13.0)
    assert p["priority"] == 85.7, p
    assert p["base"] == 85.7
    assert p["chat_normalized"] == 93.0
    assert p["formula"] == "geometric"


def test_reference_illojuan():
    p = priority(51, 84, 6.44)
    assert p["priority"] == 60.8, p
    assert p["chat_normalized"] == 58.1


def test_seo_multiplies_not_adds():
    # viralidad 90 / SEO 70 queda POR DEBAJO de viralidad 75 / SEO 98,
    # con el mismo chat. El SEO bajo arrastra el producto.
    a = priority(90, 70, 8.0)
    b = priority(75, 98, 8.0)
    assert a["priority"] == 80.1, a
    assert b["priority"] == 80.9, b
    assert a["priority"] < b["priority"]


def test_weights_sum_and_zero():
    p = priority(100, 100, 100.0)
    assert p["priority"] == 100.0
    p = priority(0, 0, 0)
    assert p["priority"] == 0.0
    p = priority(90, 95, 0)  # sin chat -> el factor 0 arrastra todo
    assert p["priority"] == 0.0


def test_age_penalty():
    assert age_penalty(0) == 0
    assert age_penalty(7) == 0
    assert age_penalty(8) == 10
    assert age_penalty(10) == 30
    p = priority(78, 95, 13.0, age_days=10)
    assert p["priority"] == 55.7, p
    assert p["age_penalty"] == 30
    assert p["age_days"] == 10


def test_publish_status():
    assert publish_status(78, 95)[0] == "publicable"
    assert publish_status(44, 95)[0] == "descartado"
    assert publish_status(45, 95)[0] == "publicable"  # borde incluido
    assert publish_status(51, 79)[0] == "bloqueado_seo"
    assert publish_status(51, 79, seo_override=True)[0] == "publicable_override"
    assert publish_status(51, 80)[0] == "publicable"  # puerta incluida


def test_hard_filters_never_change():
    assert VIRAL_MIN_PUBLISH == 45
    assert SEO_GATE == 80


def test_should_auto_archive():
    ok, _ = should_auto_archive(3, False, 62.0, 51)
    assert ok is True
    ok, _ = should_auto_archive(2, False, 62.0, 51)
    assert ok is False  # menos de 3 días
    ok, _ = should_auto_archive(5, False, 86.0, 78)
    assert ok is False  # prioridad alta, sigue en cola
    ok, _ = should_auto_archive(5, True, 62.0, 51)
    assert ok is False  # ya subido


def test_moment_key():
    assert moment_key("v1", 100) == moment_key("v1", 105)  # mismo momento
    assert moment_key("v1", 100) != moment_key("v1", 200)
    assert moment_key("v1", 100) != moment_key("v2", 100)


def _clip(**kw):
    c = {"id": 1, "title": "x", "vod_id": "v1", "start_sec": 100,
         "virality": 78, "seo": 95, "chat_multiple": 13.0,
         "created_at": "", "uploaded": False, "seo_override": False}
    c.update(kw)
    return c


def test_order_queue_by_priority_not_fifo():
    clips = [
        _clip(id=1, title="IlloJuan", virality=51, seo=79,
              chat_multiple=6.44, start_sec=3940, vod_id="v-illo"),
        _clip(id=2, title="Coker", virality=78, seo=95,
              chat_multiple=13.0, start_sec=12000, vod_id="v-coker"),
        _clip(id=3, title="Locones", virality=29, seo=60,
              chat_multiple=2.0, start_sec=11000, vod_id="v-loc"),
    ]
    out = order_queue(clips, "youtube_shorts")
    assert [c["title"] for c in out] == ["Coker", "IlloJuan", "Locones"]
    assert out[0]["priority"]["priority"] == 85.7
    assert out[1]["priority"]["priority"] == 59.7
    assert out[0]["queue_position"] == 1
    # Locones descartado por viralidad < 45, queda atrás con motivo.
    assert out[2]["status"] == "descartado"
    assert out[2]["left_behind"]
    # IlloJuan bloqueado por SEO < 80 sin override, queda atrás.
    assert out[1]["status"] == "bloqueado_seo"
    assert out[1]["left_behind"]


def test_order_queue_seo_per_platform():
    clip = _clip(seo={"youtube_shorts": 79, "tiktok": 96})
    yt = order_queue([clip], "youtube_shorts")[0]
    tt = order_queue([clip], "tiktok")[0]
    assert yt["priority"]["seo"] == 79
    assert tt["priority"]["seo"] == 96
    assert tt["priority"]["priority"] > yt["priority"]["priority"]
    assert yt["status"] == "bloqueado_seo"
    assert tt["status"] == "publicable"


def test_order_queue_same_moment_same_day():
    clips = [
        _clip(id=1, title="mejor", virality=78, seo=95,
              chat_multiple=13.0, start_sec=100),
        _clip(id=2, title="peor", virality=60, seo=92,
              chat_multiple=5.0, start_sec=104),  # mismo momento
    ]
    out = order_queue(clips, "youtube_shorts")
    assert out[0]["title"] == "mejor"
    assert out[1]["status"] == "duplicate_moment"
    assert out[1]["left_behind"]


def test_order_queue_breakdown_visible():
    out = order_queue([_clip()], "youtube_shorts")
    p = out[0]["priority"]
    assert set(p) >= {"priority", "base", "virality", "seo",
                      "chat_normalized", "age_penalty", "band",
                      "weights", "formula"}
    assert p["band"] == "urgente"
    assert p["weights"] == DEFAULT_WEIGHTS


# --- Aprendizaje -----------------------------------------------------------

def _records_viral_drives_views(n=40, seed=42):
    """Vistas dependen (casi) solo de la viralidad; SEO y chat son
    ruido independiente. La recalibración debe subir el peso viral."""
    rnd = random.Random(seed)
    recs = []
    for _ in range(n):
        v = rnd.uniform(50, 95)
        s = rnd.uniform(70, 100)
        c = rnd.uniform(30, 100)
        views = 100 * (v ** 2.0) * (1 + rnd.uniform(-0.1, 0.1))
        recs.append({"virality": round(v, 1), "seo": round(s, 1),
                     "chat_norm": round(c, 1), "views": round(views)})
    return recs


def test_recalibrate_needs_30():
    recs = _records_viral_drives_views(n=12)
    w, report = recalibrate(recs)
    assert w == DEFAULT_WEIGHTS
    assert report["status"] == "insuficiente"
    assert report["n"] == 12
    assert report["needed"] == MIN_CLIPS_LEARN


def test_recalibrate_learns_viral_weight():
    recs = _records_viral_drives_views(n=40)
    w, report = recalibrate(recs)
    assert report["status"] == "recalibrated"
    assert report["n"] == 40
    assert abs(sum(w.values()) - 1.0) < 0.01
    # El peso viral sube (las vistas dependen de él), sin pasar +25%.
    assert w["viral"] > DEFAULT_WEIGHTS["viral"]
    assert w["viral"] <= DEFAULT_WEIGHTS["viral"] * 1.25 + 0.01
    assert report["correlations"]["viral"] > 0.9
    assert "date" in report and "reason" in report


def test_recalibrate_and_save_registers():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = d + "/priority_weights.json"
        recs = _records_viral_drives_views(n=35)
        report = recalibrate_and_save(recs, path=path)
        assert report["status"] == "recalibrated"
        weights, history = load_weights(path)
        assert weights == report["weights"]
        assert len(history) == 1
        assert history[0]["n"] == 35
        # Con pocos clips no se guarda nada nuevo.
        report2 = recalibrate_and_save(
            _records_viral_drives_views(n=5), path=path)
        assert report2["status"] == "insuficiente"
        _, history2 = load_weights(path)
        assert len(history2) == 1
